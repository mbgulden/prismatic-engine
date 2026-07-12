"""Execution proof scoring for Prismatic Engine worker runs.

The module is intentionally side-effect free: callers pass in the worker run,
artifact, and Linear evidence they collected elsewhere. The scorer answers one
question: did a dispatched issue become real execution with durable artifacts
and the expected Linear state/label sync?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence


class ProofStatus(str, Enum):
    """Coarse execution-proof status."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class WorkerRunEvidence:
    """Evidence emitted by the worker/session launcher."""

    issue_id: str
    lane: str
    run_id: str | None = None
    session_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    status: str | None = None
    result_artifacts: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "WorkerRunEvidence":
        """Build evidence from a loose run-record mapping.

        The dispatcher/supervisor has accumulated several record shapes over
        time. This accepts common aliases so proof checks can sit at the
        boundary instead of hard-coding one telemetry schema.
        """

        def first_text(*keys: str) -> str | None:
            for key in keys:
                value = payload.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
            return None

        raw_artifacts = (
            payload.get("result_artifacts") or payload.get("artifacts") or ()
        )
        if isinstance(raw_artifacts, (str, Path)):
            artifacts = (str(raw_artifacts),)
        elif isinstance(raw_artifacts, Iterable):
            artifacts = tuple(str(item) for item in raw_artifacts)
        else:
            artifacts = ()

        exit_value = payload.get("exit_code")
        exit_code = int(str(exit_value)) if exit_value is not None else None

        return cls(
            issue_id=first_text("issue_id", "issue", "identifier") or "",
            lane=first_text("lane", "agent", "agent_label") or "",
            run_id=first_text("run_id", "id"),
            session_id=first_text("session_id", "session"),
            started_at=first_text("started_at", "launched_at", "created_at"),
            completed_at=first_text("completed_at", "finished_at", "ended_at"),
            exit_code=exit_code,
            status=first_text("status", "state"),
            result_artifacts=artifacts,
        )


@dataclass(frozen=True)
class LinearSyncEvidence:
    """Evidence that Linear was updated after execution."""

    issue_id: str
    final_state: str | None = None
    labels: tuple[str, ...] = ()
    final_comment_id: str | None = None
    final_comment_body: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "LinearSyncEvidence":
        raw_labels = payload.get("labels") or ()
        if isinstance(raw_labels, str):
            labels = (raw_labels,)
        elif isinstance(raw_labels, Iterable):
            labels = tuple(str(item) for item in raw_labels)
        else:
            labels = ()
        return cls(
            issue_id=str(payload.get("issue_id") or payload.get("identifier") or ""),
            final_state=str(payload["final_state"])
            if payload.get("final_state")
            else None,
            labels=labels,
            final_comment_id=str(payload["final_comment_id"])
            if payload.get("final_comment_id")
            else None,
            final_comment_body=str(payload["final_comment_body"])
            if payload.get("final_comment_body")
            else None,
        )


@dataclass(frozen=True)
class ExecutionProofReport:
    """Structured result for proof gates and dashboards."""

    issue_id: str
    lane: str
    status: ProofStatus
    passed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    trace: dict[str, str | int | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report."""

        return {
            "issue_id": self.issue_id,
            "lane": self.lane,
            "status": self.status.value,
            "passed": list(self.passed),
            "warnings": list(self.warnings),
            "failures": list(self.failures),
            "trace": dict(self.trace),
        }


def _artifact_exists(path_text: str, *, root: Path | None) -> bool:
    path = Path(path_text).expanduser()
    if not path.is_absolute() and root is not None:
        path = root / path
    return path.exists() and (path.is_file() or path.is_dir())


def assess_execution_proof(
    run: WorkerRunEvidence,
    linear: LinearSyncEvidence,
    *,
    artifact_root: str | Path | None = None,
    terminal_states: Sequence[str] = ("In Review", "Done", "Done - Doc Pending"),
    completion_labels: Sequence[str] = ("agent:done",),
    require_comment_body_evidence: bool = True,
) -> ExecutionProofReport:
    """Score a dispatched issue's end-to-end execution proof.

    A passing proof requires launch identity, completion identity, at least one
    durable artifact on disk, successful worker exit, a final Linear comment,
    and a Linear terminal state or completion label. Warnings are reserved for
    recoverable sync gaps; missing run/artifact/comment evidence is a failure.
    """

    passed: list[str] = []
    warnings: list[str] = []
    failures: list[str] = []
    root = Path(artifact_root).expanduser() if artifact_root is not None else None

    if run.issue_id and linear.issue_id and run.issue_id == linear.issue_id:
        passed.append("issue identity matches worker run and Linear sync")
    else:
        failures.append("issue identity mismatch between worker run and Linear sync")

    if run.lane:
        passed.append("worker lane recorded")
    else:
        failures.append("worker lane missing")

    if run.run_id or run.session_id:
        passed.append("worker/session launch id recorded")
    else:
        failures.append("worker/session launch id missing")

    if run.started_at:
        passed.append("worker start timestamp recorded")
    else:
        failures.append("worker start timestamp missing")

    if run.completed_at:
        passed.append("worker completion timestamp recorded")
    else:
        failures.append("worker completion timestamp missing")

    if run.exit_code == 0 or (
        run.exit_code is None
        and str(run.status or "").lower() in {"completed", "done", "success"}
    ):
        passed.append("worker completed successfully")
    else:
        failures.append("worker success exit/status missing")

    existing_artifacts = [
        path for path in run.result_artifacts if _artifact_exists(path, root=root)
    ]
    if existing_artifacts:
        passed.append("result artifact exists")
    elif run.result_artifacts:
        failures.append("result artifact path recorded but missing on disk")
    else:
        failures.append("result artifact missing")

    if linear.final_comment_id:
        passed.append("Linear final evidence comment recorded")
    else:
        failures.append("Linear final evidence comment missing")

    if require_comment_body_evidence:
        body = (linear.final_comment_body or "").lower()
        issue_token = run.issue_id.lower()
        artifact_hit = any(
            Path(path).name.lower() in body or path.lower() in body
            for path in run.result_artifacts
        )
        if issue_token and issue_token in body and artifact_hit:
            passed.append("Linear comment links issue and artifact evidence")
        else:
            warnings.append(
                "Linear comment body does not link issue and artifact evidence"
            )

    labels = set(linear.labels)
    terminal_state_hit = linear.final_state in set(terminal_states)
    completion_label_hit = bool(labels.intersection(completion_labels))
    if terminal_state_hit or completion_label_hit:
        passed.append("Linear state/label is terminal for completed work")
    else:
        warnings.append("Linear state/labels are not terminal for completed work")

    status = (
        ProofStatus.FAIL
        if failures
        else ProofStatus.WARN
        if warnings
        else ProofStatus.PASS
    )
    return ExecutionProofReport(
        issue_id=run.issue_id or linear.issue_id,
        lane=run.lane,
        status=status,
        passed=tuple(passed),
        warnings=tuple(warnings),
        failures=tuple(failures),
        trace={
            "run_id": run.run_id,
            "session_id": run.session_id,
            "exit_code": run.exit_code,
            "final_state": linear.final_state,
            "final_comment_id": linear.final_comment_id,
            "artifact_count": len(existing_artifacts),
        },
    )


__all__ = [
    "ExecutionProofReport",
    "LinearSyncEvidence",
    "ProofStatus",
    "WorkerRunEvidence",
    "assess_execution_proof",
]
