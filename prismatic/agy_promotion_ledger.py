"""Durable promotion-decision ledger for one-agent completed-work rows.

The ledger records Prismatic's operator-facing promotion decision for a completed
work row after packet classification, dashboard/Linear dry-run, and verified PR
dry-run planning. It is intentionally local/durable and never posts Linear
comments, creates GitHub PRs, enables auto-merge, dispatches agents, or deploys.
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

from prismatic.agy_completed_work import get_completed_work, list_completed_work
from prismatic.agy_merge_backlog import (
    build_operator_pr_creation_dry_run,
    verify_merge_backlog_item,
)
from prismatic.verification.receipt_store import (
    VerificationReceiptStore,
    verification_receipt_store_path,
)

ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER = "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    explicit = os.environ.get("PRISMATIC_AGY_PROMOTION_LEDGER_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "agy_promotion_decisions.json"
    return Path("prismatic_state") / "agy_promotion_decisions.json"


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


def _ledger_id(completed_work_id: str) -> str:
    digest = hashlib.sha256(completed_work_id.encode("utf-8")).hexdigest()[:16]
    return f"promotion-{digest}"


@dataclass(frozen=True)
class PromotionDecision:
    promotion_decision_id: str
    completed_work_id: str
    status: str
    recommendation: str
    requested_by: str
    recorded_at: str
    okf: dict[str, str]
    packet_classification: str | None
    completed_work_classification: str | None
    integration_classification: str | None
    verification_gate: str | None
    verification_lane: str | None
    dry_run_pr_action: str | None
    target_issue: str | None
    evidence: dict[str, Any]
    source_decision: dict[str, Any]
    side_effects: dict[str, bool]
    non_claims: dict[str, bool]
    marker: str = ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "promotion_decision_id": self.promotion_decision_id,
            "completed_work_id": self.completed_work_id,
            "status": self.status,
            "recommendation": self.recommendation,
            "requested_by": self.requested_by,
            "recorded_at": self.recorded_at,
            "okf": self.okf,
            "packet_classification": self.packet_classification,
            "completed_work_classification": self.completed_work_classification,
            "integration_classification": self.integration_classification,
            "verification_gate": self.verification_gate,
            "verification_lane": self.verification_lane,
            "dry_run_pr_action": self.dry_run_pr_action,
            "target_issue": self.target_issue,
            "evidence": self.evidence,
            "source_decision": self.source_decision,
            "side_effects": self.side_effects,
            "non_claims": self.non_claims,
            "marker": self.marker,
        }


def _valid_source_commit_sha(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", text):
        return text
    return None


def _native_acceptance_for(row_payload: dict[str, Any]) -> dict[str, Any]:
    packet = row_payload.get("packet") or {}
    evidence_retention = row_payload.get("evidence_retention") or {}
    task_id = str(packet.get("issue_identifier") or "").strip()
    candidate_sha = _valid_source_commit_sha(
        evidence_retention.get("source_commit_sha")
    )
    store_path = verification_receipt_store_path()
    if not task_id or not candidate_sha:
        return {
            "status": "blocked",
            "reason": "missing_task_or_candidate_binding",
            "authoritative": True,
            "merge_authorized": False,
            "deploy_authorized": False,
        }
    if not store_path.is_file():
        return {
            "status": "blocked",
            "reason": "native_receipt_store_missing",
            "authoritative": True,
            "merge_authorized": False,
            "deploy_authorized": False,
        }
    try:
        receipt = VerificationReceiptStore(store_path).find_latest(
            task_id=task_id, candidate_sha=candidate_sha
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        return {
            "status": "blocked",
            "reason": f"native_receipt_read_failed:{type(exc).__name__}",
            "authoritative": True,
            "merge_authorized": False,
            "deploy_authorized": False,
        }
    if receipt is None:
        return {
            "status": "blocked",
            "reason": "matching_native_receipt_missing",
            "authoritative": True,
            "merge_authorized": False,
            "deploy_authorized": False,
        }
    accepted = receipt.classification == "accepted" and receipt.merge_eligible
    return {
        "status": "accepted" if accepted else receipt.classification,
        "reason": receipt.decision_reason,
        "authoritative": True,
        "receipt_id": receipt.receipt_id,
        "receipt_sha256": receipt.receipt_sha256,
        "repository_id": receipt.receipt.get("repository_id"),
        "task_id": receipt.receipt.get("task_id"),
        "base_sha": receipt.receipt.get("base_sha"),
        "base_tree_sha": receipt.receipt.get("base_tree_sha"),
        "candidate_sha": candidate_sha,
        "tree_sha": receipt.receipt.get("tree_sha"),
        "checkout_clean_state": receipt.receipt.get("checkout_clean_state"),
        "merge_authorized": accepted,
        "deploy_authorized": accepted,
        "hosted_signals_required": False,
    }


def _source_decision_summary(source: dict[str, Any]) -> dict[str, Any]:
    evidence_retention = source.get("evidence_retention")
    evidence = evidence_retention if isinstance(evidence_retention, dict) else {}
    return {
        "status": source.get("status"),
        "policy_gate": source.get("policy_gate"),
        "promotion_decision": source.get("promotion_decision")
        or source.get("recommendation"),
        "recommendation": source.get("recommendation"),
        "reasons": list(source.get("reasons") or []),
        "packet_classification": source.get("packet_classification"),
        "completed_work_classification": source.get("completed_work_classification"),
        "integration_classification": source.get("integration_classification"),
        "proof_result": source.get("proof_result"),
        "proof_marker": source.get("proof_marker"),
        "evidence_retention": {
            "status": evidence.get("status") or "unavailable",
            "proof_log_retained": bool(evidence.get("proof_log_retained")),
            "manifest_sha256_present": bool(evidence.get("manifest_sha256")),
            "source_commit_sha": _valid_source_commit_sha(
                evidence.get("source_commit_sha")
            ),
        },
        "marker": source.get("marker"),
    }


def _source_decision_allows_promotion(summary: dict[str, Any]) -> bool:
    evidence = summary.get("evidence_retention") or {}
    return (
        summary.get("status") == "decision_ready"
        and summary.get("policy_gate") == "pass"
        and summary.get("promotion_decision") == "open_or_update_pr_dry_run_only"
        and evidence.get("status") == "complete"
        and evidence.get("proof_log_retained") is True
        and bool(evidence.get("manifest_sha256_present"))
        and bool(_valid_source_commit_sha(evidence.get("source_commit_sha")))
    )


def _recommendation_from_source(summary: dict[str, Any]) -> str:
    source_recommendation = str(summary.get("recommendation") or "manual_review")
    if source_recommendation == "open_or_update_pr_dry_run_only":
        return "open_or_update_pr"
    if source_recommendation in {"hold_for_durable_evidence", "blocked"}:
        return source_recommendation
    return "manual_review"


def build_promotion_decision(
    completed_work_id: str,
    *,
    requested_by: str = "operator",
    recorded_at: str | None = None,
) -> PromotionDecision:
    """Build the promotion decision payload without persisting it."""

    row = get_completed_work(completed_work_id)
    row_payload = row.as_dict()
    verification = verify_merge_backlog_item(completed_work_id)
    pr_dry_run = build_operator_pr_creation_dry_run(
        completed_work_id,
        requested_by=requested_by,
        action="promotion_decision_ledger_dry_run",
        linear_writeback=True,
    )
    merge_backlog = verification.get("merge_backlog") or {}
    verification_gate = verification.get("verification_gate")
    integration = row_payload.get("integration_classification")
    packet_classification = row_payload.get("packet_classification")
    source_decision = _source_decision_summary(
        row_payload.get("promotion_decision") or {}
    )
    native_acceptance = _native_acceptance_for(row_payload)
    source_allows_promotion = _source_decision_allows_promotion(source_decision)
    native_allows_promotion = bool(native_acceptance.get("merge_authorized"))
    recommendation = _recommendation_from_source(source_decision)
    status = (
        "decision_ready"
        if source_allows_promotion
        and native_allows_promotion
        and verification_gate == "pass"
        and integration == "pass_ready_for_review"
        else str(source_decision.get("status") or "manual_review")
    )
    if not source_allows_promotion and status == "decision_ready":
        status = "manual_review"
    if not native_allows_promotion:
        status = "manual_review"
    if status != "decision_ready" and recommendation == "open_or_update_pr":
        recommendation = "manual_review"

    okf = {
        "objective": "Agent completed-work output becomes trustworthy and operator-decisionable.",
        "key_result": "One completed-work row has a durable promotion decision with provider-neutral native authorization and optional PR/Linear planning.",
        "function": "completed-work classifier + native receipt authority + promotion ledger",
        "evidence": "packet/read-model fields, exact-artifact native receipt, optional dry-run transport plans, side-effect safety flags, and ledger record",
        "promotion_decision": recommendation,
    }
    side_effects = {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "bulk_agent_dispatch": False,
        "overnight_autopilot": False,
    }
    non_claims = {
        "real_linear_writeback": False,
        "real_github_pr_creation": False,
        "auto_merge": False,
        "bulk_agy_dispatch": False,
        "broad_overnight_autopilot": False,
        "production_deploy": False,
        "canonical_full_suite_green": False,
    }
    evidence = {
        "completed_work_marker": row_payload.get("marker"),
        "completed_work_id": completed_work_id,
        "source_branch": row_payload.get("source_branch"),
        "proof_marker": row_payload.get("proof_marker"),
        "proof_result": row_payload.get("proof_result"),
        "packet_classification": packet_classification,
        "normalized_record_classification": (
            row_payload.get("normalized_record") or {}
        ).get("classification"),
        "classification": row_payload.get("classification"),
        "integration_classification": integration,
        "merge_backlog_id": merge_backlog.get("merge_backlog_id"),
        "merge_backlog_action": merge_backlog.get("recommended_action"),
        "verification_gate": verification_gate,
        "verification_lane": verification.get("verification_lane")
        or merge_backlog.get("verification_lane"),
        "dashboard_linear_dry_run_marker": "ONE_AGENT_COMPLETED_WORK_TO_DASHBOARD_LINEAR_DRY_RUN_OK",
        "verified_pr_dry_run_marker": "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK",
        "pr_dry_run_marker": pr_dry_run.get("marker"),
        "linear_writeback_posted": (pr_dry_run.get("linear_writeback") or {}).get(
            "posted"
        ),
        "dry_run_only": pr_dry_run.get("dry_run_only"),
        "source_decision": source_decision,
        "native_acceptance": native_acceptance,
        "authorization": {
            "acceptance_authority": "native_provider_neutral_receipt",
            "merge_authorized": bool(native_acceptance.get("merge_authorized")),
            "deploy_authorized": bool(native_acceptance.get("deploy_authorized")),
            "hosted_signals_required": False,
        },
        "source_decision_gate": {
            "allows_promotion": source_allows_promotion,
            "required_status": "decision_ready",
            "required_policy_gate": "pass",
            "required_promotion_decision": "open_or_update_pr_dry_run_only",
            "requires_complete_retained_evidence": True,
        },
    }

    return PromotionDecision(
        promotion_decision_id=_ledger_id(completed_work_id),
        completed_work_id=completed_work_id,
        status=status,
        recommendation=recommendation,
        requested_by=requested_by or "operator",
        recorded_at=recorded_at or _now(),
        okf=okf,
        packet_classification=packet_classification,
        completed_work_classification=row_payload.get("classification"),
        integration_classification=integration,
        verification_gate=verification_gate,
        verification_lane=verification.get("verification_lane")
        or merge_backlog.get("verification_lane"),
        dry_run_pr_action=merge_backlog.get("recommended_action"),
        target_issue=row_payload.get("packet", {}).get("issue_identifier")
        or merge_backlog.get("issue_identifier"),
        evidence=evidence,
        source_decision=source_decision,
        side_effects=side_effects,
        non_claims=non_claims,
    )


def record_promotion_decision(
    completed_work_id: str,
    *,
    requested_by: str = "operator",
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist/update one deterministic decision record for a completed-work row."""

    decision = build_promotion_decision(completed_work_id, requested_by=requested_by)
    record = decision.as_dict()
    records = _read(state_path)
    records = [
        r
        for r in records
        if r.get("promotion_decision_id") != decision.promotion_decision_id
    ]
    records.insert(0, record)
    _write(records, state_path)
    return record


