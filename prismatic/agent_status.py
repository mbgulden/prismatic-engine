"""Agent and worker status read models for the governance dashboard.

The dashboard must distinguish active work from idle capacity, queue starvation,
human-feedback waits, completed work, and genuine failures. This module only
normalizes existing local evidence: run records, optional agent registry, queue
payloads, timeline items, and recovery/health context. It never shells out.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

SOURCE = "run_records+agent_registry+queue_state+timeline+health_context"
DEFAULT_AGENTS: dict[str, dict[str, str]] = {
    "agy": {"name": "AGY", "role": "Vision & Research CLI Specialist"},
    "jules": {"name": "Jules", "role": "Async Git, Conflict Resolver & PR Agent"},
    "fred": {"name": "Fred", "role": "Staging Governor & Compliance Watcher"},
    "ned": {"name": "Ned", "role": "Long-running Research & Context Compiler"},
    "kai": {"name": "Kai", "role": "Tourism Orchestrator & Config Coordinator"},
    "codex": {"name": "Codex", "role": "PR Review Specialist & Security Linter"},
}
KNOWN_STATUSES = [
    "active",
    "idle",
    "queue_starved",
    "awaiting_user_feedback",
    "completed_recently",
    "errored",
    "churning",
    "launch_failing",
    "unknown",
]


def agent_key(name: str | None) -> str:
    key = (name or "unknown").strip().lower().replace("agent:", "")
    aliases = {
        "agy-cli": "agy",
        "kai-content": "kai",
        "kai-css": "kai",
        "kai-js": "kai",
        "jules-cli": "jules",
        "openai-codex": "codex",
    }
    return aliases.get(key, key)


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)  # type: ignore[arg-type]
    out: dict[str, Any] = {}
    for name in (
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
        "failure_category",
        "cleanup_status",
        "done_gate_result",
        "done_gate_errors",
    ):
        if hasattr(record, name):
            out[name] = getattr(record, name)
    return out


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _issue_url(issue_id: str | None) -> str | None:
    if not issue_id or issue_id == "unknown":
        return None
    return f"https://prismatic.growthwebdev.com/tab/tasks?issue={issue_id}"


def _latest_time(record: dict[str, Any]) -> datetime | None:
    return _parse_dt(record.get("completed_at")) or _parse_dt(record.get("started_at"))


def _status_from_record(record: dict[str, Any], now: datetime) -> tuple[str, str, dict[str, Any]]:
    raw_status = str(record.get("status") or "unknown").lower()
    error = str(record.get("error_message") or "")
    evidence_raw = record.get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, dict) else {}
    lower_blob = " ".join(
        str(x or "").lower()
        for x in [raw_status, error, evidence.get("status"), evidence.get("reason"), evidence.get("phase"), record.get("done_gate_result")]
    )
    started = _parse_dt(record.get("started_at"))
    completed = _parse_dt(record.get("completed_at"))
    event_dt = completed or started
    age_s = (now - event_dt).total_seconds() if event_dt is not None else None
    meta = {"raw_status": raw_status, "age_seconds": age_s}

    if "await" in lower_blob and ("feedback" in lower_blob or "user" in lower_blob or "approval" in lower_blob):
        return "awaiting_user_feedback", "awaiting_user_feedback", meta
    if "launch" in lower_blob and ("fail" in lower_blob or "error" in lower_blob):
        return "launch_failing", "launch_failing", meta
    if "churn" in lower_blob or "restart loop" in lower_blob or "crashloop" in lower_blob:
        return "churning", "churning", meta
    if raw_status in {"running", "processing", "active", "in_progress"}:
        return "active", "active", meta
    if raw_status in {"pending", "queued"}:
        return "queue_starved", "queue_starved", meta
    if raw_status in {"failed", "error", "errored", "blocked"} or error:
        return "errored", "errored", meta
    if raw_status in {"completed", "success", "succeeded", "done"}:
        if age_s is not None and age_s <= 24 * 60 * 60:
            return "completed_recently", "completed_recently", meta
        return "idle", "idle", meta
    return "unknown", "unknown", meta


def _queue_counts(queue_payload: dict[str, Any] | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    if isinstance(queue_payload, dict):
        by_status = queue_payload.get("by_status") or queue_payload.get("counts") or queue_payload.get("queue_depths")
        if isinstance(by_status, dict):
            for key, value in by_status.items():
                try:
                    counts[str(key)] = int(value)
                except Exception:
                    pass
        for item in queue_payload.get("items") or []:
            if isinstance(item, dict):
                status = str(item.get("status") or "unknown").lower()
                counts[status] = counts.get(status, 0) + 1
    return counts


def _registry_item(registry: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(registry, dict):
        return {}
    for raw_name, info in registry.items():
        if agent_key(raw_name) == key and isinstance(info, dict):
            return dict(info)
    return {}


def build_agent_status(
    *,
    run_records: list[Any] | None = None,
    registry: dict[str, Any] | None = None,
    queue_payload: dict[str, Any] | None = None,
    timeline_payload: dict[str, Any] | None = None,
    health_context: dict[str, Any] | None = None,
    include_defaults: bool = True,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    registry = registry if isinstance(registry, dict) else {}
    records = [_record_to_dict(r) for r in (run_records or [])]
    records.sort(key=lambda r: _latest_time(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    queue_counts = _queue_counts(queue_payload)
    timeline_items = (timeline_payload or {}).get("items") if isinstance(timeline_payload, dict) else []
    if not isinstance(timeline_items, list):
        timeline_items = []

    agent_keys = set(agent_key(r.get("agent_name")) for r in records)
    agent_keys.update(agent_key(name) for name in registry.keys())
    if include_defaults:
        agent_keys.update(DEFAULT_AGENTS.keys())
    agent_keys.discard("")

    by_agent_records: dict[str, list[dict[str, Any]]] = {key: [] for key in agent_keys}
    for rec in records:
        by_agent_records.setdefault(agent_key(rec.get("agent_name")), []).append(rec)

    agents: list[dict[str, Any]] = []
    for key in sorted(agent_keys):
        default = DEFAULT_AGENTS.get(key, {"name": key.title(), "role": "Agent"})
        reg = _registry_item(registry, key)
        recs = by_agent_records.get(key, [])
        latest = recs[0] if recs else {}
        status = "idle" if (include_defaults or reg) else "unknown"
        phase = status
        class_meta: dict[str, Any] = {"raw_status": None, "age_seconds": None}
        if latest:
            status, phase, class_meta = _status_from_record(latest, now)
        reg_status = str(reg.get("status") or reg.get("state") or "").lower()
        if reg_status in {"running", "active", "working", "processing"} and status in {"idle", "unknown", "completed_recently"}:
            status = phase = "active"
        elif reg_status in {"offline", "failed", "error", "errored"} and not latest:
            status = phase = "errored"
        elif reg_status in {"idle", "ready"} and not latest:
            status = phase = "idle"

        current_issue = reg.get("issue") or reg.get("task_id") or latest.get("issue_id")
        last_success = next((_latest_time(r) for r in recs if str(r.get("status") or "").lower() in {"completed", "success", "succeeded", "done"}), None)
        last_error_rec = next((r for r in recs if str(r.get("status") or "").lower() in {"failed", "error", "errored", "blocked"} or r.get("error_message")), None)
        last_error_time = _latest_time(last_error_rec) if last_error_rec else None
        queue_depth = 0
        for r in recs:
            if str(r.get("status") or "").lower() in {"pending", "queued"}:
                queue_depth += 1
        if key in {"dispatcher", "queue"}:
            queue_depth += queue_counts.get("pending", 0)
        idle_reason = None
        queue_starved_reason = None
        if status == "idle":
            idle_reason = "no active or pending run evidence"
        if status == "queue_starved":
            queue_starved_reason = "pending/queued run exists without running completion evidence"

        agent = {
            "id": key,
            "name": str(reg.get("name") or default["name"]),
            "role": str(reg.get("role") or default["role"]),
            "kind": str(reg.get("kind") or "agent"),
            "status": status,
            "phase": phase,
            "current_issue": current_issue,
            "current_issue_url": _issue_url(current_issue),
            "current_branch": reg.get("branch") or reg.get("current_branch"),
            "current_workspace": reg.get("workspace") or reg.get("current_workspace"),
            "pid": reg.get("pid"),
            "service_name": reg.get("service_name"),
            "queue_depth": queue_depth,
            "last_run_id": latest.get("run_id"),
            "last_activity_at": _iso(_latest_time(latest)) or reg.get("last_heartbeat") or reg.get("last_seen"),
            "last_success_at": _iso(last_success),
            "last_error_at": _iso(last_error_time),
            "last_error": (last_error_rec or {}).get("error_message"),
            "awaiting_user_feedback": status == "awaiting_user_feedback",
            "completed_recently": status == "completed_recently",
            "churning_or_launch_failing": status in {"churning", "launch_failing"},
            "idle_reason": idle_reason,
            "queue_starved_reason": queue_starved_reason,
            "source": SOURCE,
            "evidence": {
                "run_count": len(recs),
                "latest_raw_status": class_meta.get("raw_status"),
                "latest_age_seconds": class_meta.get("age_seconds"),
                "registry_present": bool(reg),
                "queue_counts": queue_counts,
            },
        }
        agents.append(agent)

    status_counts = {status: 0 for status in KNOWN_STATUSES}
    for agent in agents:
        status_counts[agent["status"]] = status_counts.get(agent["status"], 0) + 1

    def subset(statuses: set[str]) -> list[dict[str, Any]]:
        return [a for a in agents if a["status"] in statuses]

    recent_activity = []
    for rec in records[:20]:
        recent_activity.append(
            {
                "run_id": rec.get("run_id"),
                "issue_id": rec.get("issue_id"),
                "issue_url": _issue_url(rec.get("issue_id")),
                "agent_id": agent_key(rec.get("agent_name")),
                "status": rec.get("status"),
                "started_at": rec.get("started_at"),
                "completed_at": rec.get("completed_at"),
                "last_activity_at": _iso(_latest_time(rec)),
                "error_message": rec.get("error_message"),
            }
        )

    return {
        "source": SOURCE,
        "generated_at": now.isoformat(),
        "empty": len(agents) == 0,
        "status_counts": status_counts,
        "agents": agents,
        "workers": [a for a in agents if a.get("kind") in {"worker", "agent"}],
        "queues": {
            "counts": queue_counts,
            "total": sum(queue_counts.values()) if queue_counts else (queue_payload or {}).get("total", 0) if isinstance(queue_payload, dict) else 0,
            "source": "webhook_queue" if queue_payload else "none",
        },
        "recent_activity": recent_activity,
        "awaiting_user_feedback": subset({"awaiting_user_feedback"}),
        "completed_recently": subset({"completed_recently"}),
        "churning_or_launch_failing": subset({"churning", "launch_failing"}),
        "idle": subset({"idle"}),
        "queue_starved": subset({"queue_starved"}),
        "evidence": {
            "run_record_count": len(records),
            "registry_count": len(registry),
            "queue_item_count": len((queue_payload or {}).get("items") or []) if isinstance(queue_payload, dict) else 0,
            "timeline_item_count": len(timeline_items),
            "health_sources": sorted((health_context or {}).keys()) if isinstance(health_context, dict) else [],
            "classification_rules": {
                "active": "latest run/status evidence is running/processing/active",
                "queue_starved": "pending/queued run exists without active execution evidence",
                "awaiting_user_feedback": "run/error/evidence text mentions awaiting user feedback/approval",
                "completed_recently": "latest completed run is within 24h",
                "errored": "latest run failed/errored/blocked or has error_message",
                "churning": "run/evidence text mentions churn/restart loop/crashloop",
                "launch_failing": "run/evidence text mentions launch failure",
                "idle": "known agent exists but no active/pending/error/recent-completion evidence",
            },
        },
    }


def build_agent_detail(
    agent_id: str,
    *,
    run_records: list[Any] | None = None,
    registry: dict[str, Any] | None = None,
    queue_payload: dict[str, Any] | None = None,
    timeline_payload: dict[str, Any] | None = None,
    health_context: dict[str, Any] | None = None,
    include_defaults: bool = True,
) -> dict[str, Any]:
    key = agent_key(agent_id)
    status_payload = build_agent_status(
        run_records=run_records,
        registry=registry,
        queue_payload=queue_payload,
        timeline_payload=timeline_payload,
        health_context=health_context,
        include_defaults=include_defaults,
    )
    agent = next((a for a in status_payload["agents"] if a["id"] == key), None)
    records = [_record_to_dict(r) for r in (run_records or [])]
    records = [r for r in records if agent_key(r.get("agent_name")) == key]
    records.sort(key=lambda r: _latest_time(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    timeline_items = (timeline_payload or {}).get("items") if isinstance(timeline_payload, dict) else []
    if not isinstance(timeline_items, list):
        timeline_items = []
    recent_timeline = [
        item for item in timeline_items
        if key in str(item.get("entity_id") or item.get("message") or item.get("title") or "").lower()
    ][:10]
    queue_items = []
    if isinstance(queue_payload, dict):
        queue_items = [
            item for item in (queue_payload.get("items") or [])
            if key in str(item.get("agent") or item.get("agent_name") or item.get("assignee") or "").lower()
        ][:20]
    return {
        "agent": agent or {
            "id": key,
            "name": key.title(),
            "role": "Agent",
            "kind": "agent",
            "status": "unknown",
            "phase": "unknown",
            "source": SOURCE,
            "evidence": {"missing": True},
        },
        "recent_runs": [
            {
                **r,
                "agent_id": agent_key(r.get("agent_name")),
                "issue_url": _issue_url(r.get("issue_id")),
                "last_activity_at": _iso(_latest_time(r)),
            }
            for r in records[:10]
        ],
        "recent_timeline": recent_timeline,
        "queue_context": {
            "items": queue_items,
            "counts": _queue_counts(queue_payload),
            "source": "webhook_queue" if queue_payload else "none",
        },
        "health_context": health_context or {},
        "evidence": {
            "source": SOURCE,
            "run_count": len(records),
            "timeline_matches": len(recent_timeline),
            "queue_matches": len(queue_items),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
