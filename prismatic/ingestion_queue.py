"""Durable webhook ingestion queue adapter for the governance dashboard.

The governance dashboard's Ingestion Queue tab is an operator console, not an
EventBus activity feed.  This module owns the durable `linear_webhook_queue.db`
contract used by the older working Linear webhook implementation and exposes a
small schema-tolerant API for the gateway.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

QUEUE_TABLE = "linear_webhook_queue"
COUNTERS_TABLE = "webhook_counters"
REPLAYABLE_STATUSES = {"failed", "stale", "skipped_no_agent_label", "no_op"}
PURGE_STATUSES = {"completed", "failed", "stale", "skipped_no_agent_label", "no_op", "dispatched"}


def state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def queue_db_path() -> Path:
    return state_dir() / "linear_webhook_queue.db"


def _connect() -> sqlite3.Connection:
    path = queue_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _counter_name_column(conn: sqlite3.Connection) -> str:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({COUNTERS_TABLE})")}
    if "name" in cols:
        return "name"
    if "key" in cols:
        return "key"
    return "name"


def ensure_queue_db() -> Path:
    """Create/migrate the durable queue DB without dropping legacy data."""
    path = queue_db_path()
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                identifier TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                received_at REAL,
                raw_json TEXT,
                dispatch_status TEXT DEFAULT 'pending',
                agent_name TEXT,
                processed_at TIMESTAMP,
                queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({QUEUE_TABLE})")}
        migrations = {
            # SQLite cannot add a UNIQUE column via ALTER TABLE; legacy DBs get
            # a plain column plus idempotent INSERT OR IGNORE where the original
            # schema already has the unique constraint.
            "event_id": "TEXT",
            "identifier": "TEXT NOT NULL DEFAULT ''",
            "event_type": "TEXT NOT NULL DEFAULT ''",
            "action": "TEXT NOT NULL DEFAULT ''",
            "received_at": "REAL",
            "raw_json": "TEXT",
            "dispatch_status": "TEXT DEFAULT 'pending'",
            "agent_name": "TEXT",
            "processed_at": "TIMESTAMP",
            "queued_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {QUEUE_TABLE} ADD COLUMN {column} {ddl}")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {COUNTERS_TABLE} (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        counter_cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({COUNTERS_TABLE})")}
        if "value" not in counter_cols:
            conn.execute(f"ALTER TABLE {COUNTERS_TABLE} ADD COLUMN value INTEGER NOT NULL DEFAULT 0")
        if "updated_at" not in counter_cols:
            conn.execute(f"ALTER TABLE {COUNTERS_TABLE} ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_status ON {QUEUE_TABLE}(dispatch_status)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_received_at ON {QUEUE_TABLE}(received_at)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_identifier ON {QUEUE_TABLE}(identifier)")
        conn.commit()
    return path


def increment_counter(name: str, amount: int = 1) -> None:
    ensure_queue_db()
    now = time.time()
    with _connect() as conn:
        name_col = _counter_name_column(conn)
        conn.execute(
            f"""
            INSERT INTO {COUNTERS_TABLE} ({name_col}, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT({name_col}) DO UPDATE SET
                value = value + excluded.value,
                updated_at = excluded.updated_at
            """,
            (name, int(amount), now),
        )
        conn.commit()


def _extract_labels(data: dict[str, Any]) -> list[str]:
    labels = data.get("labels") or {}
    nodes: Any
    if isinstance(labels, dict):
        nodes = labels.get("nodes", [])
    elif isinstance(labels, list):
        nodes = labels
    else:
        nodes = []
    out: list[str] = []
    for node in nodes:
        if isinstance(node, dict):
            name = str(node.get("name") or "")
        else:
            name = str(node or "")
        if name:
            out.append(name)
    return out


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _extract_agent_name(payload: dict[str, Any]) -> str:
    data = _payload_data(payload)
    for label in _extract_labels(data):
        if label.startswith("agent:"):
            return label.split(":", 1)[1].strip() or "unassigned"
    return str(payload.get("agent") or payload.get("agent_name") or "unassigned")


def _extract_identifier(payload: dict[str, Any]) -> str:
    data = _payload_data(payload)
    return str(
        data.get("identifier")
        or payload.get("identifier")
        or payload.get("issue")
        or data.get("id")
        or payload.get("id")
        or ""
    )


def _extract_event_id(payload: dict[str, Any], raw_body: bytes | None = None) -> str:
    data = _payload_data(payload)
    value = payload.get("event_id") or payload.get("webhookId") or payload.get("id") or data.get("id")
    if value:
        return str(value)
    seed = raw_body if raw_body is not None else json.dumps(payload, sort_keys=True, default=str).encode()
    return f"linear-{uuid.uuid5(uuid.NAMESPACE_URL, seed.decode('utf-8', errors='replace'))}"


def enqueue_linear_event(payload: dict[str, Any], *, raw_body: bytes | None = None) -> dict[str, Any]:
    """Insert a Linear webhook into the durable queue idempotently."""
    ensure_queue_db()
    event_id = _extract_event_id(payload, raw_body)
    data = _payload_data(payload)
    identifier = _extract_identifier(payload)
    event_type = str(payload.get("type") or data.get("type") or "")
    action = str(payload.get("action") or "unknown")
    agent_name = _extract_agent_name(payload)
    received_at = time.time()
    raw_json = json.dumps(payload, sort_keys=True, default=str)
    with _connect() as conn:
        existing = conn.execute(f"SELECT * FROM {QUEUE_TABLE} WHERE event_id = ?", (event_id,)).fetchone()
        if existing is None:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {QUEUE_TABLE}
                    (event_id, identifier, event_type, action, received_at, raw_json, dispatch_status, agent_name)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (event_id, identifier, event_type, action, received_at, raw_json, agent_name),
            )
        inserted = existing is None and conn.total_changes > 0
        row = conn.execute(f"SELECT * FROM {QUEUE_TABLE} WHERE event_id = ? ORDER BY id DESC LIMIT 1", (event_id,)).fetchone()
        conn.commit()
    if inserted:
        increment_counter("queued", 1)
    return {"inserted": inserted, "item": normalize_row(row) if row else None, "event_id": event_id}


def normalize_status(status: Any) -> str:
    clean = str(status or "pending").strip().lower()
    if clean.startswith("failed"):
        return "failed"
    if clean in {"dispatched", "success", "succeeded"}:
        return "completed"
    if clean in {"running", "processing"}:
        return "processing"
    if clean in {"pending", "queued", "stale", "skipped_no_agent_label", "no_op", "completed", "failed"}:
        return clean
    return clean or "pending"


def normalize_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    raw_json = data.get("raw_json")
    status = normalize_status(data.get("dispatch_status"))
    return {
        "id": data.get("id"),
        "event_id": data.get("event_id") or str(data.get("id") or ""),
        "identifier": data.get("identifier") or "",
        "agent_name": data.get("agent_name") or "unassigned",
        "action": data.get("action") or "unknown",
        "event_type": data.get("event_type") or "",
        "dispatch_status": status,
        "status": status,
        "queued_at": data.get("queued_at") or data.get("received_at") or "",
        "received_at": data.get("received_at"),
        "processed_at": data.get("processed_at"),
        "raw_json": raw_json,
    }


def queue_payload(*, limit: int = 50, offset: int = 0, status: str | None = None) -> dict[str, Any]:
    ensure_queue_db()
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clean_status = status.strip().lower()
        if clean_status == "failed":
            clauses.append("dispatch_status LIKE 'failed%'")
        else:
            clauses.append("LOWER(dispatch_status) = ?")
            params.append(clean_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS total FROM {QUEUE_TABLE} {where}", params).fetchone()["total"]
        rows = conn.execute(
            f"""
            SELECT * FROM {QUEUE_TABLE}
            {where}
            ORDER BY COALESCE(received_at, 0) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return {
        "items": [normalize_row(row) for row in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "source": "linear_webhook_queue.db",
        "db_path": str(queue_db_path()),
    }


def queue_stats_payload(extra_counters: dict[str, int] | None = None) -> dict[str, Any]:
    ensure_queue_db()
    with _connect() as conn:
        rows = conn.execute(f"SELECT dispatch_status, received_at, processed_at FROM {QUEUE_TABLE}").fetchall()
        name_col = _counter_name_column(conn)
        counters = {row[name_col]: int(row["value"] or 0) for row in conn.execute(f"SELECT {name_col}, value FROM {COUNTERS_TABLE}").fetchall()}
    depths = Counter(normalize_status(row["dispatch_status"]) for row in rows)
    failed = sum(1 for row in rows if normalize_status(row["dispatch_status"]) == "failed")
    processed_statuses = {"completed", "failed", "dispatched", "no_op", "skipped_no_agent_label"}
    processed = sum(1 for row in rows if normalize_status(row["dispatch_status"]) in processed_statuses)
    latencies: list[float] = []
    for row in rows:
        try:
            if row["received_at"] and row["processed_at"]:
                latencies.append(max(0.0, float(row["processed_at"]) - float(row["received_at"])))
        except Exception:
            continue
    for key in ("pending", "processing", "completed", "failed", "stale"):
        depths.setdefault(key, 0)
    merged_counters = dict(extra_counters or {})
    merged_counters.update(counters)
    return {
        "source": "linear_webhook_queue.db",
        "received": int(merged_counters.get("linear_received", 0) or len(rows)),
        "auth_failed": int(merged_counters.get("linear_auth_failed", 0) or 0),
        "queued": int(merged_counters.get("queued", 0) or depths.get("pending", 0)),
        "processed": processed,
        "failed": failed,
        "average_dispatch_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        "recent_latencies": latencies[-25:],
        "queue_depths": dict(depths),
        "total": len(rows),
        "db_path": str(queue_db_path()),
    }


def retry_task(task_id: str | int) -> dict[str, Any]:
    ensure_queue_db()
    task = str(task_id).strip()
    if not task:
        return {"ok": False, "status": "error", "updated": 0, "message": "task_id is required"}
    with _connect() as conn:
        row = conn.execute(f"SELECT * FROM {QUEUE_TABLE} WHERE id = ? OR event_id = ?", (task, task)).fetchone()
        if row is None:
            return {"ok": False, "status": "not_found", "updated": 0, "task_id": task, "message": f"Queue task {task} not found"}
        conn.execute(
            f"UPDATE {QUEUE_TABLE} SET dispatch_status = 'pending', processed_at = NULL WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        updated = conn.total_changes
        new_row = conn.execute(f"SELECT * FROM {QUEUE_TABLE} WHERE id = ?", (row["id"],)).fetchone()
    increment_counter("retry_requested", 1)
    return {
        "ok": True,
        "status": "ok",
        "task_id": task,
        "updated": int(updated > 0),
        "message": f"Task {task} reset to pending",
        "item": normalize_row(new_row),
    }


def purge_queue() -> dict[str, Any]:
    ensure_queue_db()
    with _connect() as conn:
        cursor = conn.execute(
            f"""
            DELETE FROM {QUEUE_TABLE}
            WHERE LOWER(dispatch_status) IN ({','.join('?' for _ in PURGE_STATUSES)})
               OR LOWER(dispatch_status) LIKE 'failed:%'
            """,
            sorted(PURGE_STATUSES),
        )
        deleted = int(cursor.rowcount or 0)
        conn.commit()
    increment_counter("purge_requested", 1)
    return {
        "ok": True,
        "status": "ok",
        "deleted": deleted,
        "message": f"Purged {deleted} completed/failed queue rows",
    }
