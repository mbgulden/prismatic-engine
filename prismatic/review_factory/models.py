"""Canonical record schemas for the Review/Merge Factory V1.

This module IS the spec freeze. The five tables defined here correspond
exactly to §5 of okf-review-factory-v1.md:

    1. review_jobs           — §5.1
    2. verification_receipts — §5.2
    3. review_decisions      — §5.3
    4. merge_authorizations  — §5.4
    5. repair_packets        — §5.5

Every field, type, and constraint is authoritative. If this file
disagrees with the OKF prose, this file wins (code IS the spec).

State transitions for review_jobs are idempotent.  See
``ReviewJobState.valid_transitions()`` for the canonical graph.
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import timedelta, datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────


class ReviewJobState(enum.Enum):
    """All valid states for a review job.

    Transitions are idempotent — re-applying the same transition is a
    no-op, not an error.
    """

    QUEUED = "queued"
    VERIFYING = "verifying"
    REVIEW_READY = "review_ready"
    REVIEWING = "reviewing"
    REPAIR_REQUIRED = "repair_required"
    REJECTED = "rejected"
    MERGE_READY = "merge_ready"
    MERGE_AUTHORIZED = "merge_authorized"
    MERGING = "merging"
    MERGED = "merged"
    MERGE_VERIFICATION_FAILED = "merge_verification_failed"

    @staticmethod
    def valid_transitions() -> dict[str, list[str]]:
        """Return the canonical state transition graph.

        Keys are source states, values are lists of valid target states.
        """
        S = ReviewJobState
        return {
            S.QUEUED.value: [S.VERIFYING.value],
            S.VERIFYING.value: [
                S.REVIEW_READY.value,
                S.QUEUED.value,
                S.REPAIR_REQUIRED.value,  # verification checks failed -> producer rework
            ],
            S.REVIEW_READY.value: [
                S.REVIEWING.value,
                S.REPAIR_REQUIRED.value,
                S.QUEUED.value,  # requeue for re-verification (e.g. manifest not persisted)
            ],
            S.REVIEWING.value: [
                S.MERGE_READY.value,
                S.REPAIR_REQUIRED.value,
                S.REJECTED.value,
                S.REVIEW_READY.value,
            ],
            S.REPAIR_REQUIRED.value: [S.QUEUED.value],
            S.REJECTED.value: [],  # terminal
            S.MERGE_READY.value: [S.MERGE_AUTHORIZED.value, S.REPAIR_REQUIRED.value],
            S.MERGE_AUTHORIZED.value: [S.MERGING.value],
            S.MERGING.value: [S.MERGED.value, S.MERGE_VERIFICATION_FAILED.value],
            S.MERGED.value: [],  # terminal
            S.MERGE_VERIFICATION_FAILED.value: [],  # terminal (manual recovery)
        }

    def can_transition_to(self, target: ReviewJobState) -> bool:
        """Check whether *target* is a valid successor of this state."""
        return target.value in self.valid_transitions().get(self.value, [])


class RiskTier(enum.IntEnum):
    """Risk classification for changed paths.

    Tier 0 — deterministic-only (docs, fixtures, generated).
    Tier 1 — standard code changes (default).
    Tier 2 — auth, migrations, SQLite, git mutation, factory-self.
    Tier 3 — production authority (deploy, systemd, credentials).
    """

    DETERMINISTIC_ONLY = 0
    STANDARD = 1
    SENSITIVE = 2
    PRODUCTION = 3


class VerificationClassification(enum.Enum):
    """What kind of verification was performed."""

    TARGETED = "targeted"
    BOUNDED_REGRESSION = "bounded_regression"
    CANONICAL_SUITE = "canonical_suite"
    PACKAGE_WHEEL = "package_wheel"
    BROWSER = "browser"
    PRODUCTION = "production"


class ReviewVerdict(enum.Enum):
    """Reviewer's verdict on a candidate."""

    CLEAN = "clean"
    REPAIR_REQUIRED = "repair_required"
    REJECTED = "rejected"