def _fail_closed_revalidation_view(
    record: dict[str, Any], reason: str
) -> dict[str, Any]:
    view = dict(record)
    view.setdefault("stored_legacy_record", not bool(record.get("source_decision")))
    view["revalidation_status"] = "held_by_current_evidence"
    view["revalidation_reason"] = reason
    view["stored_status"] = record.get("status")
    view["stored_recommendation"] = record.get("recommendation")
    view["status"] = "manual_review"
    view["recommendation"] = "manual_review"
    view.setdefault("source_decision", {})
    evidence = dict(view.get("evidence") or {})
    evidence["native_acceptance"] = {
        "status": "blocked",
        "reason": reason,
        "authoritative": True,
        "merge_authorized": False,
        "deploy_authorized": False,
    }
    evidence["authorization"] = {
        "acceptance_authority": "native_provider_neutral_receipt",
        "merge_authorized": False,
        "deploy_authorized": False,
        "hosted_signals_required": False,
    }
    evidence["current_evidence_revalidation"] = {
        "status": "failed_closed",
        "reason": reason,
        "completed_work_id": record.get("completed_work_id"),
    }
    view["evidence"] = evidence
    return view


def _revalidated_record_view(record: dict[str, Any]) -> dict[str, Any]:
    completed_work_id = str(record.get("completed_work_id") or "")
    if not completed_work_id:
        return _fail_closed_revalidation_view(record, "missing_completed_work_id")

    try:
        current = build_promotion_decision(
            completed_work_id,
            requested_by=str(record.get("requested_by") or "ledger-revalidation"),
            recorded_at=str(record.get("recorded_at") or "") or None,
        ).as_dict()
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return _fail_closed_revalidation_view(
            record, f"completed_work_revalidation_failed:{type(exc).__name__}"
        )

    source_gate = (current.get("evidence") or {}).get("source_decision_gate") or {}
    if (
        current.get("status") != "decision_ready"
        or current.get("recommendation") != "open_or_update_pr"
    ):
        view = _fail_closed_revalidation_view(
            record, "current_completed_work_evidence_not_decision_ready"
        )
        view["status"] = str(current.get("status") or "manual_review")
        if view["status"] == "decision_ready":
            view["status"] = "manual_review"
        current_recommendation = str(current.get("recommendation") or "manual_review")
        view["recommendation"] = (
            current_recommendation
            if current_recommendation != "open_or_update_pr"
            else "manual_review"
        )
        view["source_decision"] = current.get("source_decision") or {}
        view["target_issue"] = current.get("target_issue")
        evidence = dict(view.get("evidence") or {})
        current_evidence = current.get("evidence") or {}
        evidence["native_acceptance"] = current_evidence.get("native_acceptance")
        evidence["authorization"] = current_evidence.get("authorization")
        evidence["current_evidence_revalidation"] = {
            "status": "failed_closed",
            "reason": "current_completed_work_evidence_not_decision_ready",
            "completed_work_id": completed_work_id,
            "source_decision_gate": source_gate,
        }
        view["evidence"] = evidence
        return view

    view = dict(record)
    view.setdefault("stored_legacy_record", not bool(record.get("source_decision")))
    view["revalidation_status"] = "passed_current_evidence"
    view["revalidation_reason"] = "current_completed_work_evidence_decision_ready"
    view["source_decision"] = (
        current.get("source_decision") or record.get("source_decision") or {}
    )
    view["target_issue"] = current.get("target_issue")
    evidence = dict(view.get("evidence") or {})
    current_evidence = current.get("evidence") or {}
    evidence["native_acceptance"] = current_evidence.get("native_acceptance")
    evidence["authorization"] = current_evidence.get("authorization")
    evidence["current_evidence_revalidation"] = {
        "status": "passed",
        "reason": "current_completed_work_evidence_decision_ready",
        "completed_work_id": completed_work_id,
        "source_decision_gate": source_gate,
    }
    view["evidence"] = evidence
    return view


def list_promotion_decisions(
    *, limit: int = 50, state_path: str | Path | None = None
) -> list[dict[str, Any]]:
    records = _read(state_path)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    bounded = records[: max(1, min(limit, 200))]
    return [_revalidated_record_view(record) for record in bounded]


def get_promotion_decision(
    promotion_decision_id: str, *, state_path: str | Path | None = None
) -> dict[str, Any] | None:
    for record in _read(state_path):
        if record.get("promotion_decision_id") == promotion_decision_id:
            return _revalidated_record_view(record)
    return None


def latest_completed_work_id() -> str | None:
    rows = list_completed_work(limit=1)
    return rows[0].id if rows else None


def latest_or_record_decision(
    *, requested_by: str = "operator", state_path: str | Path | None = None
) -> dict[str, Any] | None:
    records = list_promotion_decisions(limit=1, state_path=state_path)
    if records:
        return records[0]
    completed_work_id = latest_completed_work_id()
    if not completed_work_id:
        return None
    return record_promotion_decision(
        completed_work_id, requested_by=requested_by, state_path=state_path
    )
