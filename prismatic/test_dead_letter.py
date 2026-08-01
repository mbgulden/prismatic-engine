from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


from prismatic.dead_letter import DeadLetterStore, replay


def test_record_failure_retains_retry_payload(tmp_path):
    store = DeadLetterStore(tmp_path / "dead.db")

    event = store.record_failure(
        event_id="evt-1",
        source="linear",
        event_type="Issue",
        payload={"identifier": "GRO-1"},
        error="temporary Linear timeout",
        attempts=1,
        max_attempts=3,
        retry_after_seconds=10,
        now=100.0,
    )

    assert event.status == "retry"
    assert event.next_retry_at == 110.0
    assert event.payload == {"identifier": "GRO-1"}

    con = sqlite3.connect(tmp_path / "dead.db")
    row = con.execute("SELECT payload_json, status FROM dead_letter_events").fetchone()
    con.close()
    assert json.loads(row[0]) == {"identifier": "GRO-1"}
    assert row[1] == "retry"


def test_record_failure_dead_letters_after_attempt_cap(tmp_path):
    store = DeadLetterStore(tmp_path / "dead.db")

    event = store.record_failure(
        event_id="evt-2",
        source="gateway",
        event_type="webhook_queued",
        payload={"event_id": "evt-2"},
        error="handler keeps failing",
        attempts=3,
        max_attempts=3,
        now=200.0,
    )

    assert event.status == "dead_letter"
    assert event.next_retry_at is None
    assert store.due_for_replay(now=200.0)[0].event_id == "evt-2"


def test_record_failure_is_idempotent_by_event_source(tmp_path):
    store = DeadLetterStore(tmp_path / "dead.db")
    first = store.record_failure(
        event_id="evt-3",
        source="linear",
        event_type="Issue",
        payload={"version": 1},
        error="first",
        attempts=1,
        now=1.0,
    )
    second = store.record_failure(
        event_id="evt-3",
        source="linear",
        event_type="Issue",
        payload={"version": 2},
        error="second",
        attempts=2,
        now=2.0,
    )

    assert first.id == second.id
    assert second.payload == {"version": 2}
    assert second.error == "second"
    assert second.attempts == 2


def test_replay_marks_success_without_deleting_payload(tmp_path):
    store = DeadLetterStore(tmp_path / "dead.db")
    store.record_failure(
        event_id="evt-4",
        source="linear",
        event_type="Issue",
        payload={"identifier": "GRO-4"},
        error="failed",
        attempts=3,
        max_attempts=3,
        now=1.0,
    )
    seen = []

    stats = replay(
        store, lambda event: seen.append(event.payload["identifier"]) or True, now=2.0
    )

    assert stats == {"selected": 1, "replayed": 1, "failed": 0}
    assert seen == ["GRO-4"]
    event = store.get(event_id="evt-4", source="linear")
    assert event.status == "replayed"
    assert event.payload == {"identifier": "GRO-4"}
    assert event.replayed_at is not None


def test_replay_failure_reschedules_then_dead_letters(tmp_path):
    store = DeadLetterStore(tmp_path / "dead.db")
    event = store.record_failure(
        event_id="evt-5",
        source="linear",
        event_type="Issue",
        payload={"identifier": "GRO-5"},
        error="failed",
        attempts=1,
        max_attempts=3,
        now=1.0,
    )

    updated = store.mark_replay_failed(row_id=event.id, error="still down")
    assert updated.status == "retry"
    assert updated.attempts == 2

    updated = store.mark_replay_failed(row_id=event.id, error="cap reached")
    assert updated.status == "dead_letter"
    assert updated.attempts == 3


def test_cli_lists_retained_events(tmp_path):
    db = tmp_path / "dead.db"
    store = DeadLetterStore(db)
    store.record_failure(
        event_id="evt-cli",
        source="linear",
        event_type="Issue",
        payload={"identifier": "GRO-CLI"},
        error="boom",
        attempts=3,
        max_attempts=3,
        now=1.0,
    )

    script = (
        Path(__file__).resolve().parent.parent / "scripts" / "prismatic_dead_letter.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(db), "list"],
        text=True,
        capture_output=True,
        check=True,
    )

    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert rows[0]["event_id"] == "evt-cli"
    assert rows[0]["status"] == "dead_letter"
    assert rows[0]["payload"] == {"identifier": "GRO-CLI"}
