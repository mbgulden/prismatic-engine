"""RF-3: Optional LLM deep-review stage (Phase 3).

ACTIVE enrichment, not a passive advisor. When enabled, structured LLM
findings are compiled into concrete repair work orders (what to change,
patch sketch, tests to add) and dispatched through the existing repair
flow (``ReviewQueue.dispatch_repair_task``), so the repair agent gets a
real work order instead of a vague note.

Authority rules (fail-closed, unchanged from the master plan):
- Default OFF. Enabled only via ``PRISMATIC_REVIEW_LLM=1`` or the
  dashboard toggle. A fresh install with zero models works fully.
- The LLM can NEVER downgrade a deterministic REPAIR_REQUIRED/REJECTED
  to CLEAN. The deterministic verdict always stands.
- High-severity LLM findings on a deterministic-CLEAN job route to HUMAN
  ESCALATION with a plain-language summary + evidence. Never auto-merge:
  the job is held in REPAIR_REQUIRED for operator review.
- Any error, timeout, or schema-invalid output -> the stage is skipped, a
  note is audited, and the deterministic verdict stands unchanged.

Re-review loop (bounded): when a repair dispatched from LLM findings is
re-submitted, the LLM re-reviews the new diff against the original
findings (fixed / still-broken + why). At most 2 LLM re-reviews per job;
after that the job goes to a human.

Model names and endpoint are configuration, never hardcoded: Michael
supplies the exact Ned / George model names and endpoint at enable time.
Set PRISMATIC_REVIEW_LLM_PROTOCOL=vllm to talk to a vLLM
OpenAI-compatible endpoint instead of Ollama.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from prismatic.review_factory.models import Finding

logger = logging.getLogger(__name__)

# ── Environment / toggle configuration ─────────────────────────────────

_ENV_ENABLE = "PRISMATIC_REVIEW_LLM"
_ENV_ENDPOINT = "PRISMATIC_REVIEW_LLM_ENDPOINT"
_ENV_MODEL_FULL = "PRISMATIC_REVIEW_LLM_MODEL"  # Ned-class, 262k ctx: full reviews
_ENV_MODEL_BOUNDED = (
    "PRISMATIC_REVIEW_LLM_MODEL_BOUNDED"  # George-class, 64k: truncated diffs
)
_ENV_TIMEOUT = "PRISMATIC_REVIEW_LLM_TIMEOUT"
_ENV_MAX_DIFF = "PRISMATIC_REVIEW_LLM_MAX_DIFF"
_ENV_MAX_DIFF_FULL = "PRISMATIC_REVIEW_LLM_MAX_DIFF_FULL"  # Ned-class, 262k ctx
_ENV_PROTOCOL = "PRISMATIC_REVIEW_LLM_PROTOCOL"  # "vllm" | "ollama" (default)
_ENV_VLLM_KEY = "VLLM_API_KEY"  # fallback VLLM_NED_API_KEY, read by the client

_TOGGLE_FILE = Path.home() / ".prismatic" / "review-factory-llm.json"


def toggle_file_path() -> Path:
    """Location of the dashboard-toggle state file."""
    return _TOGGLE_FILE


def set_toggle_enabled(enabled: bool, actor: str = "") -> Path:
    """Persist the dashboard toggle. Returns the toggle file path."""
    from datetime import datetime, timezone

    path = toggle_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(enabled),
        "actor": actor or "dashboard",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Dashboard toggle for the optional LLM deep-review stage. "
            "PRISMATIC_REVIEW_LLM=1 in the environment always enables the "
            "stage regardless of this file."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _toggle_file_enabled() -> bool:
    try:
        data = json.loads(toggle_file_path().read_text(encoding="utf-8"))
        return bool(data.get("enabled", False))
    except Exception:
        return False


def _parse_int_or(raw: str, default: int) -> int:
    """Parse an int env value; fall back to default on garbage."""
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class LLMReviewConfig:
    """Configuration for the optional LLM deep-review stage.

    Model names are deliberately empty by default — Michael supplies the
    exact Ned / George names and endpoint at enable time.
    """

    enabled: bool = False
    endpoint: str = "http://localhost:11434"
    protocol: str = "ollama"  # "ollama" (Ollama /api/chat) or "vllm" (OpenAI /v1)
    model_full: str = ""  # Ned-class full-review model (262k ctx)
    model_bounded: str = ""  # George-class bounded-review model (64k ctx)
    timeout_seconds: float = 180.0
    max_diff_chars: int = 100_000  # above this -> bounded model + truncated diff
    max_diff_full_chars: int = 600_000  # truncation limit for the full (Ned) model
    max_rereviews: int = 2
    temperature: float = 0.1

    @classmethod
    def from_env(cls) -> "LLMReviewConfig":
        enabled = os.environ.get(_ENV_ENABLE, "").strip() == "1"
        if not enabled:
            enabled = _toggle_file_enabled()
        timeout = os.environ.get(_ENV_TIMEOUT, "").strip()
        max_diff = os.environ.get(_ENV_MAX_DIFF, "").strip()
        max_diff_full = os.environ.get(_ENV_MAX_DIFF_FULL, "").strip()
        protocol = os.environ.get(_ENV_PROTOCOL, "").strip().lower()
        if protocol not in ("ollama", "vllm"):
            protocol = "ollama"
        return cls(
            enabled=enabled,
            protocol=protocol,
            endpoint=os.environ.get(_ENV_ENDPOINT, "").strip()
            or "http://localhost:11434",
            model_full=os.environ.get(_ENV_MODEL_FULL, "").strip(),
            model_bounded=os.environ.get(_ENV_MODEL_BOUNDED, "").strip(),
            timeout_seconds=float(timeout) if timeout else 180.0,
            max_diff_chars=int(max_diff) if max_diff else 100_000,
            max_diff_full_chars=_parse_int_or(max_diff_full, 600_000),
            max_rereviews=2,
            temperature=0.1,
        )

    def is_configured(self) -> bool:
        """Enabled AND at least one model name supplied."""
        return self.enabled and bool(self.model_full or self.model_bounded)


# ── Structured output types ────────────────────────────────────────────

# Raw LLM severities -> normalized factory severities (Finding.severity).
_SEVERITY_MAP = {
    "info": "info",
    "low": "info",
    "medium": "warning",
    "high": "error",
    "critical": "critical",
}
_ESCALATION_SEVERITIES = ("error", "critical")
_VALID_VERDICTS = ("clean", "repair_required", "rejected")
_VALID_CATEGORIES = (
    "security",
    "correctness",
    "concurrency",
    "error-handling",
    "performance",
    "api-contract",
    "data-integrity",
    "testing",
    "maintainability",
)

_MAX_FINDINGS = 25
_MAX_EXPLANATION_CHARS = 2000


@dataclass
class LLMFinding:
    """One structured finding from the LLM deep review."""

    severity: str  # normalized: info | warning | error | critical
    file: str
    lines: str  # "123" or "120-135"
    category: str
    explanation: str

    def to_finding(self) -> Finding:
        start = self.lines.split("-")[0].strip()
        try:
            line_no = int(start)
        except ValueError:
            line_no = 0
        return Finding(
            severity=self.severity,
            path=self.file,
            line=line_no,
            invariant=f"[{self.category}] {self.explanation}",
            reproduction_command="",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "file": self.file,
            "lines": self.lines,
            "category": self.category,
            "explanation": self.explanation,
        }


@dataclass
class LLMReviewResult:
    findings: list[LLMFinding] = field(default_factory=list)
    verdict: str = "clean"
    confidence: float = 0.0
    rationale: str = ""
    model: str = ""


@dataclass
class LLMReassessment:
    finding_index: int
    status: str  # fixed | still_broken
    why: str


@dataclass
class LLMReReviewResult:
    reassessments: list[LLMReassessment] = field(default_factory=list)
    verdict: str = "clean"
    confidence: float = 0.0
    rationale: str = ""
    model: str = ""


@dataclass
class LLMStageOutcome:
    """What the daemon should do with an LLM stage result."""

    action: (
        str  # packet | escalate | rereview_fixed | rereview_repair | rereview_escalate
    )
    summary: str = ""
    detail: str = ""
    packet: dict[str, Any] | None = None
    work_order_text: str = ""
    evidence: dict[str, Any] | None = None
    force_dispatch: bool = False  # True: re-review repair needs a fresh task


# ── Schema validation ──────────────────────────────────────────────────


def _normalize_lines(raw: Any) -> str:
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return f"{raw[0]}-{raw[1]}"
    text = str(raw or "").strip()
    if re.fullmatch(r"\d+", text):
        return text
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return text or "0"


def _validate_finding(raw: Any, index: int) -> LLMFinding:
    if not isinstance(raw, dict):
        raise ValueError(f"finding[{index}] is not an object")
    severity_raw = str(raw.get("severity") or "").strip().lower()
    if severity_raw not in _SEVERITY_MAP:
        raise ValueError(
            f"finding[{index}].severity {severity_raw!r} not in {sorted(_SEVERITY_MAP)}"
        )
    file = str(raw.get("file") or raw.get("path") or "").strip()
    if not file:
        raise ValueError(f"finding[{index}].file is required")
    explanation = str(raw.get("explanation") or raw.get("message") or "").strip()
    if not explanation:
        raise ValueError(f"finding[{index}].explanation is required")
    category = str(raw.get("category") or "correctness").strip().lower()
    if category not in _VALID_CATEGORIES:
        category = "correctness"
    return LLMFinding(
        severity=_SEVERITY_MAP[severity_raw],
        file=file[:500],
        lines=_normalize_lines(raw.get("lines", raw.get("line", 0))),
        category=category,
        explanation=explanation[:_MAX_EXPLANATION_CHARS],
    )


def validate_review_output(data: Any, *, model: str = "") -> LLMReviewResult:
    """Validate raw model JSON into an LLMReviewResult. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("LLM output 'findings' must be a list")
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"LLM verdict {verdict!r} not in {_VALID_VERDICTS}")
    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        raise ValueError("LLM confidence must be a number") from None
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("LLM confidence must be between 0.0 and 1.0")
    findings = [
        _validate_finding(raw, i) for i, raw in enumerate(raw_findings[:_MAX_FINDINGS])
    ]
    return LLMReviewResult(
        findings=findings,
        verdict=verdict,
        confidence=confidence,
        rationale=str(data.get("rationale") or "")[:_MAX_EXPLANATION_CHARS],
        model=model,
    )


