"""Robustness coverage for dispatch_consumer_v3.

Covers three production-hardening fixes (2026-09-28):
- poison-shape events (valid JSON, wrong shape) are dead-lettered instead of
  wedging the consumer in a silent infinite stall;
- fail-closed gate failures back off instead of crash-looping through systemd;
- vacuum_processed prunes stale dedup keys so processed_event_keys can't grow
  unbounded.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer


def _init_db(path):
    conn = sqlite3.connect(path)
    try:
        consumer.ensure_schema(conn)
    finally:
        conn.close()


def _insert_event(path, dedup_key, topic, payload_json):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed)"
            " VALUES (?, ?, ?, ?, 0)",
            (dedup_key, topic, payload_json, time.time()),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _processed_flag(path, rowid):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT processed FROM events WHERE rowid = ?", (rowid,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_poison_data_null_is_dead_lettered_not_stuck(tmp_path, monkeypatch):
    """{"type": "Issue", "data": null} used to raise AttributeError pre-claim,
    leaving the row unprocessed and the cursor stuck: every poll refetched the
    same event forever (silent stall). It must now be skipped + marked."""
    db_path = tmp_path / "event_log.sqlite"
    _init_db(db_path)
    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))

    payload = json.dumps({"type": "Issue", "data": None})
    rowid = _insert_event(db_path, "poison-1", "update", payload)

    # Must not raise.
    consumer.process_event(rowid, "poison-1", "update", payload, time.time())

    assert _processed_flag(db_path, rowid) == 1


def test_poison_non_dict_payload_is_dead_lettered(tmp_path, monkeypatch):
    """A JSON array payload is valid JSON but has no .get(); same wedge."""
    db_path = tmp_path / "event_log.sqlite"
    _init_db(db_path)
    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))

    payload = json.dumps([1, 2, 3])
    rowid = _insert_event(db_path, "poison-2", "update", payload)

    consumer.process_event(rowid, "poison-2", "update", payload, time.time())

    assert _processed_flag(db_path, rowid) == 1


def test_poison_data_wrong_type_is_dead_lettered(tmp_path, monkeypatch):
    db_path = tmp_path / "event_log.sqlite"
    _init_db(db_path)
    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))

    payload = json.dumps({"type": "Issue", "data": "not-a-dict"})
    rowid = _insert_event(db_path, "poison-3", "update", payload)

    consumer.process_event(rowid, "poison-3", "update", payload, time.time())

    assert _processed_flag(db_path, rowid) == 1


def test_classify_loop_error():
    marker = (
        "[FAIL_CLOSED] Cursor ahead of max rowid: cursor=9 > max=5\n"
        "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
    )
    assert consumer._classify_loop_error(RuntimeError(marker)) == "backoff"
    assert consumer._classify_loop_error(ValueError(marker)) == "backoff"
    assert consumer._classify_loop_error(RuntimeError("boom")) == "raise"
    assert consumer._classify_loop_error(ValueError("boom")) == "continue"


def test_main_loop_backs_off_on_fail_closed_gate(tmp_path, monkeypatch):
    """A permanently-failing gate must sleep with backoff, not raise (which
    used to crash the process into a 33k-restart systemd storm)."""

    class _Stop(Exception):
        pass

    sleeps: list[float] = []

    def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 2:
            raise _Stop()

    monkeypatch.setattr(
        consumer,
        "verify_startup_gate",
        lambda *a, **k: (
            False,
            (
                "[FAIL_CLOSED] Cursor ahead of max rowid: cursor=9 > max=5\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ),
            None,
        ),
    )
    monkeypatch.setattr(consumer.time, "sleep", fake_sleep)

    with pytest.raises(_Stop):
        consumer.main_loop()

    assert sleeps == [consumer.FAIL_CLOSED_BACKOFF_SEC] * 2
    assert consumer.FAIL_CLOSED_BACKOFF_SEC == 300


def test_vacuum_prunes_stale_dedup_keys(tmp_path, monkeypatch):
    db_path = tmp_path / "event_log.sqlite"
    _init_db(db_path)
    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))

    now = time.time()
    conn = sqlite3.connect(str(db_path))
    try:
        # Old processed event -> vacuumed; fresh processed event -> kept.
        conn.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed)"
            " VALUES ('k-old-ev', 'update', '{}', ?, 1)",
            (now - 2 * 86400,),
        )
        conn.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed)"
            " VALUES ('k-new-ev', 'update', '{}', ?, 1)",
            (now,),
        )
        # Stale dedup key -> pruned; fresh key -> kept.
        conn.execute(
            "INSERT INTO processed_event_keys"
            " (dedup_key, first_rowid, topic, issue_id, processed_at)"
            " VALUES ('k-old', 1, 'update', 'X', ?)",
            (now - 31 * 86400,),
        )
        conn.execute(
            "INSERT INTO processed_event_keys"
            " (dedup_key, first_rowid, topic, issue_id, processed_at)"
            " VALUES ('k-new', 2, 'update', 'Y', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    consumer.vacuum_processed(expected_generation=None, db_path=str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        events = {
            r[0] for r in conn.execute("SELECT dedup_key FROM events").fetchall()
        }
        keys = {
            r[0]
            for r in conn.execute("SELECT dedup_key FROM processed_event_keys").fetchall()
        }
    finally:
        conn.close()

    assert events == {"k-new-ev"}
    assert keys == {"k-new"}
