"""Normalized Ingestion Queue / Dispatcher read models for the gateway dashboard.

This module is intentionally adapter-only. It does not launch workers or mutate
real dispatcher state; it converts existing gateway counters, run records, and
control ledgers into stable dashboard contracts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATUSES = {"pending", "processing", "running", "completed", "success", "failed", "error"}
_FAILURE_TAXONOMY = [
    {
        "id": "ingest_auth",
        "label": "Ingest Auth",
        "description": "Webhook signatures or allowed-source checks failed before queueing.",
        "severity": "error",
    },
    {
        "id": "ingest_parse",
        "label": "Ingest Parse",
        "description": "Payload was received but could not be normalized into an event/task.",
        "severity": "warning",
    },
    {
        "id": "routing",
        "label": "Routing",
        "description": "Event accepted, but no agent/lane/workspace route could be selected.",
        "severity": "warning",
    },
    {
        "id": "execution",
        "label": "Execution",
        "description": "Agent run failed or remains blocked after dispatch.",
        "severity": "error",
    },
    {
        "id": "artifact",
        "label": "Artifact",
        "description": "Expected output, proof, branch, or file artifact is missing or invalid.",
        "severity": "warning",
    },
    {
        "id": "state_sync",
        "label": "State Sync",
        "description": "Dashboard state, event bus, run store, or recovery ledger disagree.",
        "severity": "warning",
    },
    {
        "id": "label_debt",
        "label": "Label Debt",
        "description": "Linear labels or queue markers are stale, contradictory, or unactionable.",
        "severity": "warning",
    },
    {
        "id": "silent_stall",
        "label": "Silent Stall",
        "description": "Queued/running work exists without recent dispatcher or run movement.",
        "severity": "error",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _age_seconds(value: Any) -> int | None:
    dt = _iso_to_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))


def _run_value(run: Any, name: str, default: Any = None) -> Any:
    if isinstance(run, dict):
        return run.get(name, default)
    return getattr(run, name, default)


def normalize_queue_item(run: Any) -> dict[str, Any]:
    status = str(_run_value(run, "status", "pending") or "pending").lower()
    if status == "running":
        dispatch_status = "processing"
    elif status == "success":
        dispatch_status = "completed"
    elif status == "error":
        dispatch_status = "failed"
    else:
        dispatch_status = status if status in _STATUSES else "pending"
    queued_at = _run_value(run, "started_at", None) or _run_value(run, "created_at", None) or ""
    completed_at = _run_value(run, "completed_at", None)
    return {
        "id": str(_run_value(run, "run_id", "") or _run_value(run, "id", "")),
        "run_id": str(_run_value(run, "run_id", "") or ""),
        "issue_id": str(_run_value(run, "issue_id", "") or ""),
        "agent_name": str(_run_value(run, "agent_name", "unknown") or "unknown"),
        "action": "dispatch",
        "dispatch_status": dispatch_status,
        "status": dispatch_status,
        "queued_at": queued_at,
        "updated_at": completed_at or queued_at,
        "completed_at": completed_at,
        "error_message": _run_value(run, "error_message", None),
        "failure_category": str(_run_value(run, "failure_category", "none") or "none"),
        "verification_status": str(_run_value(run, "verification_status", "self_reported") or "self_reported"),
        "payload": {
            "issue_id": _run_value(run, "issue_id", ""),
            "agent_name": _run_value(run, "agent_name", "unknown"),
            "output_path": _run_value(run, "output_path", None),
        },
    }


def queue_payload(run_records: list[Any], *, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    items = [normalize_queue_item(run) for run in run_records]
    if status:
        wanted = status.lower()
        items = [item for item in items if item.get("dispatch_status") == wanted]
    items = items[: max(1, min(int(limit or 50), 500))]
    depths = Counter(item.get("dispatch_status") or "pending" for item in (normalize_queue_item(run) for run in run_records))
    for key in ("pending", "processing", "completed", "failed"):
        depths.setdefault(key, 0)
    return {
        "source": "run_records",
        "generated_at": utc_now(),
        "total": len(items),
        "unfiltered_total": len(run_records),
        "status_filter": status,
        "queue_depths": dict(depths),
        "items": items,
        "empty": len(items) == 0,
    }


def webhook_stats_payload(counters: dict[str, int], run_records: list[Any]) -> dict[str, Any]:
    depths = queue_payload(run_records, limit=500)["queue_depths"]
    github_received = int(counters.get("github_received", 0) or 0)
    linear_received = int(counters.get("linear_received", 0) or 0)
    github_auth_failed = int(counters.get("github_auth_failed", 0) or 0)
    linear_auth_failed = int(counters.get("linear_auth_failed", 0) or 0)
    github_published = int(counters.get("github_published", 0) or 0)
    linear_published = int(counters.get("linear_published", 0) or 0)
    processed = int(depths.get("completed", 0) or 0) + github_published + linear_published
    failed = int(depths.get("failed", 0) or 0) + github_auth_failed + linear_auth_failed
    return {
        "source": "gateway_counters+run_records",
        "generated_at": utc_now(),
        "received": github_received + linear_received,
        "auth_failed": github_auth_failed + linear_auth_failed,
        "queued": int(depths.get("pending", 0) or 0),
        "processed": processed,
        "failed": failed,
        "latency_ms": None,
        "queue_depths": {
            "pending": int(depths.get("pending", 0) or 0),
            "processing": int(depths.get("processing", 0) or 0),
            "completed": int(depths.get("completed", 0) or 0),
            "failed": int(depths.get("failed", 0) or 0),
        },
        "by_source": {
            "github": {
                "received": github_received,
                "auth_failed": github_auth_failed,
                "published": github_published,
            },
            "linear": {
                "received": linear_received,
                "auth_failed": linear_auth_failed,
                "published": linear_published,
            },
        },
    }


def dispatcher_status_payload(
    dispatcher_state: dict[str, Any] | None,
    run_records: list[Any],
    *,
    server_started_at: float | None = None,
) -> dict[str, Any]:
    dispatcher_state = dispatcher_state or {}
    queue = queue_payload(run_records, limit=500)
    active_items = [item for item in queue["items"] if item["dispatch_status"] == "processing"]
    pending = int(queue["queue_depths"].get("pending", 0) or 0)
    failed = int(queue["queue_depths"].get("failed", 0) or 0)
    last_command = dispatcher_state.get("last_command") or {}
    action = str(last_command.get("action") or "").lower()
    updated_at = dispatcher_state.get("updated_at") or last_command.get("created_at")
    age = _age_seconds(updated_at)

    if action == "stop":
        status = "paused"
        running = False
        reason = "last operator command requested stop"
    elif action in {"start", "restart"}:
        status = "active" if (active_items or pending) else "idle"
        running = True
        reason = "last operator command requested dispatcher start/restart"
    elif active_items:
        status = "active"
        running = True
        reason = "run records show active processing work"
    elif pending:
        status = "queue-starved"
        running = False
        reason = "pending queue exists but no active processing run is visible"
    elif failed:
        status = "blocked"
        running = False
        reason = "failed queue items require recovery attention"
    else:
        status = "idle"
        running = False
        reason = "no pending or active queue work"

    silent_stall = {
        "triggered": bool((pending or active_items) and age is not None and age > 900),
        "age_seconds": age,
        "message": "Queued or active work has no recent dispatcher command/update." if (pending or active_items) else "No queued or active work.",
    }
    return {
        "source": "dashboard_dispatcher_state+run_records",
        "generated_at": utc_now(),
        "status": status,
        "running": running,
        "paused": status == "paused",
        "last_command": last_command or None,
        "updated_at": updated_at,
        "status_reason": reason,
        "cycle_number": int(dispatcher_state.get("cycle_number", 0) or 0),
        "last_cycle_at": updated_at,
        "active_agents": sorted({item["agent_name"] for item in active_items if item.get("agent_name")}),
        "queue_depths": queue["queue_depths"],
        "silent_stall": silent_stall,
        "server_started_at": server_started_at,
    }


def recovery_status_payload(recovery_state: dict[str, Any], run_records: list[Any], counters: dict[str, int] | None = None) -> dict[str, Any]:
    failed_items = [normalize_queue_item(run) for run in run_records if normalize_queue_item(run)["dispatch_status"] == "failed"]
    actions = list(recovery_state.get("actions", [])) if isinstance(recovery_state, dict) else []
    counters = counters or {}
    first_failing_layer = None
    if int(counters.get("github_auth_failed", 0) or 0) + int(counters.get("linear_auth_failed", 0) or 0) > 0:
        first_failing_layer = "ingest_auth"
    elif failed_items:
        first_failing_layer = "execution"
    elif any((item.get("failure_category") or "none") not in {"none", ""} for item in failed_items):
        first_failing_layer = "artifact"
    return {
        "source": "dashboard_recovery_controls+run_records",
        "generated_at": utc_now(),
        "failure_taxonomy": _FAILURE_TAXONOMY,
        "recent_failures": failed_items[:25],
        "controls": {
            "actions": actions[:25],
            "last_status": recovery_state.get("last_status") if isinstance(recovery_state, dict) else None,
            "updated_at": recovery_state.get("updated_at") if isinstance(recovery_state, dict) else None,
        },
        "first_failing_layer": first_failing_layer,
        "last_recovery_action": actions[0] if actions else None,
        "summary": {
            "failed_count": len(failed_items),
            "recovery_action_count": len(actions),
            "auth_failed": int(counters.get("github_auth_failed", 0) or 0) + int(counters.get("linear_auth_failed", 0) or 0),
        },
    }