def validate_rereview_output(data: Any, *, model: str = "") -> LLMReReviewResult:
    """Validate raw model JSON into an LLMReReviewResult. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    raw = data.get("reassessments")
    if not isinstance(raw, list):
        raise ValueError("LLM output 'reassessments' must be a list")
    reassessments: list[LLMReassessment] = []
    for i, item in enumerate(raw[:_MAX_FINDINGS]):
        if not isinstance(item, dict):
            raise ValueError(f"reassessment[{i}] is not an object")
        try:
            finding_index = int(item.get("finding_index", i))
        except (TypeError, ValueError):
            raise ValueError(
                f"reassessment[{i}].finding_index must be an int"
            ) from None
        status = str(item.get("status") or "").strip().lower()
        if status not in ("fixed", "still_broken"):
            raise ValueError(
                f"reassessment[{i}].status {status!r} must be fixed|still_broken"
            )
        reassessments.append(
            LLMReassessment(
                finding_index=finding_index,
                status=status,
                why=str(item.get("why") or "")[:_MAX_EXPLANATION_CHARS],
            )
        )
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"LLM verdict {verdict!r} not in {_VALID_VERDICTS}")
    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        raise ValueError("LLM confidence must be a number") from None
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("LLM confidence must be between 0.0 and 1.0")
    return LLMReReviewResult(
        reassessments=reassessments,
        verdict=verdict,
        confidence=confidence,
        rationale=str(data.get("rationale") or "")[:_MAX_EXPLANATION_CHARS],
        model=model,
    )


# ── Verdict combination (the never-downgrade rule) ─────────────────────


def resolve_llm_outcome(deterministic_verdict: str, llm: LLMReviewResult | None) -> str:
    """Combine the deterministic verdict with the LLM result.

    Returns one of:
    - "none": stage skipped / no LLM result — deterministic verdict stands.
    - "packet": compile findings into a repair work order. Used whenever the
      deterministic verdict is REPAIR_REQUIRED/REJECTED — the deterministic
      verdict is NEVER downgraded, even if the LLM said "clean".
    - "escalate": deterministic CLEAN but the LLM found high-severity
      (error/critical) issues -> human escalation, never auto-merge.
    - "advisory": deterministic CLEAN and no high-severity LLM findings ->
      findings are recorded in the audit trail only.
    """
    if llm is None:
        return "none"
    det = (deterministic_verdict or "").strip().lower()
    if det in ("repair_required", "rejected"):
        return "packet"
    if det == "clean":
        if any(f.severity in _ESCALATION_SEVERITIES for f in llm.findings):
            return "escalate"
        return "advisory"
    return "none"


# ── Prompts ────────────────────────────────────────────────────────────

_REVIEW_SYSTEM_PROMPT = """\
You are a senior code reviewer for the Prismatic Engine project. You review a candidate code diff AFTER deterministic checks (tests, import checks, heuristic secret/size scans) have already run.

