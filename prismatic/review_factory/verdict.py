"""Verdict v1 — the replayable record of a review decision.

A Verdict joins three things: the deterministic L1 outcome (authoritative),
the optional L2 judgment (recorded, never re-run), and the mechanical final
outcome (the no-downgrade enforcer lives in ``judge.py`` / ``jev/gates.py``;
this module only *records* the enforced result).

Spec: ``prismatic/review_factory/spec/verdict_v1.md`` (frozen).
Plan ref: ``jev-validation-loop-plan.md`` §2.

Replayability (the core promise): :func:`replay` re-runs the L1
deterministic layer from the stored artifact and must reproduce
``deterministic.verdict`` byte-identically. The judgment is NOT re-run —
the recorded judgment stands; a fresh judgment is a *new* verdict linked
via ``supersedes`` (see :func:`rejudge`).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from prismatic.review_factory.artifact import (
    ReviewArtifact,
    load_artifact,
    store_artifact,
)

SCHEMA_VERSION = "verdict-v1"

DETERMINISTIC_VERDICTS = frozenset({"CLEAN", "REPAIR", "REJECT"})
FINAL_OUTCOMES = frozenset({"CLEAN", "REPAIR", "REJECT", "ESCALATE"})

JUDGE_IDS = frozenset({"jev", "null"})
JUDGMENT_DECISIONS = frozenset({"CLEAR", "PAUSE"})
JUDGMENT_FIELDS = frozenset({"judge", "decision", "confidence", "reasons", "trace_id"})
JUDGMENT_REASON_FIELDS = frozenset({"question", "finding", "severity"})
POLICY_VERSION_FIELDS = frozenset({"deterministic", "calibration", "judge"})
EVIDENCE_POINTER_FIELDS = frozenset({"artifacts", "receipts", "audit_rows"})


class VerdictValidationError(ValueError):
    """Raised when a verdict payload fails fail-closed boundary validation."""


# ─────────────────────────────────────────────────────────────────────
# Dataclass (exactly the §2 verdict-v1 fields, nothing more)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    schema_version: str
    verdict_id: str
    artifact_id: str
    policy_versions: dict[str, Any]
    deterministic_verdict: str
    deterministic_receipt_ids: list[str]
    judgment: dict[str, Any] | None
    final: str
    evidence_pointers: dict[str, list[str]]
    decided_at: str
    judgment_skipped: str | None
    supersedes: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verdict_id": self.verdict_id,
            "artifact_id": self.artifact_id,
            "policy_versions": dict(self.policy_versions),
            "deterministic": {
                "verdict": self.deterministic_verdict,
                "receipt_ids": list(self.deterministic_receipt_ids),
            },
            "judgment": (
                None
                if self.judgment is None
                else {
                    "judge": self.judgment["judge"],
                    "decision": self.judgment["decision"],
                    "confidence": self.judgment["confidence"],
                    "reasons": [
                        {
                            "question": r["question"],
                            "finding": r["finding"],
                            "severity": r["severity"],
                        }
                        for r in self.judgment["reasons"]
                    ],
                    "trace_id": self.judgment["trace_id"],
                }
            ),
            "final": self.final,
            "evidence_pointers": {
                "artifacts": list(self.evidence_pointers["artifacts"]),
                "receipts": list(self.evidence_pointers["receipts"]),
                "audit_rows": list(self.evidence_pointers["audit_rows"]),
            },
            "decided_at": self.decided_at,
            "judgment_skipped": self.judgment_skipped,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Verdict:
        validate_verdict(raw)
        judgment = raw["judgment"]
        return cls(
            schema_version=raw["schema_version"],
            verdict_id=raw["verdict_id"],
            artifact_id=raw["artifact_id"],
            policy_versions=dict(raw["policy_versions"]),
            deterministic_verdict=raw["deterministic"]["verdict"],
            deterministic_receipt_ids=list(raw["deterministic"]["receipt_ids"]),
            judgment=None if judgment is None else dict(judgment),
            final=raw["final"],
            evidence_pointers={
                k: list(raw["evidence_pointers"][k]) for k in EVIDENCE_POINTER_FIELDS
            },
            decided_at=raw["decided_at"],
            judgment_skipped=raw["judgment_skipped"],
            supersedes=raw["supersedes"],
        )


def validate_verdict(raw: Any) -> None:
    """Fail-closed validation of a raw verdict mapping."""
    if not isinstance(raw, dict):
        raise VerdictValidationError("verdict must be a mapping")
    allowed = {
        "schema_version",
        "verdict_id",
        "artifact_id",
        "policy_versions",
        "deterministic",
        "judgment",
        "final",
        "evidence_pointers",
        "decided_at",
        "judgment_skipped",
        "supersedes",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise VerdictValidationError(f"unknown field(s): {sorted(unknown)}")
    missing = allowed - set(raw)
    if missing:
        raise VerdictValidationError(f"missing field(s): {sorted(missing)}")

    if raw["schema_version"] != SCHEMA_VERSION:
        raise VerdictValidationError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {raw['schema_version']!r}"
        )
    try:
        uuid.UUID(raw["verdict_id"], version=4)
    except (ValueError, AttributeError, TypeError):
        raise VerdictValidationError("verdict_id must be a uuid4 string")
    if not isinstance(raw["artifact_id"], str) or not raw["artifact_id"]:
        raise VerdictValidationError("artifact_id must be a non-empty string")

    pv = raw["policy_versions"]
    if not isinstance(pv, dict):
        raise VerdictValidationError("policy_versions must be a mapping")
    unknown_pv = set(pv) - POLICY_VERSION_FIELDS
    if unknown_pv:
        raise VerdictValidationError(
            f"unknown policy_versions field(s): {sorted(unknown_pv)}"
        )
    missing_pv = POLICY_VERSION_FIELDS - set(pv)
    if missing_pv:
        raise VerdictValidationError(
            f"missing policy_versions field(s): {sorted(missing_pv)}"
        )
    for key in POLICY_VERSION_FIELDS:
        if not isinstance(pv[key], str):
            raise VerdictValidationError(f"policy_versions.{key} must be a string")

    det = raw["deterministic"]
    if not isinstance(det, dict):
        raise VerdictValidationError("deterministic must be a mapping")
    if set(det) != {"verdict", "receipt_ids"}:
        raise VerdictValidationError(
            "deterministic must have exactly {verdict, receipt_ids}"
        )
    if det["verdict"] not in DETERMINISTIC_VERDICTS:
        raise VerdictValidationError(
            f"deterministic.verdict must be one of {sorted(DETERMINISTIC_VERDICTS)}"
        )
    if not isinstance(det["receipt_ids"], list) or not all(
        isinstance(r, str) for r in det["receipt_ids"]
    ):
        raise VerdictValidationError(
            "deterministic.receipt_ids must be a list of strings"
        )

    judgment = raw["judgment"]
    if judgment is not None:
        if not isinstance(judgment, dict):
            raise VerdictValidationError("judgment must be a mapping or null")
        if set(judgment) != JUDGMENT_FIELDS:
            raise VerdictValidationError(
                f"judgment must have exactly {sorted(JUDGMENT_FIELDS)}"
            )
        if judgment["judge"] not in JUDGE_IDS:
            raise VerdictValidationError(
                f"judgment.judge must be one of {sorted(JUDGE_IDS)}"
            )
        if judgment["decision"] not in JUDGMENT_DECISIONS:
            raise VerdictValidationError(
                f"judgment.decision must be one of {sorted(JUDGMENT_DECISIONS)}"
            )
        conf = judgment["confidence"]
        if (
            not isinstance(conf, (int, float))
            or isinstance(conf, bool)
            or not 0.0 <= conf <= 1.0
        ):
            raise VerdictValidationError("judgment.confidence must be a number in 0..1")
        reasons = judgment["reasons"]
        if not isinstance(reasons, list) or not all(
            isinstance(r, dict) and set(r) == JUDGMENT_REASON_FIELDS for r in reasons
        ):
            raise VerdictValidationError(
                "judgment.reasons must be a list of "
                "{question, finding, severity} mappings"
            )
        for r in reasons:
            if not all(isinstance(r[k], str) for k in JUDGMENT_REASON_FIELDS):
                raise VerdictValidationError(
                    "judgment.reasons entries must be all strings"
                )
        if not isinstance(judgment["trace_id"], str) or not judgment["trace_id"]:
            raise VerdictValidationError("judgment.trace_id must be non-empty")
        # Judgment only ever runs on deterministic-CLEAN. Recording a
        # judgment on REPAIR/REJECT is a protocol violation.
        if det["verdict"] != "CLEAN":
            raise VerdictValidationError(
                "judgment must be null when deterministic.verdict is not CLEAN"
            )

    if raw["final"] not in FINAL_OUTCOMES:
        raise VerdictValidationError(f"final must be one of {sorted(FINAL_OUTCOMES)}")
    if det["verdict"] in ("REPAIR", "REJECT") and raw["final"] != det["verdict"]:
        raise VerdictValidationError(
            "final must equal deterministic.verdict for REPAIR/REJECT "
            "(judgment can never downgrade or upgrade the deterministic floor)"
        )
    if det["verdict"] == "CLEAN" and raw["final"] not in ("CLEAN", "ESCALATE"):
        raise VerdictValidationError(
            "final must be CLEAN or ESCALATE when deterministic.verdict is CLEAN"
        )
    if raw["final"] == "ESCALATE" and (
        judgment is None or judgment["decision"] != "PAUSE"
    ):
        raise VerdictValidationError(
            "ESCALATE requires a recorded judgment with decision PAUSE"
        )

    ep = raw["evidence_pointers"]
    if not isinstance(ep, dict) or set(ep) != EVIDENCE_POINTER_FIELDS:
        raise VerdictValidationError(
            f"evidence_pointers must have exactly {sorted(EVIDENCE_POINTER_FIELDS)}"
        )
    for key in EVIDENCE_POINTER_FIELDS:
        if not isinstance(ep[key], list) or not all(
            isinstance(v, str) for v in ep[key]
        ):
            raise VerdictValidationError(
                f"evidence_pointers.{key} must be a list of strings"
            )

    if not isinstance(raw["decided_at"], str) or not raw["decided_at"]:
        raise VerdictValidationError("decided_at must be a non-empty string")
    if raw["judgment_skipped"] is not None and not isinstance(
        raw["judgment_skipped"], str
    ):
        raise VerdictValidationError("judgment_skipped must be a string or null")
    if judgment is not None and raw["judgment_skipped"] is not None:
        raise VerdictValidationError(
            "judgment_skipped must be null when a judgment was recorded"
        )
    if raw["supersedes"] is not None:
        try:
            uuid.UUID(raw["supersedes"], version=4)
        except (ValueError, AttributeError, TypeError):
            raise VerdictValidationError("supersedes must be a uuid4 string or null")


# ─────────────────────────────────────────────────────────────────────
# The L1 deterministic layer (reference runner; workstream C wires the
# real verifier/novelty/tiering stack as the registered runner)
# ─────────────────────────────────────────────────────────────────────

#: A deterministic runner maps an artifact to its L1 outcome:
#: ``{"verdict": "CLEAN"|"REPAIR"|"REJECT", "receipt_ids": [...]}``.
DeterministicRunner = Callable[[ReviewArtifact], dict[str, Any]]

_runner: DeterministicRunner | None = None


def register_runner(fn: DeterministicRunner) -> None:
    """Register the L1 deterministic runner used by :func:`replay`.

    Called once at wiring time (workstream C). Tests pass their runner
    explicitly to :func:`replay` instead.
    """
    global _runner
    _runner = fn


def default_deterministic_runner(artifact: ReviewArtifact) -> dict[str, Any]:
    """Reference derivation of the L1 outcome from the artifact alone.

    This is the portable floor any harness can reproduce without the full
    verifier stack:

    * no checks recorded → ``REJECT`` (nothing was verified — fail closed);
    * any check with a non-zero exit code → ``REPAIR`` (the repair loop's
      input: failures get fixed and re-verified, never waved through);
    * all checks exit 0 → ``CLEAN``.

    ``receipt_ids`` are the artifact's ``prior_receipts`` — the verification
    attempts this outcome rests on.
    """
    if not artifact.checks:
        verdict = "REJECT"
    elif any(c.exit_code != 0 for c in artifact.checks):
        verdict = "REPAIR"
    else:
        verdict = "CLEAN"
    return {"verdict": verdict, "receipt_ids": list(artifact.prior_receipts)}


def _canonical_deterministic(det: dict[str, Any]) -> str:
    """Byte-identical canonical form of a deterministic outcome block."""
    block = {"verdict": det["verdict"], "receipt_ids": list(det["receipt_ids"])}
    return json.dumps(block, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ─────────────────────────────────────────────────────────────────────
# decide / rejudge / replay
# ─────────────────────────────────────────────────────────────────────


def decide(
    artifact: ReviewArtifact,
    *,
    deterministic: dict[str, Any] | None = None,
    judgment: dict[str, Any] | None = None,
    judgment_skipped: str | None = None,
    policy_versions: dict[str, Any] | None = None,
    decided_at: str | None = None,
    supersedes: str | None = None,
) -> Verdict:
    """Record a verdict for an artifact and store it (write-once).

    * ``deterministic`` defaults to :func:`default_deterministic_runner`.
    * Judgment is fail-closed: a non-null ``judgment`` with a
      non-CLEAN deterministic verdict raises; a ``PAUSE`` decision on a
      CLEAN deterministic verdict escalates ``final`` to ``ESCALATE``.
      Judgment can never downgrade or override the deterministic floor.
    """
    store_artifact(artifact)  # idempotent; the artifact is the join key
    det = (
        deterministic
        if deterministic is not None
        else default_deterministic_runner(artifact)
    )
    if set(det) != {"verdict", "receipt_ids"}:
        raise VerdictValidationError(
            "deterministic must be exactly {verdict, receipt_ids}"
        )
    if det["verdict"] not in DETERMINISTIC_VERDICTS:
        raise VerdictValidationError(
            f"deterministic.verdict must be one of {sorted(DETERMINISTIC_VERDICTS)}"
        )
    if det["verdict"] != "CLEAN" and judgment is not None:
        raise VerdictValidationError(
            "judgment is only recorded on deterministic-CLEAN; "
            "L2 never runs on REPAIR/REJECT"
        )
    if det["verdict"] == "CLEAN":
        final = "ESCALATE" if (judgment or {}).get("decision") == "PAUSE" else "CLEAN"
    else:
        final = det["verdict"]

    verdict = Verdict(
        schema_version=SCHEMA_VERSION,
        verdict_id=uuid.uuid4().hex,
        artifact_id=artifact.artifact_id,
        policy_versions=(
            dict(policy_versions)
            if policy_versions is not None
            else {
                "deterministic": "deterministic-reference-v1",
                "calibration": "merge_bar_calibration_v1",
                "judge": "null" if judgment is None else judgment.get("judge", "jev"),
            }
        ),
        deterministic_verdict=det["verdict"],
        deterministic_receipt_ids=list(det["receipt_ids"]),
        judgment=None if judgment is None else dict(judgment),
        final=final,
        evidence_pointers={
            "artifacts": [artifact.artifact_id],
            "receipts": list(det["receipt_ids"]),
            "audit_rows": [],
        },
        decided_at=(
            decided_at
            if decided_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        judgment_skipped=judgment_skipped,
        supersedes=supersedes,
    )
    validate_verdict(verdict.to_dict())
    store_verdict(verdict)
    return verdict


def rejudge(
    verdict_id: str,
    judgment: dict[str, Any],
    *,
    policy_versions: dict[str, Any] | None = None,
    decided_at: str | None = None,
) -> Verdict:
    """Record a fresh judgment as a NEW verdict linked via ``supersedes``.

    The recorded judgment is never re-run or rewritten — a fresh judgment
    is a new verdict. The old verdict stays stored untouched (write-once).
    """
    old = load_verdict(verdict_id)
    if old.deterministic_verdict != "CLEAN":
        raise VerdictValidationError(
            "rejudge is only meaningful on a deterministic-CLEAN verdict"
        )
    artifact = load_artifact(old.artifact_id)
    det = {
        "verdict": old.deterministic_verdict,
        "receipt_ids": list(old.deterministic_receipt_ids),
    }
    return decide(
        artifact,
        deterministic=det,
        judgment=judgment,
        judgment_skipped=None,
        policy_versions=policy_versions,
        decided_at=decided_at,
        supersedes=old.verdict_id,
    )


@dataclass(frozen=True)
class ReplayResult:
    verdict_id: str
    artifact_id: str
    reproduced: bool
    expected_canonical: str
    actual_canonical: str


def replay(verdict_id: str, runner: DeterministicRunner | None = None) -> ReplayResult:
    """Re-run the L1 deterministic layer from the stored artifact.

    Must reproduce ``deterministic.verdict`` byte-identically (compared on
    the canonical JSON form). The judgment is NOT re-run — the recorded
    judgment stands.
    """
    verdict = load_verdict(verdict_id)
    artifact = load_artifact(verdict.artifact_id)
    run = runner if runner is not None else _runner
    if run is None:
        # No explicit or registered runner: fall back to the portable
        # reference floor. Workstream C registers the real verifier /
        # novelty / tiering stack here.
        run = default_deterministic_runner
    fresh = run(artifact)
    if set(fresh) != {"verdict", "receipt_ids"}:
        raise VerdictValidationError(
            "runner must return exactly {verdict, receipt_ids}"
        )
    expected = _canonical_deterministic(
        {
            "verdict": verdict.deterministic_verdict,
            "receipt_ids": verdict.deterministic_receipt_ids,
        }
    )
    actual = _canonical_deterministic(fresh)
    return ReplayResult(
        verdict_id=verdict.verdict_id,
        artifact_id=verdict.artifact_id,
        reproduced=(expected == actual),
        expected_canonical=expected,
        actual_canonical=actual,
    )


# ─────────────────────────────────────────────────────────────────────
# Write-once JSONL store
# ─────────────────────────────────────────────────────────────────────


def _audit_dir() -> str:
    return os.environ.get(
        "PRISMATIC_AUDIT_DIR", os.path.expanduser("~/.prismatic/audit")
    )


def _verdict_store_path() -> str:
    return os.path.join(_audit_dir(), "review-verdicts.jsonl")


def store_verdict(verdict: Verdict) -> str:
    """Append a verdict to the store. Write-once: re-storing the same
    ``verdict_id`` is a no-op. Returns the ``verdict_id``."""
    validate_verdict(verdict.to_dict())
    path = _verdict_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if verdict_exists(verdict.verdict_id):
        return verdict.verdict_id
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(verdict.to_dict(), sort_keys=True, ensure_ascii=True) + "\n"
        )
    return verdict.verdict_id


def verdict_exists(verdict_id: str) -> bool:
    path = _verdict_store_path()
    if not os.path.exists(path):
        return False
    needle = json.dumps(verdict_id).encode("utf-8")
    with open(path, "rb") as fh:
        for line in fh:
            if needle in line:
                try:
                    if json.loads(line)["verdict_id"] == verdict_id:
                        return True
                except (json.JSONDecodeError, KeyError):
                    continue
    return False


def load_verdict(verdict_id: str) -> Verdict:
    path = _verdict_store_path()
    if not os.path.exists(path):
        raise KeyError(f"verdict not found: {verdict_id}")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("verdict_id") == verdict_id:
                return Verdict.from_dict(raw)
    raise KeyError(f"verdict not found: {verdict_id}")


def verdicts_for_artifact(artifact_id: str) -> list[Verdict]:
    """All stored verdicts for an artifact (oldest first), including superseded."""
    path = _verdict_store_path()
    out: list[Verdict] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("artifact_id") == artifact_id:
                out.append(Verdict.from_dict(raw))
    return out
