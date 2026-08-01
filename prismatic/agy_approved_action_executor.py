"""Approved operator action executor dry-run ledger.

This module is the final dry-run bridge after an operator action approval. It
materializes the exact executor request that *would* run while keeping every
real side effect disabled by default. It does not create GitHub PRs, post Linear
comments, enable auto-merge, dispatch agents, run overnight autopilot, or deploy
production.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_operator_action_approval import (
    get_operator_action_approval,
    latest_or_record_operator_action_approval,
    list_operator_action_approvals,
)

ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER = (
    "ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_OK"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_approved_action_executors.json"
    return Path("prismatic_state") / "agy_approved_action_executors.json"


def _read(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = _state_path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _write(records: list[dict[str, Any]], path: str | Path | None = None) -> None:
    p = _state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _executor_id(operator_action_approval_id: str, executor_mode: str) -> str:
    digest = hashlib.sha256(
        f"{operator_action_approval_id}:{executor_mode}".encode("utf-8")
    ).hexdigest()[:16]
    return f"approved-executor-{digest}"


def _side_effects() -> dict[str, bool]:
    return {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "bulk_agent_dispatch": False,
        "overnight_autopilot": False,
        "real_executor_invoked": False,
    }


def _non_claims() -> dict[str, bool]:
    return {
        "canonical_full_suite_green": False,
        "real_linear_writeback": False,
        "real_github_pr_creation": False,
        "auto_merge": False,
        "bulk_agy_dispatch": False,
        "broad_overnight_autopilot": False,
        "production_deploy": False,
        "production_public_browser_proof": False,
        "real_execution": False,
    }


def _authorization_token(operator_action_approval_id: str) -> str:
    return f"APPROVE_EXECUTE_REAL_ACTION:{operator_action_approval_id}"


def _final_authorization_present(
    operator_action_approval_id: str, final_authorization_token: str | None
) -> bool:
    return os.environ.get(
        "PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR"
    ) == "1" and final_authorization_token == _authorization_token(
        operator_action_approval_id
    )


def _eligible_for_dry_run(approval: dict[str, Any]) -> bool:
    return (
        approval.get("operator_decision") == "approve"
        and approval.get("policy_gate") == "pass"
        and approval.get("requested_action") == "open_or_update_pr"
    )


def _execution_status(
    approval: dict[str, Any], executor_mode: str, final_authorization_present: bool
) -> str:
    if not _eligible_for_dry_run(approval):
        return "blocked_operator_approval_required"
    if executor_mode != "dry_run" and not final_authorization_present:
        return "blocked_final_authorization_required"
    return "dry_run_ready"


def _command_preview(
    approval: dict[str, Any], execution_status: str, executor_mode: str
) -> dict[str, Any]:
    requested_action = str(approval.get("requested_action") or "manual_review")
    target_issue = approval.get("execution_preview", {}).get(
        "target_issue"
    ) or approval.get("completed_work_id")
    ready = execution_status == "dry_run_ready"
    return {
        "dry_run_only": True,
        "executor_mode": executor_mode,
        "requested_action": requested_action,
        "would_execute": ready,
        "executed": False,
        "target_issue": target_issue,
        "summary": (
            "DRY_RUN_ONLY: approved action executor request is ready; final real side-effect authorization is still absent by default"
            if ready
            else "DRY_RUN_ONLY: executor request blocked before any real command can be eligible"
        ),
        "commands": {
            "github_pr": "DRY_RUN_ONLY: gh pr create/update command preview only; not executed",
            "linear": "DRY_RUN_ONLY: Linear writeback payload preview only; not posted",
            "auto_merge": "DRY_RUN_ONLY: auto-merge remains disabled",
        },
        "side_effects": _side_effects(),
    }


def _audit_writeback(approval: dict[str, Any], execution_status: str) -> dict[str, Any]:
    return {
        "posted": False,
        "dry_run": True,
        "destination": "dashboard_and_linear_preview_only",
        "operator_action_approval_id": approval.get("operator_action_approval_id"),
        "promotion_decision_id": approval.get("promotion_decision_id"),
        "completed_work_id": approval.get("completed_work_id"),
        "requested_action": approval.get("requested_action"),
        "execution_status": execution_status,
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "summary": (
            f"{ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER}: executor dry-run request recorded; no real Linear/GitHub side effects"
        ),
        "side_effects": _side_effects(),
    }


@dataclass(frozen=True)
class ApprovedActionExecutor:
    approved_action_executor_id: str
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    executor_mode: str
    final_authorization_present: bool
    execution_status: str
    command_preview: dict[str, Any]
    audit_writeback: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: dict[str, bool]
    requested_by: str
    recorded_at: str
    marker: str = ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved_action_executor_id": self.approved_action_executor_id,
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "executor_mode": self.executor_mode,
            "final_authorization_present": self.final_authorization_present,
            "execution_status": self.execution_status,
            "command_preview": self.command_preview,
            "audit_writeback": self.audit_writeback,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
            "marker": self.marker,
        }


def build_approved_action_executor(
    operator_action_approval_id: str,
    *,
    requested_by: str = "operator",
    executor_mode: str = "dry_run",
    final_authorization_token: str | None = None,
    recorded_at: str | None = None,
) -> ApprovedActionExecutor:
    approval = get_operator_action_approval(operator_action_approval_id)
    if approval is None:
        raise KeyError(operator_action_approval_id)
    mode = str(executor_mode or "dry_run").strip().lower()
    final_auth = _final_authorization_present(
        operator_action_approval_id, final_authorization_token
    )
    status = _execution_status(approval, mode, final_auth)
    return ApprovedActionExecutor(
        approved_action_executor_id=_executor_id(operator_action_approval_id, mode),
        operator_action_approval_id=operator_action_approval_id,
        promotion_decision_id=str(approval.get("promotion_decision_id") or ""),
        completed_work_id=str(approval.get("completed_work_id") or ""),
        requested_action=str(approval.get("requested_action") or "manual_review"),
        executor_mode=mode,
        final_authorization_present=final_auth,
        execution_status=status,
        command_preview=_command_preview(approval, status, mode),
        audit_writeback=_audit_writeback(approval, status),
        side_effects=_side_effects(),
        non_claims=_non_claims(),
        requested_by=requested_by or "operator",
        recorded_at=recorded_at or _now(),
    )


def record_approved_action_executor(
    operator_action_approval_id: str,
    *,
    requested_by: str = "operator",
    executor_mode: str = "dry_run",
    final_authorization_token: str | None = None,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    executor = build_approved_action_executor(
        operator_action_approval_id,
        requested_by=requested_by,
        executor_mode=executor_mode,
        final_authorization_token=final_authorization_token,
    )
    record = executor.as_dict()
    records = _read(state_path)
    records = [
        item
        for item in records
        if item.get("approved_action_executor_id")
        != executor.approved_action_executor_id
    ]
    records.insert(0, record)
    _write(records, state_path)
    return record


def list_approved_action_executors(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return records[: max(1, min(limit, 200))]


def get_approved_action_executor(
    approved_action_executor_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if record.get("approved_action_executor_id") == approved_action_executor_id:
            return record
    return None


def latest_operator_action_approval_id() -> str | None:
    records = list_operator_action_approvals(limit=1)
    if records:
        return str(records[0].get("operator_action_approval_id"))
    approval = latest_or_record_operator_action_approval(
        requested_by="approved-action-executor"
    )
    if approval:
        return str(approval.get("operator_action_approval_id"))
    return None


def latest_or_record_approved_action_executor(
    *, requested_by: str = "dashboard", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_approved_action_executors(limit=1, state_path=state_path)
    if records:
        return records[0]
    operator_action_approval_id = latest_operator_action_approval_id()
    if not operator_action_approval_id:
        return None
    return record_approved_action_executor(
        operator_action_approval_id,
        requested_by=requested_by,
        executor_mode="dry_run",
        state_path=state_path,
    )
