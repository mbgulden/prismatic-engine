"""Idempotency coverage for dispatch_consumer_v3.

GRO-3484 acceptance:
- a stable dedup key exists for each bus event,
- a processed marker is persisted,
- replaying the same event does not duplicate supervisor dispatch work.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer


def _init_event_db(path):
    conn = sqlite3.connect(path)
    try:
        consumer.ensure_schema(conn)
        return conn
    finally:
        conn.close()


def test_replaying_same_dedup_key_does_not_spawn_twice(tmp_path, monkeypatch):
    db_path = tmp_path / "event_log.sqlite"
    _init_event_db(db_path)
    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))
    consumer._recent_dispatches.clear()

    payload = {
        "type": "Issue",
        "data": {"identifier": "GRO-IDEMPOTENT"},
    }
    payload_json = json.dumps(payload, sort_keys=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed) VALUES (?, ?, ?, ?, 0)",
            ("linear:update:GRO-IDEMPOTENT:1", "update", payload_json, time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        consumer,
        "fetch_issue",
        lambda issue_id: {
            "identifier": issue_id,
            "state": {"name": "Backlog"},
            "labels": {"nodes": [{"name": "dispatch:ready"}]},
        },
    )
    dispatches: list[str] = []
    monkeypatch.setattr(
        consumer, "dispatch_to_supervisor", lambda issue_id: dispatches.append(issue_id)
    )

    consumer.process_event(
        1, "linear:update:GRO-IDEMPOTENT:1", "update", payload_json, time.time()
    )

    # Simulate a replay/retry after restart: clear the in-memory issue window and
    # reset the hot row marker. The persistent dedup ledger must still suppress it.
    consumer._recent_dispatches.clear()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE events SET processed = 0 WHERE rowid = 1")
        conn.commit()
    finally:
        conn.close()

    consumer.process_event(
        1, "linear:update:GRO-IDEMPOTENT:1", "update", payload_json, time.time()
    )

    assert dispatches == ["GRO-IDEMPOTENT"]
    conn = sqlite3.connect(db_path)
    try:
        processed = conn.execute(
            "SELECT processed FROM events WHERE rowid = 1"
        ).fetchone()[0]
        ledger_rows = conn.execute(
            "SELECT dedup_key, issue_id FROM processed_event_keys WHERE dedup_key = ?",
            ("linear:update:GRO-IDEMPOTENT:1",),
        ).fetchall()
    finally:
        conn.close()
    assert processed == 1
    assert ledger_rows == [("linear:update:GRO-IDEMPOTENT:1", "GRO-IDEMPOTENT")]


def test_dispatch_to_supervisor_uses_three_slots_and_profile_scoped_agy_home(monkeypatch):
    calls = []

    class Proc:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr(consumer.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(consumer, "AGY_CLI_HOME", str(Path("/home") / "ubuntu" / ".hermes" / "profiles" / "kai" / "home"))

    consumer.dispatch_to_supervisor("GRO-X3")

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[cmd.index("--max-concurrent") + 1] == "3"
    assert kwargs["env"]["AGY_CLI_HOME"] == str(Path("/home") / "ubuntu" / ".hermes" / "profiles" / "kai" / "home")
    assert kwargs["stdout"] is consumer.subprocess.DEVNULL
    assert kwargs["stderr"] is consumer.subprocess.DEVNULL


def test_legacy_event_without_dedup_key_gets_stable_content_key(tmp_path):
    db_path = tmp_path / "event_log.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE events (rowid INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, payload_json TEXT NOT NULL, ts REAL NOT NULL)"
        )
        consumer.ensure_schema(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()
        }
    finally:
        conn.close()

    assert "processed" in columns
    key1 = consumer._stable_dedup_key(None, "update", '{"a":1}')
    key2 = consumer._stable_dedup_key(None, "update", '{"a":1}')
    key3 = consumer._stable_dedup_key(None, "update", '{"a":2}')
    assert key1 == key2
    assert key1 != key3
    assert key1.startswith("legacy:update:")