Rules:
- Output STRICT JSON only, matching the schema below. No markdown fences, no commentary, no extra keys.
- Findings must be specific: file path, line or line range, category, and a 1-3 sentence explanation of the concrete risk.
- Categories: security, correctness, concurrency, error-handling, performance, api-contract, data-integrity, testing, maintainability.
- Severity: info (nit), low (minor), medium (worth fixing), high (should block), critical (must block: security hole, data loss, corruption).
- verdict: "clean" (nothing above medium), "repair_required" (medium+ findings worth a repair round), "rejected" (high/critical findings that make the candidate unsafe).
- confidence: 0.0-1.0. rationale: 2-4 sentences tying the verdict to the top findings.
- Do NOT repeat the deterministic findings listed below unless you disagree with one; focus on what automated checks miss: logic errors, race conditions, insecure patterns, wrong error handling, API contract violations.

Schema:
{
  "findings": [{"severity": "info|low|medium|high|critical", "file": "path/to/file.py", "lines": "12 or 12-18", "category": "security", "explanation": "..."}],
  "verdict": "clean|repair_required|rejected",
  "confidence": 0.0,
  "rationale": "..."
}"""

_REREVIEW_SYSTEM_PROMPT = """\
You are a senior code reviewer for the Prismatic Engine project. You previously deep-reviewed a candidate and reported findings. A repair was attempted and a new candidate was submitted.

