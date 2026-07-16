"""Operational Timeline helpers for Prismatic governance surfaces."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILE_NAME = "timeline_manual_events.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))


def manual_events_path() -> Path:
    return _state_dir() / STATE_FILE_NAME


def _bus_db_path() -> Path:
    db_path = os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite"
    path = Path(db_path)
    if path.is_absolute():
        return path
    return Path(os.environ.get("PRISMATIC_HOME") or Path.home()) / path


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _infer_severity(*parts: Any, default: str = "info") -> str:
    text = " ".join(str(p or "") for p in parts).lower()
    if any(token in text for token in ("failed", "failure", "error", "auth_failed", "exception")):
        return "error"
    if any(token in text for token in ("warning", "warn", "stale", "retry", "cancelled", "blocked", "purge", "stop")):
        return "warning"
    if any(token in text for token in ("completed", "success", "merged", "installed", "optimized", "registered", "start", "restart")):
        return "success"
    return default


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "-".join(str(p) for p in parts if p is not None and str(p) != "")
    return f"{prefix}-{raw}" if raw else f"{prefix}-{uuid.uuid4().hex[:12]}"


def _timestamp_sort_key(item: dict[str, Any]) -> str:
    return str(item.get("timestamp") or "")


def normalize_eventbus_item(event: dict[str, Any]) -> dict[str, Any]:
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    topic = str(event.get("topic") or payload.get("event_type") or payload.get("type") or "event")
    source = str(payload.get("source") or payload.get("provider") or "EventBus")
    message = str(payload.get("message") or payload.get("status") or payload.get("detail") or topic)
    ts = event.get("ts") or payload.get("timestamp") or payload.get("created_at")
    return {
        "id": _stable_id("event", event.get("rowid"), topic),
        "timestamp": ts,
        "kind": "event",
        "source": source,
        "severity": _infer_severity(topic, message),
        "title": topic.replace(".", " ").replace("_", " ").title(),
        "message": message,
        "status": "processed" if event.get("processed") else "pending",
        "entity_id": str(event.get("rowid") or ""),
        "metadata": {"topic": topic, "payload": payload, "processed": bool(event.get("processed"))},
    }


def normalize_run_record(run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "unknown")
    if status in {"completed", "success", "succeeded"}:
        severity = "success"
    elif status in {"failed", "error"}:
        severity = "error"
    elif status in {"cancelled", "canceled"}:
        severity = "warning"
    else:
        severity = "info"
    run_id = str(run.get("run_id") or "")
    issue = str(run.get("issue_id") or "")
    agent = str(run.get("agent_name") or "agent")
    return {
        "id": _stable_id("run", run_id),
        "timestamp": run.get("completed_at") or run.get("started_at"),
        "kind": "run",
        "source": "RunStore",
        "severity": severity,
        "title": f"{agent} run {status}",
        "message": f"{issue or run_id or 'Run'} is {status}",
        "status": status,
        "entity_id": run_id,
        "metadata": {
            "run_id": run_id,
            "issue_id": issue,
            "agent_name": agent,
            "verification_status": run.get("verification_status"),
            "verification_scope": run.get("verification_scope"),
            "cleanup_status": run.get("cleanup_status"),
            "done_gate_result": run.get("done_gate_result"),
            "failure_category": run.get("failure_category"),
            "output_path": run.get("output_path"),
            "error_message": run.get("error_message"),
        },
    }


def normalize_recovery_action(action: dict[str, Any]) -> dict[str, Any]:
    status = str(action.get("status") or action.get("label") or "recovery action")
    action_name = str(action.get("action") or "recovery")
    return {
        "id": str(action.get("id") or _stable_id("recovery", action_name, action.get("created_at"))),
        "timestamp": action.get("created_at"),
        "kind": "recovery",
        "source": "RecoveryControl",
        "severity": _infer_severity(status, action.get("detail"), default="warning"),
        "title": str(action.get("label") or action_name.title()),
        "message": str(action.get("detail") or status),
        "status": status,
        "entity_id": str(action.get("ref") or action.get("agent") or ""),
        "metadata": dict(action),
    }


def normalize_webhook_counter(name: str, value: int) -> dict[str, Any]:
    title = name.replace("_", " ").title()
    return {
        "id": _stable_id("webhook", name),
        "timestamp": utc_now(),
        "kind": "webhook",
        "source": "Webhook",
        "severity": "error" if "auth_failed" in name and value > 0 else "info",
        "title": title,
        "message": f"{title}: {value}",
        "status": "counter",
        "entity_id": name,
        "metadata": {"counter": name, "value": value},
    }


def _normalize_manual_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(event.get("id") or _stable_id("manual", event.get("title"), event.get("timestamp"))),
        "timestamp": event.get("timestamp") or event.get("created_at") or utc_now(),
        "kind": str(event.get("kind") or "manual"),
        "source": str(event.get("source") or "Manual"),
        "severity": str(event.get("severity") or "info"),
        "title": str(event.get("title") or "Manual timeline event"),
        "message": str(event.get("message") or ""),
        "status": event.get("status"),
        "entity_id": str(event.get("entity_id") or ""),
        "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
    }


def load_manual_events() -> list[dict[str, Any]]:
    data = _read_json_file(manual_events_path(), {"events": []})
    events = data.get("events") if isinstance(data, dict) else data
    if not isinstance(events, list):
        return []
    return [_normalize_manual_event(event) for event in events if isinstance(event, dict)]


def record_timeline_item(
    *,
    kind: str = "manual",
    source: str = "Manual",
    severity: str = "info",
    title: str,
    message: str = "",
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("title is required")
    item = _normalize_manual_event(
        {
            "id": _stable_id(kind or "manual", int(time.time() * 1000), uuid.uuid4().hex[:8]),
            "timestamp": utc_now(),
            "kind": kind or "manual",
            "source": source or "Manual",
            "severity": severity or "info",
            "title": title.strip(),
            "message": message.strip(),
            "entity_id": entity_id or "",
            "metadata": metadata or {},
        }
    )
    events = [item] + load_manual_events()
    _write_json_file(manual_events_path(), {"schema": "prismatic.timeline_manual_events.v1", "updated_at": utc_now(), "events": events[:500]})
    return item


def _eventbus_items(limit: int) -> list[dict[str, Any]]:
    path = _bus_db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            rows = conn.execute(
                "SELECT rowid, topic, payload_json, ts, processed FROM events ORDER BY rowid DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    items = []
    for row in rows:
        try:
            payload = json.loads(row[2])
        except Exception:
            payload = {"_raw": str(row[2])[:300]}
        items.append(normalize_eventbus_item({"rowid": row[0], "topic": row[1], "payload": payload, "ts": row[3], "processed": bool(row[4])}))
    return items


def list_timeline(
    *,
    limit: int = 50,
    source: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    run_records: list[dict[str, Any]] | None = None,
    recovery_state: dict[str, Any] | None = None,
    webhook_counters: dict[str, int] | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 500))
    items: list[dict[str, Any]] = []
    items.extend(load_manual_events())
    items.extend(_eventbus_items(safe_limit))
    items.extend(normalize_run_record(run) for run in (run_records or []) if isinstance(run, dict))
    actions = (recovery_state or {}).get("actions", [])
    if isinstance(actions, list):
        items.extend(normalize_recovery_action(action) for action in actions if isinstance(action, dict))
    for name, value in (webhook_counters or {}).items():
        try:
            numeric = int(value)
        except Exception:
            continue
        if numeric:
            items.append(normalize_webhook_counter(str(name), numeric))

    if source:
        items = [item for item in items if str(item.get("source", "")).lower() == source.lower()]
    if kind:
        items = [item for item in items if str(item.get("kind", "")).lower() == kind.lower()]
    if severity:
        items = [item for item in items if str(item.get("severity", "")).lower() == severity.lower()]
    items.sort(key=_timestamp_sort_key, reverse=True)
    items = items[:safe_limit]
    return {"source": "prismatic.timeline", "generated_at": utc_now(), "limit": safe_limit, "count": len(items), "items": items}


def timeline_summary(**kwargs: Any) -> dict[str, Any]:
    payload = list_timeline(limit=max(int(kwargs.pop("limit", 200) or 200), 1), **kwargs)
    items = payload["items"]
    by_kind = Counter(str(item.get("kind") or "unknown") for item in items)
    by_severity = Counter(str(item.get("severity") or "info") for item in items)
    latest = items[0] if items else None
    return {
        "source": "prismatic.timeline",
        "generated_at": utc_now(),
        "total": len(items),
        "by_kind": dict(by_kind),
        "by_severity": dict(by_severity),
        "latest": {"timestamp": latest.get("timestamp"), "title": latest.get("title")} if latest else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-timeline", description="Prismatic Operational Timeline CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--kind")
    list_parser.add_argument("--source")
    list_parser.add_argument("--severity")
    summary_parser = sub.add_parser("summary")
    summary_parser.add_argument("--limit", type=int, default=200)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--kind", default="manual")
    record_parser.add_argument("--source", default="Manual")
    record_parser.add_argument("--severity", default="info")
    record_parser.add_argument("--title", required=True)
    record_parser.add_argument("--message", default="")
    record_parser.add_argument("--entity-id", default="")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps(list_timeline(limit=args.limit, kind=args.kind, source=args.source, severity=args.severity), indent=2))
    elif args.command == "summary":
        print(json.dumps(timeline_summary(limit=args.limit), indent=2))
    elif args.command == "record":
        print(json.dumps({"ok": True, "item": record_timeline_item(kind=args.kind, source=args.source, severity=args.severity, title=args.title, message=args.message, entity_id=args.entity_id)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
