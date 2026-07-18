"""AGY merge backlog and PR verification gate.

This module turns persisted completed-work rows into deterministic, dry-run merge
backlog items. It never creates branches, opens PRs, merges, dispatches AGY jobs,
or deploys production.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from prismatic.agy_completed_work import (
    CompletedWorkRow,
    get_completed_work,
    list_completed_work,
)
from prismatic.completed_work_gate import GateClassification

AGY_CLEAN_PR_CREATE_UPDATE_MARKER = "AGY_CLEAN_PR_CREATE_UPDATE_OK"
AGY_PR_VERIFICATION_GATE_MARKER = "AGY_PR_VERIFICATION_GATE_OK"
AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER = "AGY_CLEAN_PR_AND_VERIFICATION_GATE_OK"
PROMPT5_PR_CANDIDATE_LIFECYCLE_MARKER = "PROMPT5_PR_CANDIDATE_LIFECYCLE_OK"
PROMPT5_OPERATOR_PR_DRY_RUN_MARKER = "PROMPT5_OPERATOR_PR_DRY_RUN_OK"
PROMPT5_REAL_PR_APPROVAL_GATE_MARKER = "PROMPT5_REAL_PR_APPROVAL_GATE_OK"

MERGE_BACKLOG_ACTIONS = {
    "open_or_update_pr",
    "clean_rebuild_required",
    "blocked_missing_proof",
    "blocked_failed_verification",
    "manual_review_scope",
    "manual_review_conflict",
    "superseded",
    "rejected",
}

_VERIFICATION_LANES = {
    "dashboard-ui",
    "backend-api",
    "docs",
    "research",
    "mixed",
    "manual-review",
    "unknown",
}

_CLASSIFICATION_TO_ACTION = {
    GateClassification.MERGE_READY.value: "open_or_update_pr",
    GateClassification.CLEAN_REBUILD_REQUIRED.value: "clean_rebuild_required",
    GateClassification.BLOCKED_MISSING_PROOF.value: "blocked_missing_proof",
    GateClassification.BLOCKED_FAILED_VERIFICATION.value: "blocked_failed_verification",
    GateClassification.MANUAL_REVIEW_SCOPE.value: "manual_review_scope",
    GateClassification.MANUAL_REVIEW_CONFLICT.value: "manual_review_conflict",
    GateClassification.SUPERSEDED.value: "superseded",
    GateClassification.REJECTED.value: "rejected",
}

LANE_POLICY: dict[str, dict[str, Any]] = {
    "dashboard-ui": {
        "required_terms": ("dashboard", "node --check"),
        "suggested_commands": (
            "node --check /tmp/hermes-dashboard-inline-agy-merge-backlog.js",
            "python3 -m pytest -q tests/test_agy_merge_backlog_api.py",
            "curl /api/gateway/agy/merge-backlog",
        ),
        "description": "Dashboard/UI proof must include JS syntax and dashboard/API route evidence.",
    },
    "backend-api": {
        "required_terms": ("pytest",),
        "suggested_commands": (
            "python3 -m pytest -q tests/test_agy_merge_backlog.py tests/test_agy_merge_backlog_api.py",
            "python3 -m py_compile prismatic/agy_merge_backlog.py prismatic/gateway/server.py",
        ),
        "description": "Backend/API proof must include focused pytest or API/TestClient evidence.",
    },
    "docs": {
        "required_terms": ("/tmp/",),
        "suggested_commands": ("python3 -m pytest -q tests/test_agy_merge_backlog.py",),
        "description": "Docs proof must point at durable artifact/source evidence and avoid runtime claims.",
    },
    "research": {
        "required_terms": ("/tmp/",),
        "suggested_commands": ("python3 -m pytest -q tests/test_agy_merge_backlog.py",),
        "description": "Research proof must cite artifact/source evidence and make no runtime claims.",
    },
    "mixed": {
        "required_terms": (),
        "suggested_commands": (),
        "description": "Mixed lane requires manual review; no clean PR plan is auto-approved.",
    },
    "manual-review": {
        "required_terms": (),
        "suggested_commands": (),
        "description": "Manual-review lane cannot pass the clean PR gate automatically.",
    },
    "unknown": {
        "required_terms": (),
        "suggested_commands": (),
        "description": "Unknown lane is blocked until classified.",
    },
}


@dataclass(frozen=True)
class VerificationGateDecision:
    verification_gate: str
    verification_lane: str
    verification_required: bool
    policy: dict[str, Any]
    reasons: tuple[str, ...]
    marker: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verification_gate": self.verification_gate,
            "verification_lane": self.verification_lane,
            "verification_required": self.verification_required,
            "policy": self.policy,
            "reasons": list(self.reasons),
            "marker": self.marker,
        }


@dataclass(frozen=True)
class MergeBacklogItem:
    merge_backlog_id: str
    completed_work_id: str
    issue_identifier: str | None
    source_branch: str | None
    base_branch: str
    changed_files: tuple[str, ...]
    classification: str
    recommended_action: str
    pr_branch: str
    pr_title: str
    pr_body: str
    verification_required: bool
    verification_lane: str
    verification_gate: str
    eligible_for_auto_merge: bool
    reasons: tuple[str, ...]
    dry_run: bool
    markers: tuple[str, ...]
    non_claims: tuple[str, ...]
    verification_policy: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "merge_backlog_id": self.merge_backlog_id,
            "completed_work_id": self.completed_work_id,
            "issue_identifier": self.issue_identifier,
            "source_branch": self.source_branch,
            "base_branch": self.base_branch,
            "changed_files": list(self.changed_files),
            "classification": self.classification,
            "recommended_action": self.recommended_action,
            "pr_branch": self.pr_branch,
            "pr_title": self.pr_title,
            "pr_body": self.pr_body,
            "verification_required": self.verification_required,
            "verification_lane": self.verification_lane,
            "verification_gate": self.verification_gate,
            "eligible_for_auto_merge": self.eligible_for_auto_merge,
            "reasons": list(self.reasons),
            "dry_run": self.dry_run,
            "markers": list(self.markers),
            "non_claims": list(self.non_claims),
            "verification_policy": self.verification_policy,
            "side_effects": {
                "git_mutation": False,
                "github_pr_created": False,
                "auto_merge": False,
                "production_deploy": False,
                "agy_dispatch": False,
            },
        }


def build_merge_backlog_item(row: CompletedWorkRow) -> MergeBacklogItem:
    packet = row.packet
    changed_files = tuple(
        str(path) for path in packet.get("changed_files", []) if isinstance(path, str)
    )
    classification = row.classification
    action = _CLASSIFICATION_TO_ACTION.get(classification, "rejected")
    lane = verification_lane_for_row(row)
    verification = evaluate_verification_gate(row, lane=lane)
    base_branch = _normalize_base_branch(
        row.base_branch or packet.get("base_branch") or "main"
    )
    issue_identifier = _issue_identifier(packet, row)
    merge_backlog_id = merge_backlog_id_for(row)
    pr_branch = deterministic_pr_branch(issue_identifier, row, changed_files)
    pr_title = deterministic_pr_title(issue_identifier, row, action)
    pr_body = deterministic_pr_body(row, action, lane, verification)
    reasons = list(row.gate.get("reasons") or [])
    reasons.extend(verification.reasons)
    if action != "open_or_update_pr":
        reasons.append(f"recommended action is {action}; no clean PR create/update")
    if verification.verification_gate != "pass":
        reasons.append(f"verification gate is {verification.verification_gate}")
    reasons.append("eligible_for_auto_merge is false in this slice")
    markers = [AGY_CLEAN_PR_CREATE_UPDATE_MARKER]
    if verification.marker == AGY_PR_VERIFICATION_GATE_MARKER:
        markers.append(AGY_PR_VERIFICATION_GATE_MARKER)
    if action == "open_or_update_pr" and verification.verification_gate == "pass":
        markers.append(AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER)
    return MergeBacklogItem(
        merge_backlog_id=merge_backlog_id,
        completed_work_id=row.id,
        issue_identifier=issue_identifier,
        source_branch=row.source_branch,
        base_branch=base_branch,
        changed_files=changed_files,
        classification=classification,
        recommended_action=action,
        pr_branch=pr_branch,
        pr_title=pr_title,
        pr_body=pr_body,
        verification_required=True,
        verification_lane=lane,
        verification_gate=verification.verification_gate,
        eligible_for_auto_merge=False,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        dry_run=True,
        markers=tuple(dict.fromkeys(markers)),
        non_claims=tuple(row.non_claims),
        verification_policy=verification.policy,
    )


def evaluate_verification_gate(
    row: CompletedWorkRow, *, lane: str | None = None
) -> VerificationGateDecision:
    lane = lane or verification_lane_for_row(row)
    if lane not in _VERIFICATION_LANES:
        lane = "unknown"
    policy = dict(LANE_POLICY[lane])
    policy["suggested_commands"] = list(policy.get("suggested_commands") or [])
    raw_proof = row.packet.get("proof")
    proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
    proof_result = str(proof.get("result") or row.proof_result or "").upper()
    command = str(proof.get("command") or "")
    scope = str(proof.get("scope") or "")
    log = str(proof.get("log") or "")
    marker = str(proof.get("marker") or row.proof_marker or "")
    text = "\n".join([command, scope, log, marker]).lower()
    reasons: list[str] = []
    if row.classification != GateClassification.MERGE_READY.value:
        reasons.append(f"completed-work classification is {row.classification}")
        gate = (
            "blocked"
            if row.classification.startswith("blocked")
            or row.classification
            in {"rejected", "superseded", "clean_rebuild_required"}
            else "manual_review"
        )
        return VerificationGateDecision(gate, lane, True, policy, tuple(reasons), None)
    if lane in {"mixed", "manual-review", "unknown"}:
        reasons.append(f"lane {lane} requires manual review")
        return VerificationGateDecision(
            "manual_review", lane, True, policy, tuple(reasons), None
        )
    if proof_result != "PASS":
        reasons.append(f"proof result is {proof_result or 'missing'}")
        return VerificationGateDecision(
            "blocked", lane, True, policy, tuple(reasons), None
        )
    if not log.startswith("/tmp/"):
        reasons.append("proof log must point to /tmp evidence")
        return VerificationGateDecision(
            "blocked", lane, True, policy, tuple(reasons), None
        )
    missing_terms = [
        term for term in policy.get("required_terms", ()) if term.lower() not in text
    ]
    if missing_terms:
        reasons.append("proof missing lane evidence: " + ", ".join(missing_terms))
        return VerificationGateDecision(
            "blocked", lane, True, policy, tuple(reasons), None
        )
    reasons.append(f"{lane} verification policy satisfied from completed-work proof")
    return VerificationGateDecision(
        "pass", lane, True, policy, tuple(reasons), AGY_PR_VERIFICATION_GATE_MARKER
    )


def _verification_gate_selection(item: MergeBacklogItem) -> dict[str, Any]:
    """Select a conservative dry-run verification gate for a future clean PR."""

    commands = [
        "$HOME/.local/bin/ruff check prismatic/agy_merge_backlog.py prismatic/gateway/server.py",
        "$HOME/.local/bin/ruff format --check prismatic/agy_merge_backlog.py prismatic/gateway/server.py",
        "python3 -m py_compile prismatic/agy_merge_backlog.py prismatic/gateway/server.py",
    ]
    test_targets = [
        "tests/test_agy_merge_backlog.py",
        "tests/test_agy_merge_backlog_api.py",
    ]
    if item.verification_lane in {"backend-api", "integration"}:
        commands.append(
            "PYTHONPATH=$PWD $HOME/.prismatic/venv_stable/bin/python -m pytest "
            + " ".join(test_targets)
            + " -q"
        )
        gate = "backend_api_focused"
    else:
        commands.append(
            "PYTHONPATH=$PWD $HOME/.prismatic/venv_stable/bin/python -m pytest tests/test_agy_merge_backlog.py -q"
        )
        gate = "completed_work_focused"
    return {
        "gate": gate,
        "status": "selected",
        "commands": commands,
        "required_before_real_pr": True,
        "ad_hoc_or_canonical": "ad-hoc targeted",
        "not_claiming": [
            "canonical_full_suite_green",
            "real_github_pr_created",
            "auto_merge_enabled",
        ],
    }


def build_operator_pr_creation_dry_run(
    completed_work_id: str,
    *,
    requested_by: str = "operator",
    action: str = "operator_pr_creation_dry_run",
    linear_writeback: bool = True,
) -> dict[str, Any]:
    """Build an operator-approved dry-run branch/PR plan without GitHub side effects.

    Prompt 5.3 continues the metadata-only lane: an explicit operator action can
    produce a branch/PR plan, verification gate selection, and Linear/dashboard
    writeback payload, but it still must not create branches, open GitHub PRs,
    enable auto-merge, dispatch AGY, or deploy production.
    """

    candidate = build_pr_candidate_lifecycle(
        completed_work_id, requested_by=requested_by, action="stage_pr_candidate"
    )
    item = get_merge_backlog_item(completed_work_id)
    allowed = candidate.get("status") == "ok"
    issue_slug = _slug(item.issue_identifier or item.completed_work_id)[:24]
    branch_name = f"agent/{issue_slug}-{item.completed_work_id[:8]}-dry-run"
    pr_title = (
        candidate.get("candidate", {}).get("title")
        or f"AGY completed work {item.issue_identifier}"
    )
    verification = _verification_gate_selection(item)
    linear_body = (
        "## Prompt 5.3 operator PR dry-run plan\n\n"
        "```text\n"
        f"RESULT={'PASS' if allowed else 'BLOCKED'}\n"
        f"MARKER={PROMPT5_OPERATOR_PR_DRY_RUN_MARKER if allowed else 'PROMPT5_OPERATOR_PR_DRY_RUN_BLOCKED'}\n"
        f"completed_work_id={item.completed_work_id}\n"
        f"planned_branch={branch_name}\n"
        f"verification_gate={verification['gate']}\n"
        "real_github_pr_created=false\n"
        "auto_merge_enabled=false\n"
        "production_deployed=false\n"
        "```"
    )
    return {
        "status": "ok" if allowed else "blocked",
        "marker": PROMPT5_OPERATOR_PR_DRY_RUN_MARKER
        if allowed
        else "PROMPT5_OPERATOR_PR_DRY_RUN_BLOCKED",
        "completed_work_id": item.completed_work_id,
        "merge_backlog_id": item.merge_backlog_id,
        "requested_by": requested_by or "operator",
        "operator_action": action or "operator_pr_creation_dry_run",
        "operator_approved_action": True,
        "dry_run_only": True,
        "candidate_metadata": candidate.get("candidate"),
        "branch_plan": {
            "base": "origin/main",
            "branch": branch_name,
            "checkout_command": f"git switch -C {branch_name} origin/main",
            "apply_completed_work_command": "DRY_RUN_ONLY: apply the completed-work artifact set after operator approval",
            "push_command": f"DRY_RUN_ONLY: git push -u origin {branch_name}",
            "executed": False,
        },
        "github_pr_plan": {
            "title": pr_title,
            "body": candidate.get("candidate", {}).get("body"),
            "base": "main",
            "head": branch_name,
            "create_command": f"DRY_RUN_ONLY: gh pr create --base main --head {branch_name} --title {pr_title!r}",
            "created": False,
        },
        "verification_gate_selection": verification,
        "dashboard_writeback": {
            "visible": True,
            "status": "ready" if allowed else "blocked",
            "marker": PROMPT5_OPERATOR_PR_DRY_RUN_MARKER
            if allowed
            else "PROMPT5_OPERATOR_PR_DRY_RUN_BLOCKED",
        },
        "linear_writeback": {
            "enabled": bool(linear_writeback),
            "target_issue": item.issue_identifier,
            "body": linear_body,
            "posted": False,
            "dry_run_payload_only": True,
        },
        "side_effects": {
            "git_branch_created": False,
            "github_pr_created": False,
            "auto_merge": False,
            "production_deploy": False,
            "agy_dispatch": False,
            "linear_comment_posted": False,
        },
        "non_claims": {
            "real_github_pr_created": False,
            "git_branch_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "agy_dispatch": False,
        },
        "reasons": candidate.get("reasons", []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _real_pr_approval_token(completed_work_id: str) -> str:
    return f"APPROVE_REAL_PR:{completed_work_id}"


def build_real_pr_creation_approval_gate(
    completed_work_id: str,
    *,
    requested_by: str = "operator",
    approved_by: str | None = None,
    approval_token: str | None = None,
    approval_note: str | None = None,
    action: str = "record_real_pr_creation_approval",
    expose_real_pr_action: bool = True,
) -> dict[str, Any]:
    """Record an explicit operator approval gate for future real PR creation.

    Prompt 5.4 is still side-effect-free by default. This function records the
    approval/policy decision in the returned payload and exposes a separate
    approved-action plan only after the dry-run plan is valid, the policy gate
    passes, and the operator supplies the exact approval token. It does not
    create branches, open GitHub PRs, post Linear comments, enable auto-merge,
    dispatch AGY, or deploy production.
    """

    dry_run = build_operator_pr_creation_dry_run(
        completed_work_id,
        requested_by=requested_by,
        action="operator_pr_creation_dry_run",
    )
    item = get_merge_backlog_item(completed_work_id)
    expected_token = _real_pr_approval_token(item.completed_work_id)
    approval_valid = bool(approved_by) and approval_token == expected_token
    dry_run_ready = (
        dry_run.get("status") == "ok" and dry_run.get("dry_run_only") is True
    )
    verification_gate = dry_run.get("verification_gate_selection", {})
    policy_checks = {
        "dry_run_plan_ready": dry_run_ready,
        "verification_gate_selected": verification_gate.get("status") == "selected",
        "verification_required_before_real_pr": verification_gate.get(
            "required_before_real_pr"
        )
        is True,
        "scope_is_merge_ready": item.classification
        == GateClassification.MERGE_READY.value,
        "recommended_action_open_or_update_pr": item.recommended_action
        == "open_or_update_pr",
        "approval_token_matches": approval_valid,
        "auto_merge_disabled": dry_run.get("side_effects", {}).get("auto_merge")
        is False,
        "production_deploy_disabled": dry_run.get("side_effects", {}).get(
            "production_deploy"
        )
        is False,
    }
    policy_passed = all(policy_checks.values())
    approval_id_source = (
        f"{item.completed_work_id}:{approved_by or 'unapproved'}:{approval_token or ''}"
    )
    approval_id = (
        "real-pr-approval-"
        + hashlib.sha256(approval_id_source.encode()).hexdigest()[:16]
    )
    blocked_reasons = [name for name, ok in policy_checks.items() if not ok]
    real_pr_action_exposed = bool(expose_real_pr_action and policy_passed)
    create_command = dry_run.get("github_pr_plan", {}).get("create_command", "")
    approved_command = create_command.replace(
        "DRY_RUN_ONLY: ", "APPROVED_ACTION_ONLY: ", 1
    )
    linear_body = (
        "## Prompt 5.4 real PR creation approval gate\n\n"
        "```text\n"
        f"RESULT={'PASS' if policy_passed else 'BLOCKED'}\n"
        f"MARKER={PROMPT5_REAL_PR_APPROVAL_GATE_MARKER if policy_passed else 'PROMPT5_REAL_PR_APPROVAL_GATE_BLOCKED'}\n"
        f"completed_work_id={item.completed_work_id}\n"
        f"approval_id={approval_id}\n"
        f"approved_by={approved_by or 'missing'}\n"
        f"real_pr_action_exposed={str(real_pr_action_exposed).lower()}\n"
        "real_github_pr_created=false\n"
        "git_branch_created=false\n"
        "auto_merge_enabled=false\n"
        "production_deployed=false\n"
        "```"
    )
    return {
        "status": "ok" if policy_passed else "blocked",
        "marker": PROMPT5_REAL_PR_APPROVAL_GATE_MARKER
        if policy_passed
        else "PROMPT5_REAL_PR_APPROVAL_GATE_BLOCKED",
        "completed_work_id": item.completed_work_id,
        "merge_backlog_id": item.merge_backlog_id,
        "requested_by": requested_by or "operator",
        "operator_action": action or "record_real_pr_creation_approval",
        "approval_record": {
            "approval_id": approval_id,
            "approved": policy_passed,
            "approved_by": approved_by,
            "approval_note": approval_note or "",
            "approval_token_hint": expected_token,
            "approval_token_matched": approval_valid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "policy_gate": {
            "status": "pass" if policy_passed else "blocked",
            "checks": policy_checks,
            "blocked_reasons": blocked_reasons,
            "scope_confirmed": policy_checks["scope_is_merge_ready"],
            "proof_confirmed": policy_checks["verification_gate_selected"]
            and policy_checks["verification_required_before_real_pr"],
            "requires_separate_approved_action": True,
        },
        "dry_run_plan": dry_run,
        "real_pr_creation_action": {
            "exposed": real_pr_action_exposed,
            "endpoint": f"/api/gateway/agy/merge-backlog/{item.completed_work_id}/pr-create-approved",
            "method": "POST",
            "requires_approval_id": approval_id,
            "requires_final_operator_trigger": True,
            "command": approved_command if real_pr_action_exposed else None,
            "executed": False,
            "github_pr_created": False,
        },
        "dashboard_writeback": {
            "visible": True,
            "status": "approved-action-ready" if policy_passed else "blocked",
            "marker": PROMPT5_REAL_PR_APPROVAL_GATE_MARKER
            if policy_passed
            else "PROMPT5_REAL_PR_APPROVAL_GATE_BLOCKED",
        },
        "linear_writeback": {
            "enabled": True,
            "target_issue": item.issue_identifier,
            "body": linear_body,
            "posted": False,
            "dry_run_payload_only": True,
        },
        "side_effects": {
            "git_branch_created": False,
            "github_pr_created": False,
            "auto_merge": False,
            "production_deploy": False,
            "agy_dispatch": False,
            "linear_comment_posted": False,
        },
        "non_claims": {
            "real_github_pr_created": False,
            "git_branch_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "agy_dispatch": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_real_pr_creation_approved_action(
    completed_work_id: str,
    *,
    approval_id: str | None = None,
    approved_by: str | None = None,
    approval_token: str | None = None,
    requested_by: str = "operator",
) -> dict[str, Any]:
    """Expose the separate approved real-PR action without executing it.

    This is the final pre-execution surface. It requires the Prompt 5.4 approval
    gate to pass and the approval id to match. It still does not run git or gh;
    an actual side-effecting executor must be a future separately authorized
    implementation.
    """

    gate = build_real_pr_creation_approval_gate(
        completed_work_id,
        requested_by=requested_by,
        approved_by=approved_by,
        approval_token=approval_token,
        action="real_pr_creation_approved_action",
    )
    approval_matches = approval_id == gate.get("approval_record", {}).get("approval_id")
    allowed = gate.get("status") == "ok" and approval_matches
    action_payload = dict(gate.get("real_pr_creation_action", {}))
    action_payload.update(
        {
            "status": "ready_for_future_executor" if allowed else "blocked",
            "approval_id_matched": approval_matches,
            "execution_implemented": False,
            "executed": False,
            "github_pr_created": False,
        }
    )
    return {
        "status": "ok" if allowed else "blocked",
        "marker": PROMPT5_REAL_PR_APPROVAL_GATE_MARKER
        if allowed
        else "PROMPT5_REAL_PR_APPROVED_ACTION_BLOCKED",
        "completed_work_id": completed_work_id,
        "approval_gate": gate,
        "real_pr_creation_action": action_payload,
        "side_effects": {
            "git_branch_created": False,
            "github_pr_created": False,
            "auto_merge": False,
            "production_deploy": False,
            "agy_dispatch": False,
            "linear_comment_posted": False,
        },
        "non_claims": {
            "real_github_pr_created": False,
            "git_branch_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "agy_dispatch": False,
        },
    }


def list_merge_backlog(
    *, db_path: str | Path | None = None, limit: int = 50
) -> list[MergeBacklogItem]:
    return [
        build_merge_backlog_item(row)
        for row in list_completed_work(db_path=db_path, limit=limit)
    ]


def get_merge_backlog_item(
    completed_work_id: str, *, db_path: str | Path | None = None
) -> MergeBacklogItem:
    return build_merge_backlog_item(
        get_completed_work(completed_work_id, db_path=db_path)
    )


def verify_merge_backlog_item(
    completed_work_id: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    item = get_merge_backlog_item(completed_work_id, db_path=db_path)
    return {
        "status": "ok",
        "marker": AGY_PR_VERIFICATION_GATE_MARKER
        if item.verification_gate == "pass"
        else "AGY_CLEAN_PR_VERIFICATION_GATE_BLOCKED",
        "merge_backlog": item.as_dict(),
        "verification_gate": item.verification_gate,
        "eligible_for_auto_merge": False,
        "non_claims": {
            "auto_merge": False,
            "production_deploy": False,
            "github_pr_created": False,
            "agy_dispatch": False,
        },
    }


def build_pr_candidate_lifecycle(
    completed_work_id: str,
    *,
    requested_by: str = "operator",
    action: str = "stage_pr_candidate",
) -> dict[str, Any]:
    """Build explicit operator-action PR candidate metadata without side effects.

    This is Prompt 5.2's safe lifecycle surface: it converts a completed-work row
    into deterministic clean-PR candidate metadata only after an explicit operator
    action. It does not create git branches, open GitHub PRs, enable auto-merge,
    dispatch AGY, or deploy production.
    """

    item = get_merge_backlog_item(completed_work_id)
    gate = verify_merge_backlog_item(completed_work_id)
    allowed = (
        item.recommended_action == "open_or_update_pr"
        and gate.get("verification_gate") == "pass"
        and item.classification == GateClassification.MERGE_READY.value
    )
    lifecycle_state = "candidate_metadata_ready" if allowed else "blocked"
    reasons = list(item.reasons)
    if not allowed:
        reasons.append("completed work is not eligible for clean PR candidate metadata")

    return {
        "status": "ok" if allowed else "blocked",
        "marker": PROMPT5_PR_CANDIDATE_LIFECYCLE_MARKER,
        "completed_work_id": item.completed_work_id,
        "merge_backlog_id": item.merge_backlog_id,
        "requested_by": requested_by or "operator",
        "operator_action": action or "stage_pr_candidate",
        "operator_action_required": True,
        "lifecycle_state": lifecycle_state,
        "candidate": {
            "pr_branch": item.pr_branch,
            "pr_title": item.pr_title,
            "pr_body": item.pr_body,
            "base_branch": item.base_branch,
            "source_branch": item.source_branch,
            "changed_files": list(item.changed_files),
            "recommended_action": item.recommended_action,
            "verification_gate": item.verification_gate,
            "verification_lane": item.verification_lane,
            "eligible_for_auto_merge": False,
        },
        "verification": gate,
        "reasons": reasons,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "side_effects": {
            "candidate_metadata_created": allowed,
            "git_mutation": False,
            "github_pr_created": False,
            "auto_merge": False,
            "production_deploy": False,
            "agy_dispatch": False,
        },
        "non_claims": {
            "auto_merge_enabled": False,
            "production_deployed": False,
            "real_github_pr_created": False,
            "git_branch_created": False,
            "agy_dispatch": False,
        },
    }


def verification_lane_for_row(row: CompletedWorkRow) -> str:
    packet = row.packet
    explicit = (
        str(packet.get("verification_lane") or packet.get("lane") or "").strip().lower()
    )
    if explicit in _VERIFICATION_LANES:
        return explicit
    changed_files = [
        str(path) for path in packet.get("changed_files", []) if isinstance(path, str)
    ]
    if not changed_files:
        return "unknown"
    lanes = {_lane_for_path(path) for path in changed_files}
    lanes.discard("unknown")
    if not lanes:
        return "unknown"
    if len(lanes) == 1:
        return next(iter(lanes))
    if lanes == {"docs", "research"}:
        return "research"
    return "mixed"


def deterministic_pr_branch(
    issue_identifier: str | None, row: CompletedWorkRow, changed_files: Sequence[str]
) -> str:
    issue = _slug(issue_identifier or "agy")
    digest = hashlib.sha256(
        json.dumps(
            {"id": row.id, "files": list(changed_files)}, sort_keys=True
        ).encode()
    ).hexdigest()[:8]
    return f"feature/agy-clean-pr-{issue}-{digest}"


def deterministic_pr_title(
    issue_identifier: str | None, row: CompletedWorkRow, action: str
) -> str:
    prefix = f"{issue_identifier}: " if issue_identifier else ""
    if action == "open_or_update_pr":
        return f"{prefix}Integrate AGY completed work"
    return f"{prefix}Review AGY completed work ({action})"


def deterministic_pr_body(
    row: CompletedWorkRow,
    action: str,
    lane: str,
    verification: VerificationGateDecision,
) -> str:
    raw_proof = row.packet.get("proof")
    proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
    changed = (
        "\n".join(
            f"- `{path}`"
            for path in row.packet.get("changed_files", [])
            if isinstance(path, str)
        )
        or "- none provided"
    )
    non_claims = ", ".join(row.non_claims) or "none provided"
    return "\n".join(
        [
            "## AGY completed-work dry-run PR plan",
            "",
            f"Completed work: `{row.id}`",
            f"Classification: `{row.classification}`",
            f"Recommended action: `{action}`",
            f"Verification lane: `{lane}`",
            f"Verification gate: `{verification.verification_gate}`",
            "",
            "## Changed files",
            changed,
            "",
            "## Proof",
            f"- result: `{proof.get('result')}`",
            f"- command: `{proof.get('command')}`",
            f"- log: `{proof.get('log')}`",
            f"- scope: `{proof.get('scope')}`",
            f"- marker: `{proof.get('marker')}`",
            "",
            "## Non-claims / disabled side effects",
            f"- packet non-claims: {non_claims}",
            "- auto_merge: false",
            "- production_deploy: false",
            "- github_pr_created: false",
            "- agy_dispatch: false",
        ]
    )


def merge_backlog_id_for(row: CompletedWorkRow) -> str:
    digest = hashlib.sha256(row.id.encode()).hexdigest()[:16]
    return f"agy-mb-{digest}"


def _lane_for_path(path: str) -> str:
    p = path.strip()
    if p.startswith("docs/") or p.endswith(".md"):
        return "docs"
    if (
        p.startswith("research/")
        or p.startswith("scripts/reports/")
        or p.startswith("scripts/audits/")
    ):
        return "research"
    if "gateway/templates/" in p or p.endswith((".html", ".css", ".js", ".ts", ".tsx")):
        return "dashboard-ui"
    if p.startswith("prismatic/") or p.startswith("tests/") or p.startswith("scripts/"):
        return "backend-api"
    return "unknown"


def _issue_identifier(packet: Mapping[str, Any], row: CompletedWorkRow) -> str | None:
    for key in ("issue_identifier", "issue", "linear_issue", "linear_id"):
        value = packet.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = " ".join(
        str(value) for value in [row.source_branch, row.source_path, row.id] if value
    )
    match = re.search(r"[A-Z]{2,10}-\d+", text)
    return match.group(0) if match else None


def _normalize_base_branch(base: str) -> str:
    base = str(base or "main").strip()
    if base in {"origin/main", "main"}:
        return "main"
    return base


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "agy"