Rules:
- Output STRICT JSON only, matching the schema below. No markdown fences, no commentary, no extra keys.
- For EACH original finding (by finding_index), report whether it is "fixed" or "still_broken" in the new candidate, with a 1-2 sentence "why".
- verdict "clean" ONLY if every original finding is fixed and nothing new is broken. Otherwise "repair_required" (or "rejected" for critical/high still-broken findings).
- confidence: 0.0-1.0. rationale: 2-4 sentences.

Schema:
{
  "reassessments": [{"finding_index": 0, "status": "fixed|still_broken", "why": "..."}],
  "verdict": "clean|repair_required|rejected",
  "confidence": 0.0,
  "rationale": "..."
}"""


def _truncate_diff(diff_text: str, max_chars: int) -> tuple[str, bool]:
    if len(diff_text) <= max_chars:
        return diff_text, False
    note = (
        "\n\n[... diff truncated for the bounded review model: "
        f"showing first {max_chars} of {len(diff_text)} chars ...]"
    )
    return diff_text[:max_chars] + note, True


def _extract_content(resp: dict[str, Any]) -> str:
    msg = resp.get("message") or {}
    content = str(msg.get("content") or resp.get("response") or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


# ── Repair work-order compilation ──────────────────────────────────────

_TEST_SUGGESTIONS: dict[str, list[str]] = {
    "security": [
        "Add a test that drives the flagged path with hostile input and asserts it is rejected or sanitized.",
        "Add a test asserting secrets/credentials can never appear in logs, errors, or stored payloads on this path.",
    ],
    "correctness": [
        "Add a regression test reproducing the wrong behavior and asserting the expected output.",
    ],
    "concurrency": [
        "Add a test that exercises the flagged path from multiple threads and asserts no lost updates or deadlocks.",
    ],
    "error-handling": [
        "Add a test for the failure case (exception / timeout / bad input) asserting the error is handled, not swallowed.",
    ],
    "performance": [
        "Add a test with a realistic input size asserting the operation completes within budget.",
    ],
    "api-contract": [
        "Add a contract test asserting the request/response shape the finding says is violated.",
    ],
    "data-integrity": [
        "Add a test asserting stored data round-trips unchanged through the flagged path.",
    ],
    "testing": [
        "Add the missing focused test the finding describes.",
    ],
    "maintainability": [
        "Add or update a test that pins the intended behavior before refactoring.",
    ],
}


def compile_repair_packet(
    *,
    job_id: str,
    candidate_tree: str,
    candidate_commit: str,
    model: str,
    deterministic_verdict: str,
    findings: list[LLMFinding],
    reassessments: list[LLMReassessment] | None = None,
) -> dict[str, Any]:
    """Compile LLM findings into a concrete repair work order.

    Each work item names WHAT to change (file + lines), a patch SKETCH
    (anchored edit plan — an honest sketch, not a fabricated patch), and
    TESTS TO ADD. This is what gets dispatched through the existing repair
    flow so the repair agent receives a real work order.
    """
    reassess = {r.finding_index: r for r in (reassessments or [])}
    work_items: list[dict[str, Any]] = []
    for i, f in enumerate(findings):
        r = reassess.get(i)
        status_note = ""
        if r is not None:
            status_note = f"Re-review status: {r.status} — {r.why}\n"
        what_to_change = f"{status_note}In {f.file} (lines {f.lines}), {f.explanation}"
        patch_sketch = (
            f"--- a/{f.file}\n"
            f"+++ b/{f.file}\n"
            f"@@ lines {f.lines} [{f.category}, severity {f.severity}] @@\n"
            f"# Change direction: {f.explanation}\n"
            "# Edit the code at the anchored lines above so the described\n"
            "# risk is eliminated; keep the surrounding behavior identical.\n"
            "# Verify with the tests listed below before re-submitting."
        )
        tests_to_add = list(
            _TEST_SUGGESTIONS.get(f.category, _TEST_SUGGESTIONS["correctness"])
        )
        work_items.append(
            {
                "finding_index": i,
                "severity": f.severity,
                "file": f.file,
                "lines": f.lines,
                "category": f.category,
                "what_to_change": what_to_change,
                "patch_sketch": patch_sketch,
                "tests_to_add": tests_to_add,
            }
        )
    summary = (
        f"LLM deep review ({model}) produced {len(findings)} finding(s) on "
        f"candidate {(candidate_commit or '')[:12]} "
        f"(deterministic verdict: {deterministic_verdict})."
    )
    return {
        "kind": "llm-repair-work-order",
        "job_id": job_id,
        "candidate_tree": candidate_tree,
        "candidate_commit": candidate_commit,
        "model": model,
        "deterministic_verdict": deterministic_verdict,
        "summary": summary,
        "work_items": work_items,
    }


def render_work_order_text(packet: dict[str, Any]) -> str:
    """Render a repair packet as plain text for the repair task payload."""
    lines = [
        "LLM deep-review work order "
        f"(model {packet.get('model') or 'n/a'}, "
        f"{len(packet.get('work_items') or [])} findings):",
        str(packet.get("summary") or ""),
        "",
    ]
    for item in packet.get("work_items") or []:
        lines += [
            f"[{item.get('finding_index')}] {item.get('file')}:{item.get('lines')} "
            f"[{item.get('severity')}/{item.get('category')}]",
            f"    What to change: {item.get('what_to_change')}",
            "    Patch sketch:",
        ]
        for sketch_line in str(item.get("patch_sketch") or "").splitlines():
            lines.append(f"        {sketch_line}")
        lines.append("    Tests to add:")
        for test in item.get("tests_to_add") or []:
            lines.append(f"      - {test}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Human escalation ───────────────────────────────────────────────────

_LINEAR_ISSUE_RE = re.compile(r"[A-Z][A-Z0-9]*-\d+")


def _try_linear_comment(issue_id: str, body: str) -> str:
    """Best-effort Linear comment. Never raises; returns a status string."""
    task_id = (issue_id or "").strip()
    if not _LINEAR_ISSUE_RE.fullmatch(task_id):
        return "skipped_no_issue"
    try:
        from prismatic.providers.tasks.linear import LinearTaskProvider

        provider = LinearTaskProvider()
        if not getattr(provider, "_api_key", ""):
            return "skipped_unconfigured"
        ok = provider.add_comment(task_id, body)
        return "posted" if ok else "failed"
    except Exception as exc:
        logger.warning("llm escalation linear comment failed for %s: %s", task_id, exc)
        return "failed"


def summarize_for_human(
    *,
    job_id: str,
    task_id: str,
    candidate_commit: str,
    model: str,
    deterministic_verdict: str,
    findings: list[LLMFinding],
    reason: str = "",
) -> str:
    """Crisp plain-language escalation summary with evidence."""
    severe = [f for f in findings if f.severity in _ESCALATION_SEVERITIES]
    lines = [
        f"LLM deep review ({model}) flagged {len(severe)} high-severity "
        f"finding(s) on job {job_id} (task {task_id or 'n/a'}, candidate "
        f"{(candidate_commit or '')[:12]}), even though the deterministic "
        f"review said {deterministic_verdict.upper()}.",
        "",
        "The deterministic checks (tests, import checks, heuristic scans) "
        "passed, but the model found problems those checks cannot see:",
        "",
    ]
    for f in severe[:10]:
        lines.append(f"- {f.file}:{f.lines} [{f.category}] — {f.explanation}")
    if reason:
        lines += ["", reason]
    lines += [
        "",
        "This job is HELD for human review (state: repair_required). "
        "It will NOT auto-merge.",
        "Full findings are in the Review Factory audit log (action: llm_escalated).",
    ]
    return "\n".join(lines)


def escalate_to_human(
    db: Any,
    job: Any,
    *,
    summary: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a human escalation: audit entry + best-effort Linear comment.

    Never raises. Returns {"linear_comment": <status>}.
    """
    job_id = getattr(job, "review_job_id", "")
    linear_body = (
        "Review Factory LLM deep review escalated this job for human review.\n\n"
        + summary
    )
    linear_status = _try_linear_comment(getattr(job, "task_id", ""), linear_body)
    try:
        db.insert_audit_entry(
            actor="review-factory:llm-review",
            action="llm_escalated",
            review_job_id=job_id,
            details={
                "summary": summary[:4000],
                "evidence": evidence or {},
                "linear_comment": linear_status,
                "task_id": getattr(job, "task_id", ""),
            },
        )
    except Exception as exc:
        logger.error("llm escalation audit failed for job %s: %s", job_id, exc)
    return {"linear_comment": linear_status}


