"""Queue / webhook / retry detail read models for the Prismatic dashboard.

This module converts existing run-record, webhook counter, dispatcher, recovery,
timeline, and dashboard control ledger evidence into stable dashboard contracts.
It never shells out or mutates real queue state.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

SOURCE = "run_records+webhook_counters+dispatcher_state+recovery_state+queue_control+timeline"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        out = datetime.fromisoformat(text)
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out
    except Exception:
        return None


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)  # type: ignore[arg-type]
    out: dict[str, Any] = {}
    for name in (
        "run_id", "issue_id", "agent_name", "status", "started_at", "completed_at",
        "error_message", "output_path", "branch", "workspace", "evidence", "verification_status",
    ):
        if hasattr(record, name):
            out[name] = getattr(record, name)
    return out


def _latest_time(record: dict[str, Any]) -> datetime | None:
    return _dt(record.get("completed_at")) or _dt(record.get("started_at"))


def _issue_url(issue_id: str | None) -> str | None:
    if not issue_id:
        return None
    return f"https://prismatic.growthwebdev.com/tab/tasks?issue={issue_id}"


def _failure_category(record: dict[str, Any], status: str) -> str:
    error = str(record.get("error_message") or "").lower()
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    blob = " ".join(str(x or "").lower() for x in [error, evidence.get("reason"), evidence.get("status"), evidence.get("category")])
    if "auth" in blob or "signature" in blob or "401" in blob:
        return "ingest_auth"
    if "parse" in blob or "malformed" in blob:
        return "ingest_parse"
    if "route" in blob or "label" in blob or "workspace" in blob:
        return "routing"
    if "missing" in blob or "proof" in blob or "artifact" in blob:
        return "artifact"
    if "stale" in blob or "stall" in blob or "sync" in blob:
        return "state_sync"
    if status in {"failed", "error", "blocked"} or error:
        return "execution"
    return "none"


def normalize_queue_item(record: Any, *, control_actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = _record_to_dict(record)
    run_id = str(data.get("run_id") or data.get("id") or "")
    issue_id = str(data.get("issue_id") or "")
    raw_status = str(data.get("status") or "pending").lower()
    if raw_status in {"running", "processing", "active"}:
        dispatch_status = "processing"
    elif raw_status in {"completed", "success", "succeeded"}:
        dispatch_status = "completed"
    elif raw_status in {"failed", "error", "blocked"} or data.get("error_message"):
        dispatch_status = "failed"
    elif raw_status in {"skipped", "dead_letter", "dead-letter", "deadletter"}:
        dispatch_status = "skipped"
    else:
        dispatch_status = "pending"

    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    text_blob = " ".join(str(x or "").lower() for x in [raw_status, data.get("error_message"), evidence.get("status"), evidence.get("reason")])
    skipped = dispatch_status == "skipped" or "skipped" in text_blob
    dead_lettered = "dead_letter" in text_blob or "dead-letter" in text_blob or "dlq" in text_blob
    attempts = int(evidence.get("attempts") or evidence.get("retry_count") or 0) if isinstance(evidence, dict) else 0
    if control_actions:
        attempts += len([a for a in control_actions if str(a.get("task_id") or "") in {run_id, issue_id} and a.get("action") == "retry"])
    processing = dispatch_status == "processing"
    retryable = dispatch_status == "failed" and not dead_lettered and not skipped
    category = _failure_category(data, dispatch_status)
    started_at = data.get("started_at")
    completed_at = data.get("completed_at")
    last_activity_at = completed_at or started_at
    return {
        "id": run_id or issue_id,
        "run_id": run_id,
        "issue_id": issue_id,
        "issue_url": _issue_url(issue_id),
        "identifier": issue_id or run_id,
        "agent": data.get("agent_name") or "unknown",
        "agent_name": data.get("agent_name") or "unknown",
        "status": dispatch_status,
        "dispatch_status": dispatch_status,
        "retryable": retryable,
        "dead_lettered": bool(dead_lettered),
        "skipped": bool(skipped),
        "processing": processing,
        "attempts": attempts,
        "last_error": data.get("error_message"),
        "error_message": data.get("error_message"),
        "failure_category": category,
        "started_at": started_at,
        "queued_at": started_at,
        "completed_at": completed_at,
        "last_activity_at": last_activity_at,
        "action": "dispatch",
        "source": "run_records",
        "payload": {
            "issue_id": issue_id,
            "agent_name": data.get("agent_name") or "unknown",
            "output_path": data.get("output_path"),
            "evidence": evidence,
        },
        "evidence": {
            "raw_status": raw_status,
            "verification_status": data.get("verification_status"),
            "has_error_message": bool(data.get("error_message")),
            "classification": {
                "retryable": "failed and not dead-lettered/skipped",
                "dead_lettered": "error/evidence mentions dead_letter/dead-letter/dlq",
                "skipped": "status or evidence mentions skipped",
                "processing": "running/processing status",
            },
        },
    }


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: _dt(item.get("last_activity_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def build_queue_detail(
    *,
    run_records: list[Any] | None = None,
    webhook_counters: dict[str, int] | None = None,
    dispatcher_context: dict[str, Any] | None = None,
    recovery_context: dict[str, Any] | None = None,
    timeline_payload: dict[str, Any] | None = None,
    queue_state: dict[str, Any] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    actions = list((queue_state or {}).get("actions") or []) if isinstance(queue_state, dict) else []
    records = [_record_to_dict(r) for r in (run_records or [])]
    records.sort(key=lambda r: _latest_time(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    items = _sort_items([normalize_queue_item(r, control_actions=actions) for r in records])[: max(1, min(int(limit or 500), 1000))]
    depths = {key: 0 for key in ["pending", "processing", "completed", "failed", "skipped", "dead_letter"]}
    for item in items:
        depths[item["dispatch_status"]] = depths.get(item["dispatch_status"], 0) + 1
        if item["dead_lettered"]:
            depths["dead_letter"] += 1
        if item["skipped"]:
            depths["skipped"] += 1
    now = datetime.now(timezone.utc)
    def recent(item: dict[str, Any]) -> bool:
        ts = _dt(item.get("last_activity_at"))
        return bool(ts and (now - ts).total_seconds() <= 86400)
    retry_candidates = [item for item in items if item["retryable"]]
    dead_letter = [item for item in items if item["dead_lettered"]]
    skipped = [item for item in items if item["skipped"]]
    processing = [item for item in items if item["processing"]]
    completed_recently = [item for item in items if item["dispatch_status"] == "completed" and recent(item)]
    failed_recently = [item for item in items if item["dispatch_status"] == "failed" and recent(item)]
    timeline_items = (timeline_payload or {}).get("items") if isinstance(timeline_payload, dict) else []
    if not isinstance(timeline_items, list):
        timeline_items = []
    counters = webhook_counters or {}
    return {
        "source": SOURCE,
        "generated_at": _now(),
        "empty": len(items) == 0,
        "queue_depths": depths,
        "items": items,
        "retry_candidates": retry_candidates[:50],
        "dead_letter": dead_letter[:50],
        "skipped": skipped[:50],
        "processing": processing[:50],
        "completed_recently": completed_recently[:50],
        "failed_recently": failed_recently[:50],
        "webhook_counters": counters,
        "dispatcher_context": dispatcher_context or {},
        "recovery_context": recovery_context or {},
        "recent_timeline": timeline_items[:25],
        "controls": {"actions": actions[:25], "last_status": (queue_state or {}).get("last_status") if isinstance(queue_state, dict) else None},
        "evidence": {
            "source": SOURCE,
            "run_record_count": len(records),
            "queue_item_count": len(items),
            "timeline_item_count": len(timeline_items),
            "control_action_count": len(actions),
            "classification_rules": {
                "retryable": "failed and not dead-lettered/skipped",
                "dead_letter": "error/evidence mentions dead_letter/dead-letter/dlq",
                "skipped": "status or evidence mentions skipped",
                "processing": "running/processing status",
                "completed_recently": "completed within 24h",
                "failed_recently": "failed within 24h",
            },
        },
    }


def build_queue_item_detail(task_id: str, **kwargs: Any) -> dict[str, Any]:
    detail = build_queue_detail(**kwargs)
    target = str(task_id or "")
    item = next((i for i in detail["items"] if target in {str(i.get("id")), str(i.get("run_id")), str(i.get("issue_id"))}), None)
    records = [_record_to_dict(r) for r in (kwargs.get("run_records") or [])]
    run_record = next((r for r in records if target in {str(r.get("run_id")), str(r.get("issue_id"))}), None)
    actions = list((kwargs.get("queue_state") or {}).get("actions") or []) if isinstance(kwargs.get("queue_state"), dict) else []
    retry_history = [a for a in actions if str(a.get("task_id") or "") in {target, str((item or {}).get("run_id")), str((item or {}).get("issue_id"))}]
    timeline_items = detail.get("recent_timeline") or []
    matched_timeline = [
        t for t in timeline_items
        if target in str(t.get("entity_id") or t.get("message") or t.get("title") or "")
        or (item and str(item.get("issue_id") or "") in str(t.get("entity_id") or t.get("message") or t.get("title") or ""))
    ]
    return {
        "item": item,
        "run_record": run_record,
        "recent_timeline": matched_timeline[:25],
        "retry_history": retry_history[:25],
        "recovery_context": detail.get("recovery_context") or {},
        "evidence": {
            "source": SOURCE,
            "task_id": target,
            "found": item is not None,
            "retry_history_count": len(retry_history),
            "timeline_matches": len(matched_timeline),
        },
    }
