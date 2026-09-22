"""Phase-advancement machinery (Chunk 8 of the post-shadow roadmap).

The rollout ladder (shadow -> verify -> bounded-live -> tier-2/3-human)
advances ONLY through :meth:`PhaseAdvancement.execute`, and execute() refuses
unless ALL of these hold:

1. the active phase policy sets ``advancements_enabled: true``
   (the shipped policy sets it false — the machinery is inert by default);
2. a valid ``phase-advancement-approval/v1`` record exists: correct schema,
   the policy's approver (Michael), exactly this one-rung step, bound to the
   filed request's log hash;
3. the mechanical exit criteria for the transition pass, re-evaluated at
   execution time from the live evidence — an approval never waives evidence.

There is no other code path that advances a phase. The phase lives in the
highest-versioned ``spec/phase_policy_v*.yaml`` file; ``execute()`` is the
only writer of new versions. Reverting is a config change (a newer versioned
file with a lower phase), never a code deploy.

Every request, approval validation result, refusal, execution, and revert
appends one row to the hash-chained advancement log and emits one audit
signal. No signal, no action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

from prismatic.review_factory.shadow_agreement import (
    agreement_rate,
    shadow_exit_met,
)

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_ADVANCEMENT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/phase-advancement-log.jsonl")
)
DEFAULT_ADVANCEMENT_AUDIT = Path(
    os.path.expanduser("~/.prismatic/audit/phase-advancement.jsonl")
)

# ─────────────────────────────────────────────────────────────────────
# Ladder and schemas
# ─────────────────────────────────────────────────────────────────────

PHASE_0_SHADOW = 0
PHASE_1_VERIFY = 1
PHASE_2_BOUNDED_LIVE = 2
PHASE_3_TIER23_HUMAN = 3
MAX_PHASE = 3

PHASE_NAMES = {
    PHASE_0_SHADOW: "shadow",
    PHASE_1_VERIFY: "verify",
    PHASE_2_BOUNDED_LIVE: "bounded-live",
    PHASE_3_TIER23_HUMAN: "tier23-human",
}

REQUEST_SCHEMA = "phase-advancement-request/v1"
APPROVAL_SCHEMA = "phase-advancement-approval/v1"

POLICY_FILE_RE = re.compile(r"^phase_policy_v(\d+)\.yaml$")

AUDIT_AGENT = "prismatic-phase-advancement"

# event_type values for the audit sink. These are intentionally distinct
# from the routine review-factory "decision" signals: an approval record
# must never be confused with a decision signal, and vice versa.
EVT_REQUESTED = "phase_advancement_requested"
EVT_APPROVAL_ACCEPTED = "phase_advancement_approval_accepted"
EVT_APPROVAL_DENIED = "phase_advancement_approval_denied"
EVT_EXECUTION_SUCCEEDED = "phase_advancement_execution_succeeded"
EVT_EXECUTION_REFUSED = "phase_advancement_execution_refused"
EVT_REVERTED = "phase_advancement_reverted"

# Required top-level fields on a complete audit signal (the same shape the
# prismatic-audit skill's bin/emit produces).
SIGNAL_REQUIRED_FIELDS = (
    "agent",
    "event_type",
    "id",
    "message",
    "metadata",
    "severity",
    "source",
    "status",
    "timestamp",
)

# Metadata a complete shadow-decision signal must carry for the Phase 0
# exit criterion "every shadow decision has a complete audit signal".
SHADOW_SIGNAL_METADATA_REQUIRED = ("pr_number", "call", "policy_version")

OUTCOME_CLEAN = "clean"
OUTCOME_ROLLBACK = "rolled_back"
DECISION_ALLOWED = "allowed"

# Phase 2 -> 3: at least one merge in the trailing window, otherwise a
# quiet month would read as a clean one.
PHASE2_MIN_MERGES = 1
PHASE2_MAX_ROLLBACK_RATE = 0.02
PHASE2_WINDOW_DAYS = 30

# Phase 1 -> 2 exit criterion from build-sequence.md §Phase gates.
PHASE1_CONSECUTIVE_MERGES = 20


class PhaseAdvancementError(Exception):
    """Raised for configuration/usage problems with the machinery.

    Fail-closed: every error path ends in refusal, never in advancement.
    """


# ─────────────────────────────────────────────────────────────────────
# Phase policy config (versioned; never edited in place)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhasePolicy:
    """The active phase config, resolved from the highest-versioned
    ``spec/phase_policy_v*.yaml`` file."""

    version: str
    phase: int
    advancements_enabled: bool
    approver: str
    chunks: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_file: str = "") -> "PhasePolicy":
        try:
            phase = int(data["phase"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PhaseAdvancementError(
                f"phase policy missing/invalid 'phase': {source_file}"
            ) from exc
        if phase not in PHASE_NAMES:
            raise PhaseAdvancementError(
                f"phase policy has unknown phase {phase}: {source_file}"
            )
        return cls(
            version=str(data.get("version", "unknown")),
            phase=phase,
            advancements_enabled=bool(data.get("advancements_enabled", False)),
            approver=str(data.get("approver", "")),
            chunks=dict(data.get("chunks", {}) or {}),
            source_file=source_file,
        )


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PhaseAdvancementError(f"phase config not found: {path}")
    if not _HAS_YAML:
        raise PhaseAdvancementError(
            f"PyYAML is required to load phase config ({path}); refusing"
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PhaseAdvancementError(f"phase config is not a mapping: {path}")
    return data


def discover_active_policy(spec_dir: str | Path = SPEC_DIR) -> PhasePolicy:
    """Return the policy from the highest-versioned phase_policy_v*.yaml.

    Fail-closed: no versioned file, or an unreadable one, raises rather than
    falling back to assumed defaults.
    """
    spec_dir = Path(spec_dir)
    candidates: list[tuple[int, Path]] = []
    if spec_dir.is_dir():
        for child in spec_dir.iterdir():
            m = POLICY_FILE_RE.match(child.name)
            if m:
                candidates.append((int(m.group(1)), child))
    if not candidates:
        raise PhaseAdvancementError(
            f"no phase_policy_v*.yaml found in {spec_dir}; refusing"
        )
    version, path = max(candidates, key=lambda c: c[0])
    return PhasePolicy.from_dict(_load_yaml_file(path), source_file=str(path))


def render_policy_yaml(policy: PhasePolicy) -> str:
    """Render a PhasePolicy as the canonical versioned-file YAML."""
    data = {
        "version": policy.version,
        "phase": policy.phase,
        "advancements_enabled": policy.advancements_enabled,
        "approver": policy.approver,
        "chunks": policy.chunks,
    }
    return yaml.safe_dump(data, sort_keys=False)


# ─────────────────────────────────────────────────────────────────────
# Mechanical exit criteria (pure functions of parsed evidence)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExitResult:
    """Verdict of one transition's exit criteria."""

    met: bool
    checks: dict[str, bool]
    detail: str

    def failed_checks(self) -> list[str]:
        return [name for name, ok in self.checks.items() if not ok]


