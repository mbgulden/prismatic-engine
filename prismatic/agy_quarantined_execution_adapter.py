"""Quarantined execution adapter preview ledger.

This module consumes the final authorization gate and persists a sealed,
hash-addressed adapter envelope without invoking a real executor or allowing
external egress. It is the containment/audit boundary before any future real
side-effect implementation can exist.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_final_action_authorization import (
    get_final_action_authorization,
    latest_or_record_final_action_authorization,
    list_final_action_authorizations,
)

ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER = (
    "ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_OK"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_QUARANTINED_EXECUTION_ADAPTER_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_quarantined_execution_adapters.json"
    return Path("prismatic_state") / "agy_quarantined_execution_adapters.json"


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
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [item for item in data["records"] if isinstance(item, dict)]
    return []


def _write(records: list[dict[str, Any]], path: str | Path | None = None) -> None:
    p = _state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _adapter_id(final_action_authorization_id: str) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "final_action_authorization_id": final_action_authorization_id,
                "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"quarantined-adapter-{digest}"


def _audit_packet_id(command_envelope_sha256: str) -> str:
    return f"adapter-audit-{command_envelope_sha256[:16]}"


def _side_effects() -> dict[str, bool]:
    return {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "bulk_agent_dispatch": False,
        "overnight_autopilot": False,
        "external_network_called": False,
        "github_api_called": False,
        "linear_api_called": False,
        "git_write_performed": False,
        "real_executor_invoked": False,
        "executed": False,
    }


def _egress_policy() -> dict[str, Any]:
    return {
        "policy": "deny_all_external_by_default",
        "external_network_allowed": False,
        "github_api_allowed": False,
        "linear_api_allowed": False,
        "git_write_allowed": False,
        "production_deploy_allowed": False,
        "auto_merge_allowed": False,
        "bulk_agent_dispatch_allowed": False,
        "real_executor_allowed": False,
    }


def _non_claims() -> dict[str, bool]:
    return {
        "canonical_full_suite_green": False,
        "real_linear_writeback": False,
        "real_github_pr_creation": False,
        "real_git_branch_creation": False,
        "auto_merge": False,
        "bulk_agent_dispatch": False,
        "broad_overnight_autopilot": False,
        "production_deploy": False,
        "public_browser_proof": False,
        "real_executor_implementation": False,
    }


def _adapter_state(final_authorization: dict[str, Any]) -> tuple[str, str]:
    final_guard_state = str(final_authorization.get("final_guard_state") or "")
    policy_gate = str(final_authorization.get("policy_gate") or "")
    decision = str(final_authorization.get("authorization_decision") or "")
    eligibility = final_authorization.get("execution_eligibility") or {}
    eligible = bool(eligibility.get("eligible"))
    executed = bool(eligibility.get("executed"))
    if decision == "defer" or policy_gate == "manual_review":
        return "manual_review", "final authorization requires manual review"
    if decision == "reject" or final_guard_state == "rejected_by_operator":
        return "manual_review", "final authorization was rejected by operator"
    if final_guard_state == "eligible_not_executed" or (eligible and not executed):
        return (
            "sealed_preview_ready",
            "final authorization eligible; adapter preview remains dry-run only",
        )
    return (
        "blocked_by_final_guard",
        "final authorization guard blocks runnable adapter state",
    )


def _command_envelope(
    final_authorization: dict[str, Any], adapter_state: str
) -> dict[str, Any]:
    return {
        "schema": "prismatic.quarantined_execution_adapter.v1",
        "adapter_mode": "quarantine_dry_run",
        "adapter_state": adapter_state,
        "requested_action": str(
            final_authorization.get("requested_action") or "manual_review"
        ),
        "completed_work_id": str(final_authorization.get("completed_work_id") or ""),
        "promotion_decision_id": str(
            final_authorization.get("promotion_decision_id") or ""
        ),
        "operator_action_approval_id": str(
            final_authorization.get("operator_action_approval_id") or ""
        ),
        "approved_action_executor_id": str(
            final_authorization.get("approved_action_executor_id") or ""
        ),
        "final_action_authorization_id": str(
            final_authorization.get("final_action_authorization_id") or ""
        ),
        "final_guard_state": str(final_authorization.get("final_guard_state") or ""),
        "policy_gate": str(final_authorization.get("policy_gate") or ""),
        "dry_run_only": True,
        "execute": False,
        "egress_policy": _egress_policy(),
        "side_effect_boundary": {
            "external_network_allowed": False,
            "github_api_allowed": False,
            "linear_api_allowed": False,
            "git_write_allowed": False,
            "production_deploy_allowed": False,
            "real_executor_invoked": False,
            "executed": False,
        },
    }


@dataclass(frozen=True)
class QuarantinedExecutionAdapter:
    quarantined_execution_adapter_id: str
    final_action_authorization_id: str
    approved_action_executor_id: str
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    adapter_mode: str
    adapter_state: str
    adapter_reason: str
    requested_by: str
    recorded_at: str
    egress_policy: dict[str, Any]
    command_envelope: dict[str, Any]
    command_envelope_sha256: str
    audit_packet: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: dict[str, bool]
    marker: str = ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "quarantined_execution_adapter_id": self.quarantined_execution_adapter_id,
            "final_action_authorization_id": self.final_action_authorization_id,
            "approved_action_executor_id": self.approved_action_executor_id,
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "adapter_mode": self.adapter_mode,
            "adapter_state": self.adapter_state,
            "adapter_reason": self.adapter_reason,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
            "egress_policy": self.egress_policy,
            "command_envelope": self.command_envelope,
            "command_envelope_sha256": self.command_envelope_sha256,
            "audit_packet": self.audit_packet,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
        }


def build_quarantined_execution_adapter(
    final_action_authorization_id: str,
    *,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    final_authorization = get_final_action_authorization(final_action_authorization_id)
    if final_authorization is None:
        raise KeyError(
            f"final action authorization not found: {final_action_authorization_id}"
        )
    adapter_state, adapter_reason = _adapter_state(final_authorization)
    envelope = _command_envelope(final_authorization, adapter_state)
    envelope_sha = hashlib.sha256(_canonical_json(envelope).encode("utf-8")).hexdigest()
    audit_packet = {
        "audit_packet_id": _audit_packet_id(envelope_sha),
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "adapter_mode": "quarantine_dry_run",
        "adapter_state": adapter_state,
        "command_envelope_sha256": envelope_sha,
        "would_write_before_real_execution": True,
        "posted": False,
        "executed": False,
        "side_effects": _side_effects(),
    }
    return QuarantinedExecutionAdapter(
        quarantined_execution_adapter_id=_adapter_id(final_action_authorization_id),
        final_action_authorization_id=final_action_authorization_id,
        approved_action_executor_id=str(
            final_authorization.get("approved_action_executor_id") or ""
        ),
        operator_action_approval_id=str(
            final_authorization.get("operator_action_approval_id") or ""
        ),
        promotion_decision_id=str(
            final_authorization.get("promotion_decision_id") or ""
        ),
        completed_work_id=str(final_authorization.get("completed_work_id") or ""),
        requested_action=str(
            final_authorization.get("requested_action") or "manual_review"
        ),
        adapter_mode="quarantine_dry_run",
        adapter_state=adapter_state,
        adapter_reason=adapter_reason,
        requested_by=requested_by,
        recorded_at=_now(),
        egress_policy=_egress_policy(),
        command_envelope=envelope,
        command_envelope_sha256=envelope_sha,
        audit_packet=audit_packet,
        side_effects=_side_effects(),
        non_claims=_non_claims(),
    ).as_dict()


def record_quarantined_execution_adapter(
    final_action_authorization_id: str,
    *,
    requested_by: str = "dashboard",
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    record = build_quarantined_execution_adapter(
        final_action_authorization_id, requested_by=requested_by
    )
    records = [
        item
        for item in _read(state_path)
        if item.get("quarantined_execution_adapter_id")
        != record["quarantined_execution_adapter_id"]
    ]
    records.append(record)
    _write(records, state_path)
    return record


def list_quarantined_execution_adapters(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return records[:limit]


def get_quarantined_execution_adapter(
    quarantined_execution_adapter_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if (
            record.get("quarantined_execution_adapter_id")
            == quarantined_execution_adapter_id
        ):
            return record
    return None


def latest_final_action_authorization_id() -> str | None:
    records = list_final_action_authorizations(limit=1)
    if records:
        return str(records[0].get("final_action_authorization_id"))
    authorization = latest_or_record_final_action_authorization(
        requested_by="quarantined-execution-adapter"
    )
    if authorization:
        return str(authorization.get("final_action_authorization_id"))
    return None


def latest_or_record_quarantined_execution_adapter(
    *, requested_by: str = "dashboard", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_quarantined_execution_adapters(limit=1, state_path=state_path)
    if records:
        return records[0]
    final_action_authorization_id = latest_final_action_authorization_id()
    if not final_action_authorization_id:
        return None
    return record_quarantined_execution_adapter(
        final_action_authorization_id,
        requested_by=requested_by,
        state_path=state_path,
    )
