"""Foundation / Peer Review dashboard read models.

This adapter converts existing run records and dashboard control state into a
stable API contract for the Prismatic governance dashboard. It does not launch
Jules, Ned, or AGY; browser controls are audit-only operator intents.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

FOUNDATION_AGENTS = ("jules", "ned", "agy")
DEFAULT_LIMITS = {
    "jules": 300,
    "ned": 300,
    "agy": 300,
}
CONTROL_ACTIONS: dict[str, dict[str, str]] = {
    "orchestrate": {
        "label": "Run peer review orchestrator",
        "status": "queued_for_operator_review",
        "severity": "info",
        "detail": "Operator requested peer-review orchestration. No browser shell execution was performed.",
    },
    "sync": {
        "label": "Verify sync / git status",
        "status": "sync_check_requested",
        "severity": "info",
        "detail": "Operator requested Foundation sync status verification. No browser shell execution was performed.",
    },
}


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    data: dict[str, Any] = {}
    for key in (
        "run_id",
        "issue_id",
        "agent_name",
        "status",
        "started_at",
        "completed_at",
        "output_path",
        "error_message",
        "evidence",
        "verification_status",
        "verification_scope",
        "cleanup_status",
    ):
        data[key] = getattr(record, key, None)
    return data


def _agent_name(record: dict[str, Any]) -> str:
    raw = str(record.get("agent_name") or "").strip().lower()
    if raw.startswith("agent:"):
        raw = raw.split(":", 1)[1]
    if raw.startswith("agent::"):
        raw = raw.split("::", 1)[1]
    return raw


def _iso_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _newest_key(record: dict[str, Any]) -> str:
    return str(record.get("completed_at") or record.get("started_at") or "")


def foundation_peer_review_payload(
    records: list[Any],
    control_state: dict[str, Any] | None = None,
    *,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return normalized Foundation / Peer Review dashboard state."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    control_state = control_state or {}
    normalized = [_record_to_dict(r) for r in records]
    peer_records = [r for r in normalized if _agent_name(r) in FOUNDATION_AGENTS]
    by_agent: dict[str, list[dict[str, Any]]] = {agent: [] for agent in FOUNDATION_AGENTS}
    for record in peer_records:
        by_agent[_agent_name(record)].append(record)
    counts = {agent: len(items) for agent, items in by_agent.items()}
    status_counts = {
        agent: dict(Counter(str(item.get("status") or "unknown").lower() for item in items))
        for agent, items in by_agent.items()
    }
    all_sorted = sorted(peer_records, key=_newest_key, reverse=True)
    last_activity = _iso_or_none(_newest_key(all_sorted[0])) if all_sorted else None
    recent_activity = [
        {
            "run_id": item.get("run_id"),
            "issue_id": item.get("issue_id"),
            "agent_name": _agent_name(item),
            "status": item.get("status") or "unknown",
            "started_at": item.get("started_at"),
            "completed_at": item.get("completed_at"),
            "output_path": item.get("output_path"),
            "error_message": item.get("error_message"),
            "verification_status": item.get("verification_status"),
            "verification_scope": item.get("verification_scope"),
            "cleanup_status": item.get("cleanup_status"),
        }
        for item in all_sorted[:12]
    ]
    # Keep AGY routing explicit: if AGY has active work, AGY remains the current
    # reviewer; otherwise route review pressure toward Jules when Jules has work,
    # then Ned. The route is evidence-derived, not hardcoded fake success data.
    active_agy = any(str(item.get("status") or "").lower() in {"pending", "running", "processing"} for item in by_agent["agy"])
    if active_agy:
        current_reviewer = "agent:agy"
        reviewer_reason = "agy has active peer-review work"
    elif by_agent["jules"]:
        current_reviewer = "agent:jules"
        reviewer_reason = "latest review pressure routes to jules evidence"
    elif by_agent["ned"]:
        current_reviewer = "agent:ned"
        reviewer_reason = "latest review pressure routes to ned evidence"
    else:
        current_reviewer = "none"
        reviewer_reason = "no Jules/Ned/AGY peer-review run evidence"

    last_control = control_state.get("last_action") or None
    return {
        "source": "run_records+foundation_control_state",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "empty": not bool(peer_records),
        "jules_count": counts["jules"],
        "ned_count": counts["ned"],
        "agy_count": counts["agy"],
        "counts": counts,
        "status_counts": status_counts,
        "limits": limits,
        "jules_limit": limits["jules"],
        "ned_limit": limits["ned"],
        "agy_limit": limits["agy"],
        "current_agy_reviewer": current_reviewer,
        "reviewer_reason": reviewer_reason,
        "last_activity_at": last_activity,
        "recent_activity": recent_activity,
        "last_control_action": last_control,
        "control_actions": sorted(CONTROL_ACTIONS.keys()),
        "evidence": {
            "run_record_count": len(peer_records),
            "agents_seen": [agent for agent, count in counts.items() if count],
            "state_actions": len(control_state.get("actions", []) or []),
        },
    }


def foundation_control_entry(action: str, *, now: str, actor: str = "dashboard") -> dict[str, Any]:
    """Return a persisted audit entry for an allowlisted Foundation control."""
    spec = CONTROL_ACTIONS[action]
    return {
        "id": f"foundation-{action}-{now.replace(':', '').replace('-', '').replace('.', '')}",
        "action": action,
        "label": spec["label"],
        "status": spec["status"],
        "detail": spec["detail"],
        "actor": actor,
        "created_at": now,
    }