# ── Re-review budget ───────────────────────────────────────────────────


def rereview_budget_remaining(db: Any, job_id: str, max_rereviews: int) -> int:
    """How many LLM re-reviews are left for this job (bounded at max)."""
    try:
        done = db.count_audit_entries(job_id, "llm_rereview_completed")
    except Exception:
        done = 0
    return max(0, int(max_rereviews) - int(done or 0))


# ── Provider registry ────────────────────────────────────────────────────

PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    "vllm": ("prismatic.providers.vllm", "VLLMClient"),
    "ollama": ("prismatic.providers.ollama", "OllamaClient"),
}

DEFAULT_PROTOCOL = "ollama"


# ── The adapter ────────────────────────────────────────────────────────


class LLMDeepReviewAdapter:
    """Runs the optional LLM deep-review stage against Ollama or vLLM.

    Protocol is selected by PRISMATIC_REVIEW_LLM_PROTOCOL ("vllm" selects
    the OpenAI-compatible vLLM client; anything else keeps the Ollama
    client). All failure modes return None (stage skipped) so the
    deterministic pipeline never depends on this stage.
    """

    def __init__(self, config: LLMReviewConfig, client: Any = None):
        self.config = config
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            proto = (self.config.protocol or DEFAULT_PROTOCOL).lower()
            module_name, class_name = PROVIDER_REGISTRY.get(
                proto, PROVIDER_REGISTRY[DEFAULT_PROTOCOL]
            )
            import importlib

            module = importlib.import_module(module_name)
            client_cls = getattr(module, class_name)
            # API key comes from VLLM_API_KEY / VLLM_NED_API_KEY in the
            # environment (read inside the client); never hardcoded.
            self._client = client_cls(
                base_url=self.config.endpoint,
                timeout=10.0,
            )
        return self._client

    # -- gates ----------------------------------------------------------

    def gates_pass(self) -> tuple[bool, str]:
        """Config gate + health gate. Returns (ok, reason)."""
        if not self.config.enabled:
            return (
                False,
                "disabled: PRISMATIC_REVIEW_LLM not set and dashboard toggle off",
            )
        if not self.config.model_full and not self.config.model_bounded:
            return (
                False,
                "no model configured (set PRISMATIC_REVIEW_LLM_MODEL / _BOUNDED)",
            )
        proto = (self.config.protocol or "ollama").lower()
        try:
            healthy = self.client.check_health()
        except Exception as exc:
            return False, f"llm health check raised ({proto}): {exc}"
        if not healthy:
            return False, f"llm endpoint unreachable ({proto}): {self.config.endpoint}"
        full_ok = bool(self.config.model_full) and self._model_ok(
            self.config.model_full
        )
        bounded_ok = bool(self.config.model_bounded) and self._model_ok(
            self.config.model_bounded
        )
        if not (full_ok or bounded_ok):
            return (
                False,
                f"configured model not available on the {proto} host "
                f"(full={self.config.model_full or '-'}, "
                f"bounded={self.config.model_bounded or '-'})",
            )
        return True, "ok"

    def _model_ok(self, name: str) -> bool:
        try:
            return bool(self.client.model_available(name))
        except Exception:
            return False

    def _select_model(self, diff_chars: int) -> tuple[Optional[str], int]:
        """Pick full (Ned) vs bounded (George) model by diff size, with fallback.

        Returns (model_name | None, truncation_limit_chars). The full model
        gets the large max_diff_full_chars limit; the bounded model keeps the
        smaller max_diff_chars limit.
        """
        prefer_bounded = diff_chars > self.config.max_diff_chars
        ordered = (
            [
                (self.config.model_bounded, self.config.max_diff_chars),
                (self.config.model_full, self.config.max_diff_full_chars),
            ]
            if prefer_bounded
            else [
                (self.config.model_full, self.config.max_diff_full_chars),
                (self.config.model_bounded, self.config.max_diff_chars),
            ]
        )
        for name, limit in ordered:
            if name and self._model_ok(name):
                return name, limit
        return None, 0

    # -- review ---------------------------------------------------------

    def review(
        self,
        job: Any,
        *,
        diff_text: str,
        deterministic_verdict: str,
        deterministic_findings: list[dict[str, Any]],
    ) -> LLMReviewResult | None:
        """Run the deep review. Returns None when the stage must be skipped."""
        ok, _reason = self.gates_pass()
        if not ok:
            return None
        model, max_chars = self._select_model(len(diff_text))
        if not model:
            return None
        diff_text, truncated = _truncate_diff(diff_text, max_chars)
        det_findings_text = self._format_det_findings(deterministic_findings)
        user_prompt = (
            f"Repository: {getattr(job, 'repository', '') or 'n/a'}\n"
            f"Candidate commit: {getattr(job, 'candidate_commit', '')} "
            f"(tree {getattr(job, 'candidate_tree', '')})\n"
            f"Deterministic verdict: {deterministic_verdict}\n"
            f"Deterministic findings (already handled or accepted):\n{det_findings_text}\n\n"
            f"Diff to review ({len(diff_text)} chars"
            f"{', truncated for the bounded model' if truncated else ''}):\n{diff_text}"
        )
        resp = self._chat(model, _REVIEW_SYSTEM_PROMPT, user_prompt)
        if resp is None:
            return None
        try:
            return validate_review_output(resp, model=model)
        except ValueError as exc:
            logger.warning("llm review output failed schema validation: %s", exc)
            return None

    def rereview(
        self,
        job: Any,
        *,
        new_diff_text: str,
        original_findings: list[dict[str, Any]],
        original_tree: str,
    ) -> LLMReReviewResult | None:
        """Re-review a repaired candidate against the original findings."""
        ok, _reason = self.gates_pass()
        if not ok:
            return None
        model, max_chars = self._select_model(len(new_diff_text))
        if not model:
            return None
        new_diff_text, truncated = _truncate_diff(new_diff_text, max_chars)
        orig_lines = []
        for i, f in enumerate(original_findings[:_MAX_FINDINGS]):
            if isinstance(f, dict):
                orig_lines.append(
                    f"[{i}] {f.get('file')}:{f.get('lines')} "
                    f"[{f.get('severity')}/{f.get('category')}] {f.get('explanation')}"
                )
            else:
                orig_lines.append(f"[{i}] {f}")
        user_prompt = (
            f"You previously deep-reviewed candidate tree {original_tree} and reported "
            f"these findings:\n" + "\n".join(orig_lines) + "\n\n"
            f"A repair was attempted. New candidate tree: {getattr(job, 'candidate_tree', '')}. "
            f"New diff (new tree vs base, {len(new_diff_text)} chars"
            f"{', truncated for the bounded model' if truncated else ''}):\n{new_diff_text}"
        )
        resp = self._chat(model, _REREVIEW_SYSTEM_PROMPT, user_prompt)
        if resp is None:
            return None
        try:
            return validate_rereview_output(resp, model=model)
        except ValueError as exc:
            logger.warning("llm re-review output failed schema validation: %s", exc)
            return None

    # -- internals ------------------------------------------------------

    def _chat(
        self, model: str, system_prompt: str, user_prompt: str
    ) -> dict[str, Any] | None:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            resp = self.client.chat(
                model,
                messages,
                format="json",
                options={"temperature": self.config.temperature},
                timeout=self.config.timeout_seconds,
            )
        except Exception as exc:
            logger.warning("llm chat call failed for model %s: %s", model, exc)
            return None
        if not isinstance(resp, dict):
            return None
        content = _extract_content(resp)
        if not content:
            return None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("llm chat returned non-JSON content for model %s", model)
            return None
        return data

    @staticmethod
    def _format_det_findings(findings: list[dict[str, Any]]) -> str:
        if not findings:
            return "- (none)"
        lines = []
        for f in findings[:20]:
            if isinstance(f, dict):
                lines.append(
                    f"- [{f.get('severity', '?')}] {f.get('path', '')}:"
                    f"{f.get('line', '')} {f.get('invariant', '')}"[:300]
                )
            else:
                lines.append(f"- {f}"[:300])
        return "\n".join(lines)
