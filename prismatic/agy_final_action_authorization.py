"""Final authorization gate for approved action executor dry-runs.

This module persists the separate operator final-authorization decision that
must exist after an approved-action executor dry-run and before any real external
side effect could ever become eligible. It fails closed by default: records may
state intent, but real execution is not invoked here and all side-effect flags
remain false.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_approved_action_executor import (
    get_approved_action_executor,
    latest_or_record_approved_action_executor,
    list_approved_action_executors,
)

ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER = (
    "ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_OK"
)
_ALLOWED_AUTHORIZATION_DECISIONS = {"authorize", "reject", "defer"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_FINAL_ACTION_AUTHORIZATION_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_final_action_authorizations.json"
    return Path("prismatic_state") / "agy_final_action_authorizations.json"


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


def _authorization_id(
    approved_action_executor_id: str, authorization_decision: str
) -> str:
    digest = hashlib.sha256(
        f"{approved_action_executor_id}:{authorization_decision}".encode("utf-8")
    ).hexdigest()[:16]
    return f"final-authorization-{digest}"


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
        "real_side_effects_enabled": False,
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


def expected_authorization_token(operator_action_approval_id: str) -> str:
    return f"APPROVE_EXECUTE_REAL_ACTION:{operator_action_approval_id}"


def _real_execution_env_present() -> bool:
    return os.environ.get("PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR") == "1"


def _token_present(
    operator_action_approval_id: str, authorization_token: str | None
) -> bool:
    return authorization_token == expected_authorization_token(
        operator_action_approval_id
    )


def _executor_ready(executor: dict[str, Any]) -> bool:
    return (
        executor.get("execution_status") == "dry_run_ready"
        and executor.get("executor_mode") == "dry_run"
        and executor.get("requested_action") == "open_or_update_pr"
        and executor.get("command_preview", {}).get("dry_run_only") is True
        and executor.get("audit_writeback", {}).get("posted") is False
    )


def _policy_state(
    executor: dict[str, Any],
    authorization_decision: str,
    authorization_token_present: bool,
    real_execution_env_present: bool,
) -> tuple[str, str, bool, str]:
    if authorization_decision == "reject":
        return (
            "blocked",
            "rejected_by_operator",
            False,
            "operator rejected final authorization",
        )
    if authorization_decision == "defer":
        return (
            "manual_review",
            "manual_review",
            False,
            "operator deferred final authorization",
        )
    if authorization_decision != "authorize":
        return (
            "blocked",
            "blocked_invalid_authorization_decision",
            False,
            "authorization decision must be authorize, reject, or defer",
        )
    if not _executor_ready(executor):
        return (
            "blocked",
            "blocked_executor_not_ready",
            False,
            "approved-action executor is not dry-run-ready",
        )
    if not authorization_token_present:
        return (
            "blocked",
            "blocked_by_default",
            False,
            "exact authorization token not present",
        )
    if not real_execution_env_present:
        return (
            "blocked",
            "blocked_by_default",
            False,
            "real execution environment gate is not enabled",
        )
    return (
        "eligible_but_not_executed",
        "eligible_not_executed",
        True,
        "all final authorization gates are present, but this slice still does not execute real side effects",
    )


def _execution_eligibility(
    *,
    executor: dict[str, Any],
    authorization_decision: str,
    authorization_token_present: bool,
    real_execution_env_present: bool,
    policy_gate: str,
    final_guard_state: str,
    eligible: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "requested_action": executor.get("requested_action"),
        "executor_ready": _executor_ready(executor),
        "authorization_decision": authorization_decision,
        "authorization_token_present": authorization_token_present,
        "real_execution_env_present": real_execution_env_present,
        "policy_gate": policy_gate,
        "final_guard_state": final_guard_state,
        "eligible": eligible,
        "executed": False,
        "reason": reason,
        "required_env": "PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR=1",
        "required_token_shape": "APPROVE_EXECUTE_REAL_ACTION:<operator_action_approval_id>",
        "dry_run_only": True,
    }


@dataclass(frozen=True)
class FinalActionAuthorization:
    final_action_authorization_id: str
    approved_action_executor_id: str
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    authorization_decision: str
    requested_by: str
    recorded_at: str
    authorization_token_expected: str
    authorization_token_present: bool
    real_execution_env_present: bool
    policy_gate: str
    final_guard_state: str
    execution_eligibility: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: dict[str, bool]
    marker: str = ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_action_authorization_id": self.final_action_authorization_id,
            "approved_action_executor_id": self.approved_action_executor_id,
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "authorization_decision": self.authorization_decision,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
            "authorization_token_expected": self.authorization_token_expected,
            "authorization_token_present": self.authorization_token_present,
            "real_execution_env_present": self.real_execution_env_present,
            "policy_gate": self.policy_gate,
            "final_guard_state": self.final_guard_state,
            "execution_eligibility": self.execution_eligibility,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
        }


def build_final_action_authorization(
    approved_action_executor_id: str,
    *,
    authorization_decision: str = "authorize",
    requested_by: str = "dashboard",
    authorization_token: str | None = None,
) -> dict[str, Any]:
    decision = (authorization_decision or "authorize").strip().lower()
    if decision not in _ALLOWED_AUTHORIZATION_DECISIONS:
        raise ValueError("authorization_decision must be authorize, reject, or defer")
    executor = get_approved_action_executor(approved_action_executor_id)
    if executor is None:
        raise KeyError(
            f"approved action executor not found: {approved_action_executor_id}"
        )
    operator_action_approval_id = str(executor.get("operator_action_approval_id") or "")
    token_expected = expected_authorization_token(operator_action_approval_id)
    token_present = _token_present(operator_action_approval_id, authorization_token)
    env_present = _real_execution_env_present()
    policy_gate, final_guard_state, eligible, reason = _policy_state(
        executor,
        decision,
        token_present,
        env_present,
    )
    eligibility = _execution_eligibility(
        executor=executor,
        authorization_decision=decision,
        authorization_token_present=token_present,
        real_execution_env_present=env_present,
        policy_gate=policy_gate,
        final_guard_state=final_guard_state,
        eligible=eligible,
        reason=reason,
    )
    return FinalActionAuthorization(
        final_action_authorization_id=_authorization_id(
            approved_action_executor_id, decision
        ),
        approved_action_executor_id=approved_action_executor_id,
        operator_action_approval_id=operator_action_approval_id,
        promotion_decision_id=str(executor.get("promotion_decision_id") or ""),
        completed_work_id=str(executor.get("completed_work_id") or ""),
        requested_action=str(executor.get("requested_action") or "manual_review"),
        authorization_decision=decision,
        requested_by=requested_by,
        recorded_at=_now(),
        authorization_token_expected=token_expected,
        authorization_token_present=token_present,
        real_execution_env_present=env_present,
        policy_gate=policy_gate,
        final_guard_state=final_guard_state,
        execution_eligibility=eligibility,
        side_effects=_side_effects(),
        non_claims=_non_claims(),
    ).as_dict()


def record_final_action_authorization(
    approved_action_executor_id: str,
    *,
    authorization_decision: str = "authorize",
    requested_by: str = "dashboard",
    authorization_token: str | None = None,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    record = build_final_action_authorization(
        approved_action_executor_id,
        authorization_decision=authorization_decision,
        requested_by=requested_by,
        authorization_token=authorization_token,
    )
    records = [
        item
        for item in _read(state_path)
        if item.get("final_action_authorization_id")
        != record["final_action_authorization_id"]
    ]
    records.append(record)
    _write(records, state_path)
    return record


def list_final_action_authorizations(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return records[: max(1, min(limit, 200))]


def get_final_action_authorization(
    final_action_authorization_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if record.get("final_action_authorization_id") == final_action_authorization_id:
            return record
    return None


def latest_approved_action_executor_id() -> str | None:
    records = list_approved_action_executors(limit=1)
    if records:
        return str(records[0].get("approved_action_executor_id"))
    executor = latest_or_record_approved_action_executor(
        requested_by="final-action-authorization"
    )
    if executor:
        return str(executor.get("approved_action_executor_id"))
    return None


def latest_or_record_final_action_authorization(
    *, requested_by: str = "dashboard", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_final_action_authorizations(limit=1, state_path=state_path)
    if records:
        return records[0]
    approved_action_executor_id = latest_approved_action_executor_id()
    if not approved_action_executor_id:
        return None
    return record_final_action_authorization(
        approved_action_executor_id,
        authorization_decision="authorize",
        requested_by=requested_by,
        state_path=state_path,
    )
