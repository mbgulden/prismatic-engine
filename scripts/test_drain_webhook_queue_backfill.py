"""Focused replay/backfill tests for scripts/drain_webhook_queue.py.

These live under scripts/ because Ned owns scripts/ and the lane guard rejects
new tests/ writes.
"""

from __future__ import annotations

import json
import argparse
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))


def _agent_payload(identifier: str) -> str:
    return json.dumps(
        {
            "action": "update",
            "type": "Issue",
            "data": {
                "identifier": identifier,
                "labels": {"nodes": [{"name": "agent:ned"}]},
            },
        }
    )


def _make_queue(tmp_path, monkeypatch):
    db_path = tmp_path / "linear_webhook_queue.db"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    now = time.time()
    rows = [
        (
            "old-stale",
            "GRO-1000",
            "Issue",
            "update",
            now - 500,
            _agent_payload("GRO-1000"),
            "stale",
        ),
        (
            "old-failed",
            "GRO-1001",
            "Issue",
            "update",
            now - 400,
            _agent_payload("GRO-1001"),
            "failed: timeout",
        ),
        (
            "in-window",
            "GRO-1002",
            "Issue",
            "update",
            now - 300,
            _agent_payload("GRO-1002"),
            "stale",
        ),
        (
            "pending",
            "GRO-1003",
            "Issue",
            "update",
            now - 200,
            _agent_payload("GRO-1003"),
            "pending",
        ),
        (
            "too-new",
            "GRO-1004",
            "Issue",
            "update",
            now - 100,
            _agent_payload("GRO-1004"),
            "failed: api",
        ),
    ]
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE linear_webhook_queue (
            event_id TEXT PRIMARY KEY,
            identifier TEXT NOT NULL,
            event_type TEXT NOT NULL,
            action TEXT NOT NULL,
            received_at REAL NOT NULL,
            raw_json TEXT NOT NULL,
            dispatch_status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    con.executemany("INSERT INTO linear_webhook_queue VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db_path, now


def test_backfill_selection_includes_stale_failed_and_pending_with_bounds(
    tmp_path, monkeypatch
):
    from drain_webhook_queue import _connect, pending_events

    db_path, now = _make_queue(tmp_path, monkeypatch)
    conn = _connect(db_path)
    events = pending_events(
        conn,
        20,
        include_replayable=True,
        since=now - 350,
        until=now - 150,
    )
    conn.close()

    assert [event["identifier"] for event in events] == ["GRO-1002", "GRO-1003"]


def test_backfill_replays_failed_rows_idempotently_via_dispatcher_dedup(
    tmp_path, monkeypatch
):
    from drain_webhook_queue import drain

    db_path, now = _make_queue(tmp_path, monkeypatch)
    calls: list[str] = []

    def fake_dispatch(*, identifier: str) -> bool:
        calls.append(identifier)
        return True

    args = argparse.Namespace(
        dry_run=False,
        max=20,
        stale_only=False,
        backfill=True,
        reset=False,
        since=now - 450,
        until=now - 150,
    )

    assert drain(args, dispatch_fn=fake_dispatch) == 0
    assert calls == ["GRO-1001", "GRO-1002", "GRO-1003"]

    con = sqlite3.connect(str(db_path))
    statuses = dict(
        con.execute(
            "SELECT identifier, dispatch_status FROM linear_webhook_queue"
        ).fetchall()
    )
    con.close()

    assert statuses["GRO-1001"] == "dispatched"
    assert statuses["GRO-1002"] == "dispatched"
    assert statuses["GRO-1003"] == "dispatched"
    assert statuses["GRO-1000"] == "stale"
    assert statuses["GRO-1004"] == "failed: api"


def test_help_documents_backfill_and_range_bounds():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "drain_webhook_queue.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--backfill" in result.stdout
    assert "--since" in result.stdout
    assert "--until" in result.stdout
