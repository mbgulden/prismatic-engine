"""Canonical execution evidence contract for Prismatic-controlled flows.

The contract is intentionally stdlib-only and serializable. It gives agents,
smoke scripts, dashboards, and Linear comments one shared language for whether a
result is verified, partially verified, blocked, failed, or merely self-report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class VerificationScope(str, Enum):
    """How broad the verification claim is."""

    AD_HOC_TARGETED = "ad_hoc_targeted"
    CANONICAL_FULL_SUITE = "canonical_full_suite"
    LIVE_INTEGRATION = "live_integration"
    NOT_RUN = "not_run"


class VerificationStatus(str, Enum):
    """Allowed final evidence verdicts."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    BLOCKED = "blocked"
    FAILED = "failed"
    SELF_REPORTED = "self_reported"


class FailureCategory(str, Enum):
    """Standard failure taxonomy for agent/proof-loop runs."""

    NONE = "none"
    TIMEOUT = "timeout"
    BLOCKED_EXTERNAL_API = "blocked_external_api"
    BLOCKED_MISSING_CONTEXT = "blocked_missing_context"
    VERIFICATION_FAILED = "verification_failed"
    CONFLICT = "conflict"
    HALLUCINATED_CLAIM = "hallucinated_claim"
    TOOLING_ERROR = "tooling_error"


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    exit_code: int | None
    scope: VerificationScope
    output_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        return data


@dataclass(frozen=True)
class ExecutionEvidence:
    task_id: str
    run_id: str
    status: VerificationStatus
    scope: VerificationScope
    summary: str
    commands: list[CommandEvidence] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    external_side_effects: list[str] = field(default_factory=list)
    cleanup_status: str = "not_reported"
    failure_category: FailureCategory = FailureCategory.NONE
    blocker: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "scope": self.scope.value,
            "summary": self.summary,
            "commands": [command.to_dict() for command in self.commands],
            "artifacts": list(self.artifacts),
            "files_changed": list(self.files_changed),
            "external_side_effects": list(self.external_side_effects),
            "cleanup_status": self.cleanup_status,
            "failure_category": self.failure_category.value,
            "blocker": self.blocker,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionEvidence:
        return cls(
            task_id=str(data.get("task_id", "")),
            run_id=str(data.get("run_id", "")),
            status=VerificationStatus(
                str(data.get("status", VerificationStatus.SELF_REPORTED.value))
            ),
            scope=VerificationScope(
                str(data.get("scope", VerificationScope.NOT_RUN.value))
            ),
            summary=str(data.get("summary", "")),
            commands=[
                CommandEvidence(
                    command=str(item.get("command", "")),
                    exit_code=item.get("exit_code"),
                    scope=VerificationScope(
                        str(item.get("scope", VerificationScope.NOT_RUN.value))
                    ),
                    output_excerpt=str(item.get("output_excerpt", "")),
                )
                for item in data.get("commands", [])
            ],
            artifacts=[str(item) for item in data.get("artifacts", [])],
            files_changed=[str(item) for item in data.get("files_changed", [])],
            external_side_effects=[
                str(item) for item in data.get("external_side_effects", [])
            ],
            cleanup_status=str(data.get("cleanup_status", "not_reported")),
            failure_category=FailureCategory(
                str(data.get("failure_category", FailureCategory.NONE.value))
            ),
            blocker=str(data.get("blocker", "")),
            created_at=str(
                data.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
        )


def validate_evidence(evidence: ExecutionEvidence) -> list[str]:
    """Return contract violations. Empty list means evidence is acceptable."""

    errors: list[str] = []
    if not evidence.task_id.strip():
        errors.append("task_id is required")
    if not evidence.run_id.strip():
        errors.append("run_id is required")
    if not evidence.summary.strip():
        errors.append("summary is required")
    if not evidence.cleanup_status.strip() or evidence.cleanup_status == "not_reported":
        errors.append("cleanup_status is required")

    if evidence.status == VerificationStatus.VERIFIED:
        if evidence.scope == VerificationScope.NOT_RUN:
            errors.append("verified evidence cannot use not_run scope")
        if not evidence.commands and not evidence.artifacts:
            errors.append("verified evidence requires commands or artifacts")
        if evidence.failure_category != FailureCategory.NONE:
            errors.append("verified evidence cannot carry a failure_category")
        if evidence.blocker:
            errors.append("verified evidence cannot carry a blocker")

    if evidence.status == VerificationStatus.PARTIALLY_VERIFIED:
        if not evidence.commands and not evidence.artifacts:
            errors.append(
                "partially_verified evidence requires at least one command or artifact"
            )
        if not evidence.blocker:
            errors.append("partially_verified evidence requires remaining blocker text")

    if evidence.status == VerificationStatus.BLOCKED:
        if not evidence.blocker:
            errors.append("blocked evidence requires blocker text")
        if evidence.failure_category == FailureCategory.NONE:
            errors.append("blocked evidence requires non-none failure_category")

    if evidence.status == VerificationStatus.FAILED:
        if evidence.failure_category == FailureCategory.NONE:
            errors.append("failed evidence requires non-none failure_category")
        if not evidence.commands and not evidence.artifacts:
            errors.append("failed evidence requires command or artifact context")

    if evidence.status == VerificationStatus.SELF_REPORTED:
        errors.append("self_reported is not acceptable as Done evidence")

    return errors


def done_gate(
    status_label: str, evidence: ExecutionEvidence | None
) -> tuple[Literal["done", "not_done"], list[str]]:
    """Gate task completion so Done cannot be inferred from self-report alone."""

    wants_done = status_label.strip().lower() in {"done", "completed", "complete"}
    if not wants_done:
        return "not_done", []
    if evidence is None:
        return "not_done", ["Done requires execution evidence"]
    errors = validate_evidence(evidence)
    if errors:
        return "not_done", errors
    if evidence.status != VerificationStatus.VERIFIED:
        return "not_done", [
            f"Done requires verified evidence, got {evidence.status.value}"
        ]
    return "done", []


def write_evidence(path: str | Path, evidence: ExecutionEvidence) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(evidence.to_json(), encoding="utf-8")


def read_evidence(path: str | Path) -> ExecutionEvidence:
    return ExecutionEvidence.from_dict(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
