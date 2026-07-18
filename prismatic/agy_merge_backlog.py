"""AGY merge backlog and PR verification gate.

This module turns persisted completed-work rows into deterministic, dry-run merge
backlog items. It never creates branches, opens PRs, merges, dispatches AGY jobs,
or deploys production.
"""

from __future__ import annotations

import hashlib
import json
import re
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