def _shadow_signal_complete(signal: dict[str, Any]) -> bool:
    if not isinstance(signal, dict):
        return False
    if any(f not in signal for f in SIGNAL_REQUIRED_FIELDS):
        return False
    metadata = signal.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return all(k in metadata for k in SHADOW_SIGNAL_METADATA_REQUIRED)


def check_phase0_exit(evidence: dict[str, Any]) -> ExitResult:
    """Phase 0 -> 1 exit criteria (build-sequence.md §Phase gates).

    Pure: evidence is already-parsed data, never fetched here.
    Expected keys:
      - shadow_records: joined [{system_call, actual_outcome}] records
      - bad_merge_calls: int
      - shadow_signals: list of emitted shadow audit-signal dicts
      - watchdog: {enabled: bool, mode: str} parsed from the watchdog policy
    """
    records = evidence.get("shadow_records", [])
    bad_merge_calls = int(evidence.get("bad_merge_calls", 0))
    signals = evidence.get("shadow_signals", [])
    watchdog = evidence.get("watchdog", {}) or {}

    gate = shadow_exit_met(records, bad_merge_calls=bad_merge_calls)
    stats = agreement_rate(records)
    complete = all(_shadow_signal_complete(s) for s in signals) if signals else False

    checks = {
        "min_prs_resolved": bool(gate["checks"]["min_prs"]),
        "min_agreement": bool(gate["checks"]["min_agreement"]),
        "zero_bad_merge_calls": bool(gate["checks"]["zero_bad_merges"]),
        "watchdog_armed_monitor_only": bool(watchdog.get("enabled"))
        and watchdog.get("mode") == "monitor-only",
        "all_shadow_signals_complete": complete,
    }
    rate = stats["rate"]
    detail = (
        f"n_decided={stats['n_decided']} n_agreed={stats['n_agreed']} "
        f"rate={'%.3f' % rate if rate is not None else 'n/a'} "
        f"bad_merge_calls={bad_merge_calls} "
        f"watchdog={watchdog.get('enabled')}/{watchdog.get('mode')} "
        f"shadow_signals={len(signals)} complete={complete}"
    )
    return ExitResult(met=all(checks.values()), checks=checks, detail=detail)


