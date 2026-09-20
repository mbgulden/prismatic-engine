"""Real executor arming gate readiness ledger.

This module consumes the sandboxed execution canary and persists a readiness-only
arming gate. It enumerates the exact prerequisites that would be required before
a future real executor could be armed, while keeping all real executor and
external side-effect paths disabled.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_sandboxed_execution_canary import (
    get_sandboxed_execution_canary,
    latest_or_record_sandboxed_execution_canary,
    list_sandboxed_execution_canaries,
)

ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER = (
    "ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_OK"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_REAL_EXECUTOR_ARMING_GATE_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_real_executor_arming_gates.json"
    return Path("prismatic_state") / "agy_real_executor_arming_gates.json"


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
        "real_executor_armed": False,
        "real_executor_invoked": False,
        "executed": False,
        "linear_comment_posted": False,
        "github_pr_created": False,
        "production_deployed": False,
    }


def _operator_token_expected(canary_id: str) -> str:
    return f"ARM_REAL_EXECUTOR:{canary_id}"


def _operator_token_present(canary_id: str) -> bool:
    expected = _operator_token_expected(canary_id)
    return os.environ.get("PRISMATIC_REAL_EXECUTOR_ARMING_TOKEN") == expected


def _real_executor_env_present() -> bool:
    return os.environ.get("PRISMATIC_ALLOW_REAL_EXECUTOR_ARMING") == "1"


def _required_prerequisites(canary: dict[str, Any]) -> list[dict[str, Any]]:
    canary_id = str(canary.get("sandboxed_execution_canary_id") or "")
    return [
        {
            "key": "command_envelope_verified",
            "description": "Sandbox canary verified the quarantined command envelope hash.",
            "required_value": True,
            "actual_value": bool(canary.get("command_envelope_verified")),
        },
        {
            "key": "sandbox_transcript_present",
            "description": "Durable no-op sandbox transcript exists for the canary.",
            "required_value": True,
            "actual_value": bool(
                (canary.get("sandbox_transcript") or {}).get("sandbox_transcript_id")
            ),
        },
        {
            "key": "sandbox_side_effects_false",
            "description": "Sandbox canary side-effect flags are all false.",
            "required_value": True,
            "actual_value": all(
                value is False for value in (canary.get("side_effects") or {}).values()
            ),
        },
        {
            "key": "operator_token_present",
            "description": "Exact operator arming token is present.",
            "required_value": _operator_token_expected(canary_id),
            "actual_value": _operator_token_present(canary_id),
        },
        {
            "key": "real_executor_env_present",
            "description": "PRISMATIC_ALLOW_REAL_EXECUTOR_ARMING=1 is set.",
            "required_value": "1",
            "actual_value": _real_executor_env_present(),
        },
        {
            "key": "real_executor_implementation_registered",
            "description": "A separate real executor implementation is registered and reviewed.",
            "required_value": True,
            "actual_value": False,
        },
        {
            "key": "side_effect_policy_checks_pass",
            "description": "Explicit side-effect policy checks pass for this command envelope.",
            "required_value": True,
            "actual_value": False,
        },
        {
            "key": "operator_confirms_action_and_hash",
            "description": "Operator confirms requested action and command-envelope SHA-256.",
            "required_value": True,
            "actual_value": False,
        },
    ]


def _split_prerequisites(
    required: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    satisfied: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in required:
        expected = item.get("required_value")
        actual = item.get("actual_value")
        ok = actual is True if expected is not False else actual is False
        if ok:
            satisfied.append(item)
        else:
            missing.append(item)
    return satisfied, missing


def _arming_state(
    canary: dict[str, Any], missing: list[dict[str, Any]]
) -> tuple[str, str]:
    sandbox_state = str(canary.get("sandbox_state") or "")
    if sandbox_state in {"manual_review", "hash_mismatch"}:
        return (
            "manual_review",
            "sandbox canary requires manual review before any arming analysis",
        )
    if missing:
        return (
            "blocked_missing_real_authorization",
            "real executor arming prerequisites are missing; fail-closed by default",
        )
    return (
        "eligible_not_armed",
        "all represented prerequisites satisfied but this slice is readiness-only; executor remains unarmed",
    )


def _executor_contract_preview(canary: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "prismatic.real_executor_arming_gate.v1",
        "requested_action": canary.get("requested_action"),
        "command_envelope_sha256": canary.get("command_envelope_sha256"),
        "sandboxed_execution_canary_id": canary.get("sandboxed_execution_canary_id"),
        "would_require_exact_token": _operator_token_expected(
            str(canary.get("sandboxed_execution_canary_id") or "")
        ),
        "would_require_env": "PRISMATIC_ALLOW_REAL_EXECUTOR_ARMING=1",
        "real_executor_implemented": False,
        "real_executor_invoked": False,
        "executed": False,
    }


@dataclass(frozen=True)
class RealExecutorArmingGate:
    real_executor_arming_gate_id: str
    sandboxed_execution_canary_id: str
    quarantined_execution_adapter_id: str
    final_action_authorization_id: str
    approved_action_executor_id: str
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    arming_mode: str
    arming_state: str
    required_prerequisites: list[dict[str, Any]]
    missing_prerequisites: list[dict[str, Any]]
    satisfied_prerequisites: list[dict[str, Any]]
    executor_contract_preview: dict[str, Any]
    operator_token_expected: str
    operator_token_present: bool
    real_executor_env_present: bool
    real_executor_implemented: bool
    real_executor_invoked: bool
    execution_eligibility: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: list[str]
    marker: str
    arming_reason: str
    requested_by: str
    recorded_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "real_executor_arming_gate_id": self.real_executor_arming_gate_id,
            "sandboxed_execution_canary_id": self.sandboxed_execution_canary_id,
            "quarantined_execution_adapter_id": self.quarantined_execution_adapter_id,
            "final_action_authorization_id": self.final_action_authorization_id,
            "approved_action_executor_id": self.approved_action_executor_id,
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "arming_mode": self.arming_mode,
            "arming_state": self.arming_state,
            "required_prerequisites": self.required_prerequisites,
            "missing_prerequisites": self.missing_prerequisites,
            "satisfied_prerequisites": self.satisfied_prerequisites,
            "executor_contract_preview": self.executor_contract_preview,
            "operator_token_expected": self.operator_token_expected,
            "operator_token_present": self.operator_token_present,
            "real_executor_env_present": self.real_executor_env_present,
            "real_executor_implemented": self.real_executor_implemented,
            "real_executor_invoked": self.real_executor_invoked,
            "execution_eligibility": self.execution_eligibility,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
            "arming_reason": self.arming_reason,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
        }


def build_real_executor_arming_gate(
    sandboxed_execution_canary_id: str,
    *,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    canary = get_sandboxed_execution_canary(sandboxed_execution_canary_id)
    if canary is None:
        raise KeyError(sandboxed_execution_canary_id)
    required = _required_prerequisites(canary)
    satisfied, missing = _split_prerequisites(required)
    arming_state, reason = _arming_state(canary, missing)
    canary_id = str(canary.get("sandboxed_execution_canary_id") or "")
    operator_token_present = _operator_token_present(canary_id)
    env_present = _real_executor_env_present()
    gate_id = _short_id(
        "real-executor-arming",
        canary_id,
        canary.get("command_envelope_sha256"),
        arming_state,
    )
    eligibility = {
        "eligible": arming_state == "eligible_not_armed",
        "armed": False,
        "executed": False,
        "reason": reason,
        "fail_closed": True,
    }
    return RealExecutorArmingGate(
        real_executor_arming_gate_id=gate_id,
        sandboxed_execution_canary_id=canary_id,
        quarantined_execution_adapter_id=str(
            canary.get("quarantined_execution_adapter_id") or ""
        ),
        final_action_authorization_id=str(
            canary.get("final_action_authorization_id") or ""
        ),
        approved_action_executor_id=str(
            canary.get("approved_action_executor_id") or ""
        ),
        operator_action_approval_id=str(
            canary.get("operator_action_approval_id") or ""
        ),
        promotion_decision_id=str(canary.get("promotion_decision_id") or ""),
        completed_work_id=str(canary.get("completed_work_id") or ""),
        requested_action=str(canary.get("requested_action") or ""),
        arming_mode="readiness_gate_only",
        arming_state=arming_state,
        required_prerequisites=required,
        missing_prerequisites=missing,
        satisfied_prerequisites=satisfied,
        executor_contract_preview=_executor_contract_preview(canary),
        operator_token_expected=_operator_token_expected(canary_id),
        operator_token_present=operator_token_present,
        real_executor_env_present=env_present,
        real_executor_implemented=False,
        real_executor_invoked=False,
        execution_eligibility=eligibility,
        side_effects=_side_effects(),
        non_claims=[
            "not a real executor implementation",
            "does not arm a real executor",
            "does not invoke commands",
            "does not call GitHub or Linear",
            "does not create branches or pull requests",
            "does not auto-merge",
            "does not deploy production",
            "does not dispatch agents",
        ],
        marker=ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        arming_reason=reason,
        requested_by=requested_by,
        recorded_at=_now(),
    ).as_dict()


def record_real_executor_arming_gate(
    sandboxed_execution_canary_id: str,
    *,
    requested_by: str = "dashboard",
    path: str | Path | None = None,
) -> dict[str, Any]:
    record = build_real_executor_arming_gate(
        sandboxed_execution_canary_id, requested_by=requested_by
    )
    records = [
        item
        for item in _read(path)
        if item.get("real_executor_arming_gate_id")
        != record["real_executor_arming_gate_id"]
    ]
    records.append(record)
    _write(records, path)
    return record


def list_real_executor_arming_gates(
    *, limit: int = 50, path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = sorted(
        _read(path), key=lambda item: str(item.get("recorded_at") or ""), reverse=True
    )
    return records[:limit]


def get_real_executor_arming_gate(
    real_executor_arming_gate_id: str, *, path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(path):
        if record.get("real_executor_arming_gate_id") == real_executor_arming_gate_id:
            return record
    return None


def latest_or_record_real_executor_arming_gate(
    *, requested_by: str = "dashboard", path: str | Path | None = None
) -> dict[str, Any] | None:
    existing = list_real_executor_arming_gates(limit=1, path=path)
    if existing:
        return existing[0]
    canary = latest_or_record_sandboxed_execution_canary(requested_by=requested_by)
    if canary is None:
        canaries = list_sandboxed_execution_canaries(limit=1)
        if not canaries:
            return None
        canary = canaries[0]
    return record_real_executor_arming_gate(
        str(canary["sandboxed_execution_canary_id"]),
        requested_by=requested_by,
        path=path,
    )