class MergeScope(enum.Enum):
    """How the merge was authorized."""

    TIER_0_AUTO = "tier-0-auto"
    TIER_1_AUTO = "tier-1-auto"
    TIER_2_EXCEPTION = "tier-2-exception"
    TIER_3_EXCEPTION = "tier-3-exception"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _new_uuid() -> str:
    """Generate a new UUID v4 as a string."""
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Current UTC timestamp (timezone-aware)."""
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """Current UTC timestamp as ISO 8601 string."""
    return _utcnow().isoformat()


# ─────────────────────────────────────────────────────────────────────
# Schema 1: review_jobs (§5.1)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ReviewJob:
    """A single candidate flowing through the review factory.

    Corresponds to one row in the ``review_jobs`` table.
    The ``state`` field drives the state machine; all transitions
    must go through ``transition_to()``.
    """

    # Identity
    review_job_id: str = field(default_factory=_new_uuid)
    completed_work_id: str = ""  # FK → agy_completed_work UUID
    task_id: str = ""  # e.g. GRO-4188

    # Repository binding
    repository: str = ""  # e.g. mbgulden/prismatic-engine
    base_commit: str = ""  # SHA
    base_tree: str = ""  # SHA
    candidate_commit: str = ""  # SHA
    candidate_tree: str = ""  # SHA

    # Result packet
    result_packet_path: str = ""
    result_packet_sha256: str = ""

    # Classification
    changed_paths_json: str = "[]"  # JSON list of changed paths
    risk_tier: int = RiskTier.STANDARD
    policy_version: str = "v1"

    # State machine
    state: str = ReviewJobState.QUEUED.value

    # Witness tracking
    required_witnesses: int = 0
    completed_witnesses: int = 0

    # Timestamps
    created_at: str = field(default_factory=_utcnow_iso)

    # Lease
    lease_owner: str = ""
    lease_expires_at: str = ""

    # Persisted pipeline manifest (canonical JSON of MergeCandidateManifest).
    # Written by the verification stage once it reaches REVIEW_REQUIRED and
    # consumed by the review stage; Phase 4's merge executor binds the CLEAN
    # manifest from here as well.
    manifest_json: str = ""

    @property
    def changed_paths(self) -> list[str]:
        """Parse changed_paths_json into a list."""
        return json.loads(self.changed_paths_json) if self.changed_paths_json else []

    @changed_paths.setter
    def changed_paths(self, paths: list[str]) -> None:
        self.changed_paths_json = json.dumps(paths)

    def transition_to(self, target: ReviewJobState) -> None:
        """Idempotent state transition.

        Raises ``ValueError`` if the transition is invalid.
        Idempotent: transitioning to the current state is a no-op.
        """
        current = ReviewJobState(self.state)
        if current == target:
            return  # idempotent
        if not current.can_transition_to(target):
            raise ValueError(
                f"Invalid state transition: {current.value} → {target.value}. "
                f"Valid targets: {current.valid_transitions().get(current.value, [])}"
            )
        self.state = target.value


# ─────────────────────────────────────────────────────────────────────
# Schema 2: verification_receipts (§5.2)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class VerificationReceipt:
    """Durable proof of what was verified and how.

    Immutable after creation — no update method provided.
    """

    receipt_id: str = field(default_factory=_new_uuid)
    review_job_id: str = ""  # FK

    # Commit binding (must match review_jobs row exactly)
    candidate_commit: str = ""
    candidate_tree: str = ""

    # Archive identity
    immutable_archive_id: str = ""

    # Execution evidence (all JSON)
    commands: str = "[]"
    exit_codes: str = "{}"
    log_paths: str = "{}"
    log_sha256: str = "{}"

    # Side-effect proof
    changed_path_invariance_proof: str = ""

    # Classification
    classification: str = VerificationClassification.TARGETED.value

    # Explicit non-claims (honesty about what was NOT tested)
    explicit_non_claims: str = "[]"

    # Baseline failures (never call baseline rot "canonical green")
    baseline_failures: str = "[]"

    # Timestamp
    created_at: str = field(default_factory=_utcnow_iso)

    def recompute_provenance_hash(self, repository: str, policy_version: str) -> str:
        """Recompute the content-addressed provenance record from stored evidence."""
        cmds = json.loads(self.commands)
        exits = json.loads(self.exit_codes)
        logs = json.loads(self.log_sha256)
        sorted_commands = sorted(cmds)
        sorted_exits = sorted(f"{k}:{v}" for k, v in exits.items())
        sorted_logs = sorted(f"{k}:{v}" for k, v in logs.items())
        archive_id = self.immutable_archive_id
        if archive_id.startswith("sha256:"):
            archive_sha = archive_id[7:]
        else:
            archive_sha = archive_id
        parts = [
            repository,
            self.candidate_commit,
            self.candidate_tree,
            ",".join(sorted_commands),
            policy_version,
            ",".join(sorted_exits),
            f"sha256:{archive_sha}",
            ",".join(sorted_logs),
        ]
        raw = "\0".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Schema 3: review_decisions (§5.3)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    """A single review finding attached to a decision."""

    severity: str = "info"  # info, warning, error, critical
    path: str = ""
    line: int = 0
    invariant: str = ""
    reproduction_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "invariant": self.invariant,
            "reproduction_command": self.reproduction_command,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Finding:
        return cls(
            severity=d.get("severity", "info"),
            path=d.get("path", ""),
            line=d.get("line", 0),
            invariant=d.get("invariant", ""),
            reproduction_command=d.get("reproduction_command", ""),
        )


@dataclass
class ReviewDecision:
    """A reviewer's verdict on a candidate.

    The ``idempotency_key`` is computed automatically from
    ``reviewer_id``, ``candidate_tree``, and ``verdict``.
    """

    decision_id: str = field(default_factory=_new_uuid)
    review_job_id: str = ""  # FK

    # Reviewer identity
    reviewer_id: str = ""
    reviewer_capability_version: str = ""

    # Commit binding
    candidate_commit: str = ""
    candidate_tree: str = ""

    # Receipt binding
    receipt_id: str = ""  # FK → verification_receipts

    # Verdict
    verdict: str = ReviewVerdict.CLEAN.value
    findings: str = "[]"  # JSON list of Finding dicts

    # Idempotency
    idempotency_key: str = ""

    # Timestamp
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = self.compute_idempotency_key()

    def compute_idempotency_key(self) -> str:
        """sha256(reviewer_id + candidate_tree + verdict)."""
        raw = f"{self.reviewer_id}{self.candidate_tree}{self.verdict}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def finding_objects(self) -> list[Finding]:
        """Parse findings JSON into Finding objects."""
        raw = json.loads(self.findings) if self.findings else []
        return [Finding.from_dict(f) for f in raw]

    @finding_objects.setter
    def finding_objects(self, findings: list[Finding]) -> None:
        self.findings = json.dumps([f.to_dict() for f in findings])


# ─────────────────────────────────────────────────────────────────────
# Schema 4: merge_authorizations (§5.4)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class MergeAuthorization:
    """Explicit, versioned merge authority.

    Review completion alone NEVER implies merge authority.
    These are separate records.
    """

    authorization_id: str = field(default_factory=_new_uuid)
    review_job_id: str = ""  # FK

    # PR binding
    repository: str = ""
    pr_number: int = 0
    pr_head_commit: str = ""
    pr_base_commit: str = ""
    candidate_tree: str = ""
    expected_merge_tree: str = ""

    # Policy
    policy_version: str = "v1"
    actor: str = ""  # who authorized (user, capability, or "standing-policy: tier-N")
    scope: str = MergeScope.TIER_1_AUTO.value

    # Lifecycle
    expires_at: str = ""  # UTC ISO 8601
    consumed_at: str = ""  # UTC ISO 8601, empty until merge executes

    # Idempotency
    idempotency_key: str = ""

    @property
    def is_consumed(self) -> bool:
        return bool(self.consumed_at)

    @property
    def is_expired(self) -> bool:
        # Fail closed: any missing, malformed, or non-UTC expiry is expired.
        if not self.expires_at:
            return True
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return True
        if exp.tzinfo is None:
            return True
        if exp.utcoffset() != timedelta(0):
            return True
        return _utcnow() > exp

    def consume(self) -> None:
        """Mark this authorization as consumed. Idempotent."""
        if not self.consumed_at:
            self.consumed_at = _utcnow_iso()


# ─────────────────────────────────────────────────────────────────────
# Schema 5: repair_packets (§5.5)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RepairPacket:
    """Structured repair request sent back to the producer.

    The producer consumes this via the existing agy_completed_work
    intake path.  This avoids creating a "bespoke manual review
    project" (per the OKF's explicit goal).
    """

    packet_id: str = field(default_factory=_new_uuid)
    review_job_id: str = ""  # FK to review_jobs
    candidate_tree: str = ""
    candidate_attempt: int = 1

    # Findings from the review decision
    findings_json: str = "[]"

    # Producer identity
    producer_id: str = ""  # which agent produced (Ned/AGY/Jules)

    # Lifecycle
    consumed_at: str = ""  # UTC, nullable
    resolution_attempt_n: int = 0  # counter — prevent infinite loops

    # Timestamp
    created_at: str = field(default_factory=_utcnow_iso)

    @property
    def is_consumed(self) -> bool:
        return bool(self.consumed_at)

    def consume(self) -> None:
        """Mark as consumed. Idempotent."""
        if not self.consumed_at:
            self.consumed_at = _utcnow_iso()

    def increment_attempt(self) -> None:
        """Bump the resolution attempt counter."""
        self.resolution_attempt_n += 1
