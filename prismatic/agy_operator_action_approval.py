"""Durable operator action approvals for one-agent promotion decisions.

This module is the safe bridge after the promotion-decision ledger: it records
whether an operator approved, rejected, or deferred the recommended action and
returns only dry-run execution previews. It never creates GitHub PRs, posts
Linear comments, enables auto-merge, dispatches agents, or deploys production.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.agy_promotion_ledger import (
    get_promotion_decision,
    latest_or_record_decision,
    list_promotion_decisions,
)
from prismatic.verification.receipt_store import (
    VerificationReceiptStore,
    verification_receipt_store_path,
)

ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER = (
    "ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_OK"
)
VALID_OPERATOR_DECISIONS = {"approve", "reject", "defer"}
_NATIVE_BINDING_FIELDS = (
    "receipt_id",
    "receipt_sha256",
    "repository_id",
    "task_id",
    "base_sha",
    "base_tree_sha",
    "candidate_sha",
    "tree_sha",
)
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_ID_RE = re.compile(r"pnvr-[0-9a-f]{64}\Z")
_REPOSITORY_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,256}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_operator_action_approvals.json"
    return Path("prismatic_state") / "agy_operator_action_approvals.json"


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


def _approval_id(promotion_decision_id: str, operator_decision: str) -> str:
    digest = hashlib.sha256(
        f"{promotion_decision_id}:{operator_decision}".encode()
    ).hexdigest()[:16]
    return f"operator-approval-{digest}"


def _side_effects() -> dict[str, bool]:
    return {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "bulk_agent_dispatch": False,
        "overnight_autopilot": False,
    }


def _non_claims() -> dict[str, bool]:
    return {
        "real_linear_writeback": False,
        "real_github_pr_creation": False,
        "auto_merge": False,
        "bulk_agy_dispatch": False,
        "broad_overnight_autopilot": False,
        "production_deploy": False,
        "canonical_full_suite_green": False,
        "real_execution": False,
    }


def _normalize_operator_decision(operator_decision: str | None) -> str:
    decision = str(operator_decision or "approve").strip().lower()
    if decision not in VALID_OPERATOR_DECISIONS:
        raise ValueError("operator_decision must be one of approve, reject, or defer")
    return decision


def _binding_formats_valid(bindings: dict[str, Any]) -> bool:
    if not isinstance(bindings, dict):
        return False
    return (
        bool(_RECEIPT_ID_RE.fullmatch(str(bindings.get("receipt_id") or "")))
        and bool(_SHA256_RE.fullmatch(str(bindings.get("receipt_sha256") or "")))
        and bool(_REPOSITORY_ID_RE.fullmatch(str(bindings.get("repository_id") or "")))
        and 0 < len(str(bindings.get("task_id") or "")) <= 256
        and all(
            _GIT_OID_RE.fullmatch(str(bindings.get(field) or ""))
            for field in ("base_sha", "base_tree_sha", "candidate_sha", "tree_sha")
        )
        and isinstance(bindings.get("checkout_clean_state"), dict)
        and bindings["checkout_clean_state"].get("status") == "clean"
    )


def _authoritative_receipt_matches(expected: dict[str, Any]) -> bool:
    store_path = verification_receipt_store_path()
    if not store_path.is_file():
        return False
    try:
        receipt = VerificationReceiptStore(store_path).find_latest(
            task_id=str(expected["task_id"]),
            candidate_sha=str(expected["candidate_sha"]),
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error, KeyError):
        return False
    if (
        receipt is None
        or receipt.classification != "accepted"
        or not receipt.merge_eligible
    ):
        return False
    actual = {
        "receipt_id": receipt.receipt_id,
        "receipt_sha256": receipt.receipt_sha256,
        "repository_id": receipt.receipt.get("repository_id"),
        "task_id": receipt.receipt.get("task_id"),
        "base_sha": receipt.receipt.get("base_sha"),
        "base_tree_sha": receipt.receipt.get("base_tree_sha"),
        "candidate_sha": receipt.receipt.get("candidate_sha"),
        "tree_sha": receipt.receipt.get("tree_sha"),
        "checkout_clean_state": receipt.receipt.get("checkout_clean_state"),
    }
    return actual == expected


def _native_authorization_allows(
    promotion_decision: dict[str, Any], requested_action: str
) -> bool:
    evidence = promotion_decision.get("evidence") or {}
    if not isinstance(evidence, dict):
        return False
    authorization = evidence.get("authorization") or {}
    native = evidence.get("native_acceptance") or {}
    expected = evidence.get("expected_native_bindings") or {}
    if not all(isinstance(item, dict) for item in (authorization, native, expected)):
        return False
    target_task = str(
        promotion_decision.get("target_issue")
        or promotion_decision.get("completed_work_id")
        or ""
    )
    native_bindings = {field: native.get(field) for field in _NATIVE_BINDING_FIELDS} | {
        "checkout_clean_state": native.get("checkout_clean_state")
    }
    return (
        authorization.get("acceptance_authority") == "native_provider_neutral_receipt"
        and authorization.get("hosted_signals_required") is False
        and native.get("status") == "accepted"
        and native.get("authoritative") is True
        and native.get("merge_authorized") is True
        and native.get("task_id") == target_task
        and _binding_formats_valid(expected)
        and native_bindings == expected
        and _authoritative_receipt_matches(expected)
        and (
            authorization.get("merge_authorized") is True
            if requested_action == "open_or_update_pr"
            else authorization.get("deploy_authorized") is True
        )
    )


def _policy_gate(
    promotion_decision: dict[str, Any], operator_decision: str, requested_action: str
) -> str:
    if operator_decision == "reject":
        return "blocked"
    if operator_decision == "defer":
        return "manual_review"
    if (
        promotion_decision.get("status") == "decision_ready"
        and promotion_decision.get("recommendation") == requested_action
        and requested_action == "open_or_update_pr"
        and _native_authorization_allows(promotion_decision, requested_action)
    ):
        return "pass"
    return "manual_review"


def _execution_preview(
    promotion_decision: dict[str, Any], requested_action: str, policy_gate: str
) -> dict[str, Any]:
    completed_work_id = str(promotion_decision.get("completed_work_id") or "")
    target_issue = promotion_decision.get("target_issue") or completed_work_id
    allowed = policy_gate == "pass"
    return {
        "dry_run_only": True,
        "requested_action": requested_action,
        "would_execute": allowed,
        "execution_implemented": False,
        "executed": False,
        "target_issue": target_issue,
        "completed_work_id": completed_work_id,
        "summary": (
            "DRY_RUN_ONLY: operator approved open/update PR plan; real execution still requires a separate final side-effect gate"
            if allowed
            else "DRY_RUN_ONLY: no execution eligible from this operator decision"
        ),
        "commands": {
            "github_pr": "DRY_RUN_ONLY: no gh pr create command executed",
            "linear": "DRY_RUN_ONLY: no Linear comment posted",
        },
        "side_effects": _side_effects(),
    }


@dataclass(frozen=True)
class OperatorActionApproval:
    operator_action_approval_id: str
    promotion_decision_id: str
    completed_work_id: str
    requested_action: str
    operator_decision: str
    requested_by: str
    recorded_at: str
    policy_gate: str
    execution_preview: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: dict[str, bool]
    marker: str = ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "operator_action_approval_id": self.operator_action_approval_id,
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "requested_action": self.requested_action,
            "operator_decision": self.operator_decision,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
            "policy_gate": self.policy_gate,
            "execution_preview": self.execution_preview,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
        }


def build_operator_action_approval(
    promotion_decision_id: str,
    *,
    operator_decision: str = "approve",
    requested_by: str = "operator",
    requested_action: str | None = None,
    recorded_at: str | None = None,
) -> OperatorActionApproval:
    promotion_decision = get_promotion_decision(promotion_decision_id)
    if promotion_decision is None:
        raise KeyError(promotion_decision_id)
    decision = _normalize_operator_decision(operator_decision)
    action = str(
        requested_action or promotion_decision.get("recommendation") or "manual_review"
    )
    gate = _policy_gate(promotion_decision, decision, action)
    return OperatorActionApproval(
        operator_action_approval_id=_approval_id(promotion_decision_id, decision),
        promotion_decision_id=promotion_decision_id,
        completed_work_id=str(promotion_decision.get("completed_work_id") or ""),
        requested_action=action,
        operator_decision=decision,
        requested_by=requested_by or "operator",
        recorded_at=recorded_at or _now(),
        policy_gate=gate,
        execution_preview=_execution_preview(promotion_decision, action, gate),
        side_effects=_side_effects(),
        non_claims=_non_claims(),
    )


def record_operator_action_approval(
    promotion_decision_id: str,
    *,
    operator_decision: str = "approve",
    requested_by: str = "operator",
    requested_action: str | None = None,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    approval = build_operator_action_approval(
        promotion_decision_id,
        operator_decision=operator_decision,
        requested_by=requested_by,
        requested_action=requested_action,
    )
    record = approval.as_dict()
    records = _read(state_path)
    records = [
        item
        for item in records
        if item.get("operator_action_approval_id")
        != approval.operator_action_approval_id
    ]
    records.insert(0, record)
    _write(records, state_path)
    return record


def _revalidated_approval_view(record: dict[str, Any]) -> dict[str, Any]:
    view = dict(record)
    promotion_decision_id = str(record.get("promotion_decision_id") or "")
    try:
        promotion = get_promotion_decision(promotion_decision_id)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        promotion = None
    action = str(record.get("requested_action") or "manual_review")
    decision = str(record.get("operator_decision") or "defer")
    current_gate = (
        _policy_gate(promotion, decision, action)
        if isinstance(promotion, dict)
        else "manual_review"
    )
    view["stored_policy_gate"] = record.get("policy_gate")
    view["policy_gate"] = current_gate
    view["revalidation_status"] = (
        "passed_current_native_receipt"
        if current_gate == "pass"
        else "held_by_current_native_receipt"
    )
    view["execution_preview"] = _execution_preview(
        promotion or {}, action, current_gate
    )
    return view


def list_operator_action_approvals(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return [
        _revalidated_approval_view(record)
        for record in records[: max(1, min(limit, 200))]
    ]


def get_operator_action_approval(
    operator_action_approval_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if record.get("operator_action_approval_id") == operator_action_approval_id:
            return _revalidated_approval_view(record)
    return None


def latest_promotion_decision_id() -> str | None:
    records = list_promotion_decisions(limit=1)
    if records:
        return str(records[0].get("promotion_decision_id"))
    decision = latest_or_record_decision(requested_by="operator-action-approval")
    if decision:
        return str(decision.get("promotion_decision_id"))
    return None


def latest_or_record_operator_action_approval(
    *, requested_by: str = "dashboard", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_operator_action_approvals(limit=1, state_path=state_path)
    if records:
        return records[0]
    promotion_decision_id = latest_promotion_decision_id()
    if not promotion_decision_id:
        return None
    return record_operator_action_approval(
        promotion_decision_id,
        operator_decision="approve",
        requested_by=requested_by,
        state_path=state_path,
    )
