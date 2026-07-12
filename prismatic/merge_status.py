"""Merge Pipeline dashboard read models.

This adapter normalizes existing merge-pipeline state for the governance
dashboard. It reads state; it does not perform merges, push branches, or shell
out from browser-triggered controls.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MERGE_STATE_PATH = Path("/home/ubuntu/.prismatic/merge-pipeline/state_v6.json")
CONTROL_ACTIONS: dict[str, dict[str, str]] = {
    "refresh": {
        "label": "Request merge status refresh",
        "status": "refresh_requested",
        "severity": "info",
        "detail": "Operator requested merge status refresh. No browser shell execution was performed.",
    },
    "promote": {
        "label": "Request canonical promote review",
        "status": "promote_review_requested",
        "severity": "warning",
        "detail": "Operator requested canonical promote review. Merge remains gated outside the browser.",
    },
    "hold": {
        "label": "Hold merge queue",
        "status": "hold_requested",
        "severity": "warning",
        "detail": "Operator requested merge queue hold. No git operation was performed.",
    },
}


def merge_state_path() -> Path:
    raw = os.environ.get("PRISMATIC_MERGE_STATE_PATH")
    return Path(raw).expanduser() if raw else DEFAULT_MERGE_STATE_PATH


def load_merge_state(path: Path | None = None) -> tuple[dict[str, Any], str]:
    resolved = path or merge_state_path()
    if not resolved.exists():
        return {}, str(resolved)
    data = json.loads(resolved.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}, str(resolved)


def _as_list_map(mapping: Any, *, limit: int = 50, merged: bool = False) -> list[dict[str, Any]]:
    if not isinstance(mapping, dict):
        return []
    rows: list[dict[str, Any]] = []
    for ticket, details in mapping.items():
        details = details if isinstance(details, dict) else {}
        if merged:
            rows.append({
                "ticket": ticket,
                "tier": details.get("tier") or details.get("merge_tier") or "",
                "commit": details.get("commit") or "",
                "timestamp": details.get("merged_at") or details.get("timestamp") or "",
                "files": details.get("files") or [],
            })
            continue
        contention = details.get("contention_files") or details.get("contention") or []
        files = details.get("files") or []
        confidence = float(details.get("confidence") or 0)
        checks = details.get("checks") or {}
        status = str(details.get("status") or "").lower()
        blocked_reason = details.get("blocked_reason") or details.get("reason")
        is_conflict = bool(contention) or "conflict" in status or "blocked" in status
        checks_passed = checks.get("passed") if isinstance(checks, dict) else None
        mergeable = bool(details.get("mergeable")) or (confidence >= 80 and not is_conflict and checks_passed is not False)
        if blocked_reason:
            mergeable = False
        rows.append({
            "ticket": ticket,
            "tier": details.get("tier") or details.get("merge_tier") or "",
            "confidence": confidence,
            "modified": details.get("modified_files") or len(files),
            "files": files,
            "contention": contention,
            "contention_files": contention,
            "diff_lines": details.get("diff_lines") or 0,
            "mergeable": mergeable,
            "conflict": is_conflict,
            "blocked": bool(blocked_reason) or is_conflict,
            "blocked_reason": blocked_reason or ("contention files present" if is_conflict else ""),
            "checks": checks if isinstance(checks, dict) else {},
        })
    return sorted(rows, key=lambda row: (not row.get("blocked"), -float(row.get("confidence") or 0), str(row.get("ticket"))))[:limit]


def _ticket_family(ticket: str) -> str:
    nums = re.findall(r"GRO-(\d+)", ticket.upper())
    if not nums:
        return ticket
    # Group near-neighbor tickets into duplicate-family candidates when the
    # historic triage map did not already name the family.
    n = int(nums[0])
    return f"GRO-{n // 10 * 10}s"


def _duplicate_families(pending_rows: list[dict[str, Any]], triage: dict[str, Any] | None) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []
    triage = triage or {}
    canonical = triage.get("canonical_winners") if isinstance(triage, dict) else []
    if isinstance(canonical, list):
        for item in canonical:
            if not isinstance(item, dict):
                continue
            families.append({
                "family": item.get("family") or "unknown",
                "canonical_winner": item.get("winner") or "",
                "siblings": item.get("siblings") or [],
                "reason": item.get("reason") or "",
                "source": "governance_triage",
            })
    grouped: dict[str, list[str]] = {}
    for row in pending_rows:
        contention = row.get("contention") or []
        if len(contention) == 0 and not row.get("blocked"):
            continue
        grouped.setdefault(_ticket_family(str(row.get("ticket") or "")), []).append(str(row.get("ticket") or ""))
    known = {str(f.get("family")) for f in families}
    for family, tickets in sorted(grouped.items()):
        if len(tickets) > 1 and family not in known:
            families.append({
                "family": family,
                "canonical_winner": "",
                "siblings": tickets,
                "reason": "pending items share a nearby ticket family and contention signal",
                "source": "state_heuristic",
            })
    return families


def merge_status_payload(
    state: dict[str, Any],
    control_state: dict[str, Any] | None = None,
    triage: dict[str, Any] | None = None,
    *,
    state_path: str | None = None,
) -> dict[str, Any]:
    control_state = control_state or {}
    pending_rows = _as_list_map(state.get("pending"), limit=200)
    merged_rows = _as_list_map(state.get("merged"), limit=40, merged=True)
    pending_count = len(state.get("pending") or {}) if isinstance(state.get("pending"), dict) else 0
    merged_count = int(state.get("total_merged") or len(state.get("merged") or {}))
    blocked_count = sum(1 for row in pending_rows if row.get("blocked") or row.get("conflict"))
    conflict_count = sum(1 for row in pending_rows if row.get("conflict"))
    mergeable_count = sum(1 for row in pending_rows if row.get("mergeable"))
    duplicate_families = _duplicate_families(pending_rows, triage)
    latest_activity = state.get("last_apply") or state.get("last_scan") or None
    last_control = control_state.get("last_action") or None
    return {
        "source": "merge_state+governance_triage+merge_control_state",
        "state_path": state_path or str(merge_state_path()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "empty": pending_count == 0 and merged_count == 0,
        "status": "blocked" if blocked_count else ("mergeable" if mergeable_count else "empty" if pending_count == 0 else "review_required"),
        "pending_count": pending_count,
        "open_count": pending_count,
        "merged_count": merged_count,
        "mergeable_count": mergeable_count,
        "blocked_count": blocked_count,
        "conflict_count": conflict_count,
        "checks": {
            "passed": sum(1 for row in pending_rows if row.get("checks", {}).get("passed") is True),
            "failed": sum(1 for row in pending_rows if row.get("checks", {}).get("passed") is False),
            "unknown": sum(1 for row in pending_rows if row.get("checks", {}).get("passed") is None),
        },
        "duplicate_families": duplicate_families,
        "duplicate_family_count": len(duplicate_families),
        "pending": pending_rows[:50],
        "merged": merged_rows,
        "last_scan": state.get("last_scan"),
        "last_apply": state.get("last_apply"),
        "latest_activity_at": latest_activity,
        "drift_detected": bool(state.get("drift_detected")),
        "last_control_action": last_control,
        "control_actions": sorted(CONTROL_ACTIONS.keys()),
        "evidence": {
            "state_keys": sorted(state.keys()),
            "pending_rows_sampled": len(pending_rows),
            "merged_rows_sampled": len(merged_rows),
            "triage_source_issue": (triage or {}).get("source_issue") if isinstance(triage, dict) else None,
            "control_actions_recorded": len(control_state.get("actions", []) or []),
        },
    }


def merge_control_entry(action: str, *, now: str, actor: str = "dashboard") -> dict[str, Any]:
    spec = CONTROL_ACTIONS[action]
    return {
        "id": f"merge-{action}-{now.replace(':', '').replace('-', '').replace('.', '')}",
        "action": action,
        "label": spec["label"],
        "status": spec["status"],
        "detail": spec["detail"],
        "actor": actor,
        "created_at": now,
    }
