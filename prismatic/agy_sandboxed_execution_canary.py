"""Sandboxed execution canary preview ledger.

This module consumes the quarantined execution adapter envelope and persists a
no-op sandbox canary proof. It verifies the adapter command-envelope hash,
produces a durable transcript preview, and keeps every real executor/external
side-effect path disabled.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_quarantined_execution_adapter import (
    get_quarantined_execution_adapter,
    latest_or_record_quarantined_execution_adapter,
    list_quarantined_execution_adapters,
)

ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER = (
    "ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_OK"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_SANDBOXED_EXECUTION_CANARY_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_sandboxed_execution_canaries.json"
    return Path("prismatic_state") / "agy_sandboxed_execution_canaries.json"


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
    p.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _short_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_sha256(parts)[:16]}"


def _side_effects() -> dict[str, bool]:
    return {
        "external_network_allowed": False,
        "github_api_allowed": False,
        "linear_api_allowed": False,
        "git_write_allowed": False,
        "production_deploy_allowed": False,
        "auto_merge_enabled": False,
        "bulk_agent_dispatch_enabled": False,
        "real_executor_invoked": False,
        "executed": False,
        "linear_comment_posted": False,
        "github_pr_created": False,
        "production_deployed": False,
    }


def _sandbox_policy() -> dict[str, Any]:
    return {
        "policy": "local_noop_no_egress",
        "external_network_allowed": False,
        "github_api_allowed": False,
        "linear_api_allowed": False,
        "git_write_allowed": False,
        "production_deploy_allowed": False,
        "auto_merge_allowed": False,
        "bulk_agent_dispatch_allowed": False,
        "real_executor_allowed": False,
        "filesystem_write_allowed": False,
        "allowed_operations": [
            "verify_command_envelope_hash",
            "render_noop_command_plan",
            "record_sandbox_transcript_preview",
        ],
        "blocked_operations": [
            "external_network",
            "github_api",
            "linear_api",
            "git_write",
            "branch_create",
            "github_pr_create",
            "linear_comment_post",
            "production_deploy",
            "auto_merge",
            "bulk_agent_dispatch",
            "real_executor_invocation",
        ],
    }


def _sandbox_state(adapter: dict[str, Any], envelope_verified: bool) -> tuple[str, str]:
    if not envelope_verified:
        return "manual_review", "command envelope hash mismatch; sandbox canary blocked"
    adapter_state = str(adapter.get("adapter_state") or "")
    if adapter_state == "sealed_preview_ready":
        return (
            "noop_canary_ready",
            "adapter sealed preview can be represented as no-op sandbox transcript only",
        )
    if adapter_state == "manual_review":
        return "manual_review", "adapter is in manual review; no runnable canary state"
    return (
        "blocked_by_final_guard",
        "final authorization guard blocks runnable adapter state",
    )


def _noop_command_plan(adapter: dict[str, Any], sandbox_state: str) -> dict[str, Any]:
    return {
        "plan_mode": "noop_simulation_only",
        "requested_action": adapter.get("requested_action"),
        "sandbox_state": sandbox_state,
        "steps": [
            "load quarantined adapter record",
            "recompute canonical command envelope sha256",
            "compare envelope hash with adapter command_envelope_sha256",
            "render no-op transcript without calling GitHub, Linear, git, deploy, or agents",
            "persist sandbox canary proof with all side-effect flags false",
        ],
        "real_commands_executed": False,
        "external_calls_executed": False,
    }


def _sandbox_transcript(
    adapter: dict[str, Any], sandbox_state: str, envelope_verified: bool
) -> dict[str, Any]:
    transcript_id = _short_id(
        "sandbox-transcript",
        adapter.get("quarantined_execution_adapter_id"),
        adapter.get("command_envelope_sha256"),
        sandbox_state,
    )
    return {
        "sandbox_transcript_id": transcript_id,
        "summary": (
            "No-op sandbox canary verified envelope hash and denied all egress; "
            "no real command execution occurred."
        ),
        "events": [
            {
                "event": "adapter_loaded",
                "ok": True,
                "quarantined_execution_adapter_id": adapter.get(
                    "quarantined_execution_adapter_id"
                ),
            },
            {
                "event": "command_envelope_hash_verified",
                "ok": envelope_verified,
                "command_envelope_sha256": adapter.get("command_envelope_sha256"),
            },
            {
                "event": "egress_policy_enforced",
                "ok": True,
                "policy": "deny_all_external_by_default",
            },
            {"event": "real_executor_invocation", "ok": False, "blocked": True},
        ],
        "executed": False,
        "real_executor_invoked": False,
        "external_calls_executed": False,
    }


@dataclass(frozen=True)
class SandboxedExecutionCanary:
    sandboxed_execution_canary_id: str
    quarantined_execution_adapter_id: str
    final_action_authorization_id: str
    approved_action_executor_id: str
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    canary_mode: str
    sandbox_state: str
    command_envelope_sha256: str
    command_envelope_verified: bool
    sandbox_policy: dict[str, Any]
    noop_command_plan: dict[str, Any]
    sandbox_transcript: dict[str, Any]
    egress_attempts: list[dict[str, Any]]
    side_effects: dict[str, bool]
    non_claims: list[str]
    marker: str
    sandbox_reason: str
    requested_by: str
    recorded_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sandboxed_execution_canary_id": self.sandboxed_execution_canary_id,
            "quarantined_execution_adapter_id": self.quarantined_execution_adapter_id,
            "final_action_authorization_id": self.final_action_authorization_id,
            "approved_action_executor_id": self.approved_action_executor_id,
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "canary_mode": self.canary_mode,
            "sandbox_state": self.sandbox_state,
            "command_envelope_sha256": self.command_envelope_sha256,
            "command_envelope_verified": self.command_envelope_verified,
            "sandbox_policy": self.sandbox_policy,
            "noop_command_plan": self.noop_command_plan,
            "sandbox_transcript": self.sandbox_transcript,
            "egress_attempts": self.egress_attempts,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
            "sandbox_reason": self.sandbox_reason,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
        }


def build_sandboxed_execution_canary(
    quarantined_execution_adapter_id: str,
    *,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    adapter = get_quarantined_execution_adapter(quarantined_execution_adapter_id)
    if adapter is None:
        raise KeyError(quarantined_execution_adapter_id)

    command_envelope = adapter.get("command_envelope") or {}
    computed_sha = _sha256(command_envelope)
    adapter_sha = str(adapter.get("command_envelope_sha256") or "")
    envelope_verified = bool(adapter_sha) and computed_sha == adapter_sha
    sandbox_state, reason = _sandbox_state(adapter, envelope_verified)
    canary_id = _short_id(
        "sandbox-canary",
        quarantined_execution_adapter_id,
        adapter_sha,
        sandbox_state,
    )
    transcript = _sandbox_transcript(adapter, sandbox_state, envelope_verified)
    record = SandboxedExecutionCanary(
        sandboxed_execution_canary_id=canary_id,
        quarantined_execution_adapter_id=quarantined_execution_adapter_id,
        final_action_authorization_id=str(adapter.get("final_action_authorization_id")),
        approved_action_executor_id=str(adapter.get("approved_action_executor_id")),
        operator_action_approval_id=str(adapter.get("operator_action_approval_id")),
        promotion_decision_id=str(adapter.get("promotion_decision_id")),
        completed_work_id=str(adapter.get("completed_work_id")),
        requested_action=str(adapter.get("requested_action") or "open_or_update_pr"),
        canary_mode="sandbox_noop_dry_run",
        sandbox_state=sandbox_state,
        command_envelope_sha256=adapter_sha,
        command_envelope_verified=envelope_verified,
        sandbox_policy=_sandbox_policy(),
        noop_command_plan=_noop_command_plan(adapter, sandbox_state),
        sandbox_transcript=transcript,
        egress_attempts=[],
        side_effects=_side_effects(),
        non_claims=[
            "no real Linear writeback",
            "no real GitHub PR creation from agent output",
            "no git branch/write operation",
            "no auto-merge",
            "no production deploy",
            "no bulk AGY dispatch",
            "no broad overnight autopilot",
            "no real executor implementation",
            "no real command execution",
        ],
        marker=ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        sandbox_reason=reason,
        requested_by=requested_by,
        recorded_at=_now(),
    )
    return record.as_dict()


def record_sandboxed_execution_canary(
    quarantined_execution_adapter_id: str,
    *,
    requested_by: str = "dashboard",
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    record = build_sandboxed_execution_canary(
        quarantined_execution_adapter_id, requested_by=requested_by
    )
    records = [
        item
        for item in _read(state_path)
        if item.get("sandboxed_execution_canary_id")
        != record["sandboxed_execution_canary_id"]
    ]
    records.append(record)
    _write(records, state_path)
    return record


def list_sandboxed_execution_canaries(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return records[:limit]


def get_sandboxed_execution_canary(
    sandboxed_execution_canary_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if record.get("sandboxed_execution_canary_id") == sandboxed_execution_canary_id:
            return record
    return None


def latest_quarantined_execution_adapter_id() -> str | None:
    records = list_quarantined_execution_adapters(limit=1)
    if records:
        return str(records[0].get("quarantined_execution_adapter_id"))
    adapter = latest_or_record_quarantined_execution_adapter(
        requested_by="sandboxed-execution-canary"
    )
    if adapter:
        return str(adapter.get("quarantined_execution_adapter_id"))
    return None


def latest_or_record_sandboxed_execution_canary(
    *, requested_by: str = "dashboard", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_sandboxed_execution_canaries(limit=1, state_path=state_path)
    if records:
        return records[0]
    quarantined_execution_adapter_id = latest_quarantined_execution_adapter_id()
    if not quarantined_execution_adapter_id:
        return None
    return record_sandboxed_execution_canary(
        quarantined_execution_adapter_id,
        requested_by=requested_by,
        state_path=state_path,
    )