def _allowed_merges_newest_first(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = [d for d in decisions if d.get("decision") == DECISION_ALLOWED]
    allowed.sort(key=lambda d: str(d.get("timestamp", "")))
    return list(reversed(allowed))


def check_phase1_exit(evidence: dict[str, Any]) -> ExitResult:
    """Phase 1 -> 2 exit criteria: 20 consecutive auto-merges, 0 rollbacks.

    Pure: evidence["auto_merge_decisions"] is the parsed decision log —
    rows like {decision: "allowed"|"refused", outcome: "clean"|"rolled_back",
    timestamp: iso}. The streak is the 20 most recent allowed merges; every
    one must be clean. A refusal/escalation never breaks the streak (nothing
    merged); a rollback always does.
    """
    decisions = evidence.get("auto_merge_decisions", [])
    newest = _allowed_merges_newest_first(decisions)
    streak = newest[:PHASE1_CONSECUTIVE_MERGES]

    checks = {
        "twenty_merges_exist": len(streak) >= PHASE1_CONSECUTIVE_MERGES,
        "streak_all_clean": len(streak) >= PHASE1_CONSECUTIVE_MERGES
        and all(d.get("outcome") == OUTCOME_CLEAN for d in streak),
        "zero_rollbacks_in_streak": all(
            d.get("outcome") != OUTCOME_ROLLBACK for d in streak
        ),
    }
    clean = sum(1 for d in streak if d.get("outcome") == OUTCOME_CLEAN)
    detail = (
        f"allowed_merges_total={len(newest)} streak={len(streak)} "
        f"clean_in_streak={clean}"
    )
    return ExitResult(met=all(checks.values()), checks=checks, detail=detail)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def check_phase2_exit(
    evidence: dict[str, Any], now: datetime | None = None
) -> ExitResult:
    """Phase 2 -> 3 exit criteria: 30 days < 2% rollback rate, every novelty
    page justified in the weekly report.

    Pure: expected keys:
      - auto_merge_decisions: parsed decision log rows
      - novelty_pages: list of novelty-page signal ids in the window
      - weekly_reports: parsed weekly learn reports, each carrying
        {"novelty_pages_justified": [{"id": ..., "justification": ...}]}
    """
    now = now or datetime.now(timezone.utc)
    window_start = now.timestamp() - PHASE2_WINDOW_DAYS * 86400

    in_window = []
    for d in evidence.get("auto_merge_decisions", []):
        ts = _parse_ts(d.get("timestamp"))
        if (
            d.get("decision") == DECISION_ALLOWED
            and ts is not None
            and (ts.timestamp() >= window_start)
        ):
            in_window.append(d)
    merges = len(in_window)
    rollbacks = sum(1 for d in in_window if d.get("outcome") == OUTCOME_ROLLBACK)
    rate = (rollbacks / merges) if merges else None

    justified: dict[str, str] = {}
    for report in evidence.get("weekly_reports", []):
        entries = (report or {}).get("novelty_pages_justified", []) or []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                justified[str(entry["id"])] = str(entry.get("justification", ""))
    pages = [str(p) for p in evidence.get("novelty_pages", [])]
    pages_justified = bool(pages) and all(
        p in justified and justified[p].strip() for p in pages
    )

    checks = {
        "window_has_merges": merges >= PHASE2_MIN_MERGES,
        "rollback_rate_below_2pct": rate is not None
        and rate < PHASE2_MAX_ROLLBACK_RATE,
        "every_novelty_page_justified": pages_justified,
    }
    detail = (
        f"window_days={PHASE2_WINDOW_DAYS} merges={merges} "
        f"rollbacks={rollbacks} "
        f"rate={'%.4f' % rate if rate is not None else 'n/a'} "
        f"novelty_pages={len(pages)} justified={len(justified)}"
    )
    return ExitResult(met=all(checks.values()), checks=checks, detail=detail)


_EXIT_CHECKERS = {
    (PHASE_0_SHADOW, PHASE_1_VERIFY): check_phase0_exit,
    (PHASE_1_VERIFY, PHASE_2_BOUNDED_LIVE): check_phase1_exit,
    (PHASE_2_BOUNDED_LIVE, PHASE_3_TIER23_HUMAN): check_phase2_exit,
}


def evaluate_exit_criteria(
    from_phase: int, to_phase: int, evidence: dict[str, Any]
) -> ExitResult:
    """Dispatch to the mechanical exit check for one ladder step.

    Fail-closed: unknown steps, skipped rungs, or non-adjacent phases never
    evaluate as met.
    """
    if to_phase != from_phase + 1:
        return ExitResult(
            met=False,
            checks={"adjacent_step": False},
            detail=f"refusing non-adjacent step {from_phase} -> {to_phase}; "
            "the ladder is climbed one rung at a time",
        )
    checker = _EXIT_CHECKERS.get((from_phase, to_phase))
    if checker is None:
        return ExitResult(
            met=False,
            checks={"known_step": False},
            detail=f"no exit criteria defined for {from_phase} -> {to_phase}",
        )
    return checker(evidence)


# ─────────────────────────────────────────────────────────────────────
# Evidence loading (file IO lives here, never in the checks)
# ─────────────────────────────────────────────────────────────────────


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_evidence(pointers: dict[str, Any]) -> dict[str, Any]:
    """Resolve an evidence bundle's file pointers into parsed data.

    Pointers map criterion keys to paths (JSONL/YAML/JSON), except
    ``bad_merge_calls`` and ``novelty_pages`` which may be inline values.
    Missing optional pointers resolve to empty containers; a pointer that
    names a path that cannot be read raises PhaseAdvancementError
    (fail-closed — evidence must be readable to count).
    """
    evidence: dict[str, Any] = {}
    for key, pointer in pointers.items():
        if key == "bad_merge_calls":
            if isinstance(pointer, (int, float)):
                evidence[key] = int(pointer)
            else:  # file with a single integer
                text = Path(str(pointer)).read_text(encoding="utf-8").strip()
                evidence[key] = int(text)
            continue
        if key == "novelty_pages":
            if isinstance(pointer, list):
                evidence[key] = pointer
            else:
                with open(str(pointer), encoding="utf-8") as f:
                    evidence[key] = json.load(f)
            continue
        if key == "weekly_reports_dir":
            reports = []
            for child in sorted(Path(str(pointer)).glob("*.json")):
                with open(child, encoding="utf-8") as f:
                    reports.append(json.load(f))
            evidence[key] = reports
            continue
        if key == "watchdog_policy":
            data = _load_yaml_file(Path(str(pointer)))
            evidence["watchdog"] = {
                "enabled": bool(data.get("enabled", False)),
                "mode": str(data.get("mode", "")),
            }
            continue
        # default: JSONL log
        evidence[key] = _read_jsonl(str(pointer))
    return evidence


# ─────────────────────────────────────────────────────────────────────
# Hash-chained, append-only advancement log
# ─────────────────────────────────────────────────────────────────────

GENESIS_HASH = "GENESIS"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def row_hash(prev_hash: str, row_type: str, payload: dict[str, Any]) -> str:
    body = _canonical({"prev_hash": prev_hash, "type": row_type, "payload": payload})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AdvancementLog:
    """Append-only JSONL log; every row hashes the previous row's hash."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return _read_jsonl(self.path)

    def append(self, row_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self.read_rows()
        prev_hash = rows[-1]["hash"] if rows else GENESIS_HASH
        row = {
            "seq": len(rows),
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": row_type,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        row["hash"] = row_hash(prev_hash, row_type, payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(_canonical(row) + "\n")
        return row

    def verify(self) -> tuple[bool, str]:
        """Recompute the chain. Returns (ok, detail); tampering with any
        row breaks its hash and every row after it."""
        prev = GENESIS_HASH
        for row in self.read_rows():
            if row.get("prev_hash") != prev:
                return False, f"seq {row.get('seq')}: prev_hash mismatch"
            expected = row_hash(
                row["prev_hash"], row.get("type", ""), row.get("payload", {})
            )
            if row.get("hash") != expected:
                return False, f"seq {row.get('seq')}: hash mismatch (tampered)"
            prev = row["hash"]
        return True, f"chain intact over {len(self.read_rows())} rows"


# ─────────────────────────────────────────────────────────────────────
# Request and approval records (versioned schemas)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdvancementRequest:
    request_id: str
    from_phase: int
    to_phase: int
    evidence_pointers: dict[str, Any]
    requester: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REQUEST_SCHEMA,
            "request_id": self.request_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "evidence_pointers": self.evidence_pointers,
            "requester": self.requester,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ApprovalRecord:
    """Michael's explicit approval, as a mechanically verifiable artifact."""

    schema: str
    phase: int
    target: str
    approver: str
    timestamp: str
    rationale: str
    request_hash: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRecord":
        if not isinstance(data, dict):
            raise PhaseAdvancementError("approval record is not a mapping")
        return cls(
            schema=str(data.get("schema", "")),
            phase=int(data.get("phase", -1)),
            target=str(data.get("target", "")),
            approver=str(data.get("approver", "")),
            timestamp=str(data.get("timestamp", "")),
            rationale=str(data.get("rationale", "")),
            request_hash=str(data.get("request_hash", "")),
        )


def expected_target(phase: int) -> str:
    return f"phase-{phase}-{PHASE_NAMES[phase]}"


def validate_approval(
    approval_data: dict[str, Any],
    policy: PhasePolicy,
    request: AdvancementRequest,
    request_hash: str,
) -> tuple[bool, list[str]]:
    """Mechanically verify an approval artifact. Returns (valid, reasons).

    Every failure is a refusal reason; there is no partial credit and no
    path that treats an invalid approval as valid.
    """
    reasons: list[str] = []
    try:
        approval = ApprovalRecord.from_dict(approval_data)
    except (PhaseAdvancementError, TypeError, ValueError):
        return False, ["approval record is not a valid mapping"]

    if approval.schema != APPROVAL_SCHEMA:
        reasons.append(
            f"schema {approval.schema!r} != {APPROVAL_SCHEMA!r}; "
            "routine signals and other artifacts are never approvals"
        )
    if approval.approver != policy.approver or not approval.approver:
        reasons.append(
            f"approver {approval.approver!r} is not the authorized approver "
            f"{policy.approver!r}"
        )
    if approval.phase != request.to_phase:
        reasons.append(
            f"approval phase {approval.phase} does not match requested "
            f"phase {request.to_phase}"
        )
    if approval.phase not in PHASE_NAMES:
        reasons.append(f"approval phase {approval.phase} is not a known ladder phase")
    elif approval.target != expected_target(approval.phase):
        reasons.append(
            f"target {approval.target!r} != {expected_target(approval.phase)!r}"
        )
    ts = _parse_ts(approval.timestamp)
    if ts is None:
        reasons.append("timestamp is not a parseable ISO-8601 datetime")
    elif ts > datetime.now(timezone.utc):
        reasons.append("timestamp is in the future")
    if not approval.rationale.strip():
        reasons.append("rationale is empty")
    if not approval.request_hash or approval.request_hash != request_hash:
        reasons.append(
            "request_hash does not match the filed request's log hash; "
            "approvals cannot be recycled across requests or evidence bundles"
        )
    return (len(reasons) == 0), reasons


# ─────────────────────────────────────────────────────────────────────
# Audit signal emission
# ─────────────────────────────────────────────────────────────────────


def _emit_audit(
    sink: str | Path,
    event_type: str,
    metadata: dict[str, Any],
    severity: str = "info",
) -> Path:
    sink = Path(sink)
    sink.parent.mkdir(parents=True, exist_ok=True)
    signal = {
        "agent": AUDIT_AGENT,
        "event_type": event_type,
        "id": f"phase-adv-{int(time.time() * 1000)}-{random.randrange(16**8):08x}",
        "issue_id": "",
        "log_path": "",
        "message": f"{event_type}: {metadata.get('summary', '')}"[:500],
        "metadata": metadata,
        "run_id": "",
        "severity": severity,
        "source": "prismatic-engine",
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcript": "",
    }
    with open(sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(signal) + "\n")
    return sink


# ─────────────────────────────────────────────────────────────────────
# The transition function — the ONLY writer of new phases
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ExecuteResult:
    executed: bool
    reasons: tuple[str, ...]
    new_phase: Optional[int] = None
    policy_file: Optional[Path] = None
    log_row: Optional[dict[str, Any]] = None


class PhaseAdvancement:
    """The machinery. ``execute()`` is the single code path that can advance
    the phase; everything else reads."""

    def __init__(
        self,
        spec_dir: str | Path = SPEC_DIR,
        log_path: str | Path = DEFAULT_ADVANCEMENT_LOG,
        audit_sink: str | Path = DEFAULT_ADVANCEMENT_AUDIT,
    ):
        self.spec_dir = Path(spec_dir)
        self.log = AdvancementLog(log_path)
        self.audit_sink = Path(audit_sink)

    # ── reads ──────────────────────────────────────────────────────

    def current_policy(self) -> PhasePolicy:
        return discover_active_policy(self.spec_dir)

    def current_phase(self) -> int:
        return self.current_policy().phase

    # There is intentionally no set_phase / advance_phase method. The phase
    # moves only by writing a new versioned policy file, which only
    # execute() does (advancement) or a config change does (revert).

    # ── request ────────────────────────────────────────────────────

    def request(
        self,
        to_phase: int,
        evidence_pointers: dict[str, Any],
        requester: str,
    ) -> AdvancementRequest:
        """File an advancement request. Records intent only — filing never
        advances anything. The step must be exactly one rung up."""
        from_phase = self.current_phase()
        if to_phase != from_phase + 1:
            raise PhaseAdvancementError(
                f"request must be one rung up: {from_phase} -> {to_phase}"
            )
        request = AdvancementRequest(
            request_id=(f"adv-{int(time.time() * 1000)}-{random.randrange(16**8):08x}"),
            from_phase=from_phase,
            to_phase=to_phase,
            evidence_pointers=dict(evidence_pointers),
            requester=requester,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        row = self.log.append("request", request.to_dict())
        _emit_audit(
            self.audit_sink,
            EVT_REQUESTED,
            {
                "summary": f"advancement requested {from_phase} -> {to_phase}",
                "request_id": request.request_id,
                "from_phase": from_phase,
                "to_phase": to_phase,
                "requester": requester,
                "log_hash": row["hash"],
            },
        )
        return request

    def _find_request_row(self, request_id: str) -> dict[str, Any] | None:
        for row in self.log.read_rows():
            if (
                row.get("type") == "request"
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("request_id") == request_id
            ):
                return row
        return None

    # ── execute ────────────────────────────────────────────────────

    def execute(
        self,
        request_id: str,
        approval_data: dict[str, Any],
        evidence: dict[str, Any],
    ) -> ExecuteResult:
        """Attempt the phase transition. Refuses (fail-closed) unless the
        master switch is on, the approval record validates against the
        filed request, AND the exit criteria pass on live evidence.

        On success: writes the new versioned phase policy file, appends the
        execution row to the hash-chained log, and emits the audit signal.
        """
        policy = self.current_policy()
        from_phase = policy.phase

        def _refused(reasons: tuple[str, ...]) -> ExecuteResult:
            return self._refuse(request_id, reasons)

        # 1. Master switch. Shipped false: the machinery cannot advance
        #    anything until a policy version explicitly enables it.
        if not policy.advancements_enabled:
            return _refused(
                (
                    "advancements_enabled is false in the active phase policy "
                    f"({policy.source_file}); the machinery is inert",
                )
            )

        # 2. The request must exist in the append-only log.
        row = self._find_request_row(request_id)
        if row is None:
            return _refused((f"no filed request with id {request_id!r}",))
        payload = row["payload"]
        request = AdvancementRequest(
            request_id=payload["request_id"],
            from_phase=int(payload["from_phase"]),
            to_phase=int(payload["to_phase"]),
            evidence_pointers=dict(payload.get("evidence_pointers", {})),
            requester=str(payload.get("requester", "")),
            timestamp=str(payload.get("timestamp", "")),
        )
        if request.from_phase != from_phase:
            return _refused(
                (
                    f"request was filed for phase {request.from_phase} but "
                    f"the active phase is now {from_phase}; re-file",
                )
            )

        # 3. The approval artifact must validate mechanically.
        valid, reasons = validate_approval(approval_data, policy, request, row["hash"])
        if not valid:
            _emit_audit(
                self.audit_sink,
                EVT_APPROVAL_DENIED,
                {
                    "summary": f"approval denied for {request_id}",
                    "request_id": request_id,
                    "reasons": list(reasons),
                },
                severity="warning",
            )
            return _refused(tuple(f"approval invalid: {r}" for r in reasons))
        _emit_audit(
            self.audit_sink,
            EVT_APPROVAL_ACCEPTED,
            {
                "summary": f"approval accepted for {request_id}",
                "request_id": request_id,
                "approver": approval_data.get("approver"),
                "phase": approval_data.get("phase"),
            },
        )

        # 4. Exit criteria re-evaluated at execution time on LIVE evidence.
        #    An approval never waives evidence.
        exit_result = evaluate_exit_criteria(
            request.from_phase, request.to_phase, evidence
        )
        if not exit_result.met:
            failed = ", ".join(exit_result.failed_checks())
            return _refused(
                (
                    f"exit criteria not met for "
                    f"{request.from_phase} -> {request.to_phase}: {failed} "
                    f"({exit_result.detail})",
                )
            )

        # 5. All gates passed: write the new versioned policy file (never
        #    edit in place), log the execution, audit it.
        next_version = self._next_policy_version()
        new_policy = PhasePolicy(
            version=f"phase-v{next_version}",
            phase=request.to_phase,
            advancements_enabled=policy.advancements_enabled,
            approver=policy.approver,
            chunks=dict(policy.chunks),
        )
        policy_file = self.spec_dir / f"phase_policy_v{next_version}.yaml"
        policy_file.write_text(render_policy_yaml(new_policy), encoding="utf-8")
        log_row = self.log.append(
            "execution",
            {
                "request_id": request_id,
                "from_phase": request.from_phase,
                "to_phase": request.to_phase,
                "policy_file": policy_file.name,
                "policy_version": new_policy.version,
                "exit_detail": exit_result.detail,
            },
        )
        _emit_audit(
            self.audit_sink,
            EVT_EXECUTION_SUCCEEDED,
            {
                "summary": f"phase advanced {request.from_phase} -> {request.to_phase}",
                "request_id": request_id,
                "from_phase": request.from_phase,
                "to_phase": request.to_phase,
                "policy_file": policy_file.name,
                "exit_detail": exit_result.detail,
            },
        )
        return ExecuteResult(
            executed=True,
            reasons=(),
            new_phase=request.to_phase,
            policy_file=policy_file,
            log_row=log_row,
        )

    def _refuse(self, request_id: str, reasons: tuple[str, ...]) -> ExecuteResult:
        _emit_audit(
            self.audit_sink,
            EVT_EXECUTION_REFUSED,
            {
                "summary": f"execution refused for {request_id}",
                "request_id": request_id,
                "reasons": list(reasons),
            },
            severity="warning",
        )
        return ExecuteResult(executed=False, reasons=reasons)

    def _next_policy_version(self) -> int:
        versions = [
            int(m.group(1))
            for child in self.spec_dir.iterdir()
            if (m := POLICY_FILE_RE.match(child.name))
        ]
        return (max(versions) if versions else 0) + 1

    # ── revert (recorded; the phase itself moves by config) ─────────

    def record_revert(self, to_phase: int, reason: str, actor: str) -> dict[str, Any]:
        """Record a phase revert in the log + audit signal.

        The phase change itself is a config change (a newer versioned
        phase_policy file with the lower phase) — no code deploy. This
        method only records that it happened, so the revert is as auditable
        as the advancement was.
        """
        row = self.log.append(
            "revert",
            {
                "from_phase": self.current_phase(),
                "to_phase": to_phase,
                "reason": reason,
                "actor": actor,
            },
        )
        _emit_audit(
            self.audit_sink,
            EVT_REVERTED,
            {
                "summary": f"phase reverted to {to_phase}",
                "to_phase": to_phase,
                "reason": reason,
                "actor": actor,
            },
            severity="warning",
        )
        return row


# ─────────────────────────────────────────────────────────────────────
# Heartbeat CLI (the tick the daily systemd timer runs)
# ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    """Heartbeat: evaluate exit criteria, file a request when they pass.

    This is the only caller the phase-advancement machinery needs: the
    daily systemd timer runs the tick, the tick evaluates the current
    phase's mechanical exit criteria against the evidence pointers, and
    files an ``AdvancementRequest`` when they pass.

    Requesting is NOT executing. Filing only records intent in the
    hash-chained log; the phase still moves only through ``execute()``,
    which requires the master switch (``advancements_enabled: true``),
    Michael's approval record, and the criteria re-evaluated on live
    evidence at execution time.

    The resulting state is printed as JSON. Exit code 0 means the
    heartbeat completed (request filed, or nothing to file). Exit code 1
    means the tick could not evaluate (misconfigured policy or unreadable
    evidence) — fail-closed, never an assumed verdict.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Phase-advancement heartbeat: evaluate the current phase's exit "
            "criteria and file an advancement request when they pass. "
            "Requesting is NOT executing."
        )
    )
    parser.add_argument(
        "--spec-dir",
        default=str(SPEC_DIR),
        help="directory holding the phase_policy_v*.yaml files",
    )
    parser.add_argument("--log", default=str(DEFAULT_ADVANCEMENT_LOG))
    parser.add_argument("--audit-sink", default=str(DEFAULT_ADVANCEMENT_AUDIT))
    parser.add_argument(
        "--evidence-pointers",
        default=None,
        help=(
            "JSON file mapping evidence keys to file pointers "
            "(see load_evidence); omitted = empty evidence"
        ),
    )
    parser.add_argument("--requester", default="phase-advancement-tick")
    args = parser.parse_args(argv)

    adv = PhaseAdvancement(
        spec_dir=args.spec_dir, log_path=args.log, audit_sink=args.audit_sink
    )
    try:
        from_phase = adv.current_phase()
    except PhaseAdvancementError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    if from_phase >= MAX_PHASE:
        print(json.dumps({"status": "at-max-phase", "phase": from_phase}))
        return 0

    to_phase = from_phase + 1
    try:
        if args.evidence_pointers:
            with open(args.evidence_pointers, encoding="utf-8") as fh:
                pointers = json.load(fh)
            if not isinstance(pointers, dict):
                raise PhaseAdvancementError(
                    "evidence pointers file must hold a JSON object"
                )
        else:
            pointers = {}
        evidence = load_evidence(pointers)
    except (PhaseAdvancementError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": f"cannot load evidence: {exc}"}))
        return 1

    result = evaluate_exit_criteria(from_phase, to_phase, evidence)
    state: dict[str, Any] = {
        "status": "request-filed" if result.met else "no-request",
        "phase": from_phase,
        "target": to_phase,
        "criteria_met": result.met,
        "checks": result.checks,
        "detail": result.detail,
    }
    if result.met:
        request = adv.request(to_phase, pointers, requester=args.requester)
        state["request_id"] = request.request_id
        state["requester"] = request.requester
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
