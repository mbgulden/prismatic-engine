"""Tests for prismatic.dedup — SQLite-backed event deduplication.

GRO-2401 follow-up: EventRouterDedup is the per-webhook-instance dedup
store. Before GRO-2401, multiple in-flight webhook dispatches would race
on the SQLite WAL and fail with "database is locked". This test
verifies the fix (PRAGMA busy_timeout + WAL mode) and the basic API.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))

from prismatic.dedup import (  # noqa: E402
    EventRouterDedup,
    linear_key,
    command_key,
    github_key,
    cron_key,
    manual_key,
    DEFAULT_TTLS,
)


# ── Key builders ────────────────────────────────────────────────────
def test_linear_key_format():
    assert linear_key("GRO-1234", "agent:fred") == "linear:GRO-1234:agent:fred"


def test_command_key_format():
    assert command_key("GRO-1234", "comment-99") == "command:GRO-1234:comment-99"


def test_github_key_format():
    assert github_key("foo/bar", 42, "opened") == "github:foo/bar:42:opened"
    # str PR number also works
    assert github_key("foo/bar", "42", "opened") == "github:foo/bar:42:opened"


def test_cron_key_format():
    assert cron_key("default", "morning", "GRO-1234") == "cron:default:morning:GRO-1234"


def test_manual_key_format():
    assert manual_key("evt-abc") == "manual:evt-abc"


# ── DB connection + pragmas (GRO-2401 fix) ───────────────────────────
def test_dedup_uses_wal_and_busy_timeout(tmp_path):
    """GRO-2401: PRAGMA busy_timeout=5000 + WAL mode set on the dedup connection."""
    db = tmp_path / "dedup_test.db"
    dedup = EventRouterDedup(db_path=str(db))
    try:
        conn = dedup._conn
        assert conn is not None
        cur = conn.cursor()
        busy = cur.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = cur.execute("PRAGMA journal_mode").fetchone()[0]
        assert busy == 5000
        assert journal.lower() == "wal"
    finally:
        dedup.close()


def test_dedup_creates_parent_directory(tmp_path):
    """Auto-creates the DB parent directory on init."""
    db = tmp_path / "subdir" / "more" / "dedup.db"
    assert not db.parent.exists()
    dedup = EventRouterDedup(db_path=str(db))
    try:
        assert db.parent.exists()
    finally:
        dedup.close()


# ── Core operations: mark + check ───────────────────────────────────
def test_mark_processed_then_is_processed(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        key = linear_key("GRO-1", "agent:fred")
        assert not dedup.is_processed(key)
        dedup.mark_processed(key, event_type="linear")
        assert dedup.is_processed(key)
    finally:
        dedup.close()


def test_is_processed_returns_false_for_unknown_key(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        assert not dedup.is_processed("never-marked")
    finally:
        dedup.close()


def test_mark_processed_uses_default_ttl_for_event_type(tmp_path):
    """When ttl=None, uses DEFAULT_TTLS[event_type]."""
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_processed("k1", event_type="linear")
        dedup.mark_processed("k2", event_type="cron")
        # Both should be marked with their default TTLs
        assert dedup.is_processed("k1")
        assert dedup.is_processed("k2")
    finally:
        dedup.close()


def test_mark_processed_with_custom_ttl(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_processed("short", event_type="linear", ttl=1)
        assert dedup.is_processed("short")
        # Wait for expiry
        time.sleep(1.5)
        assert not dedup.is_processed("short")
    finally:
        dedup.close()


def test_mark_processed_overwrites_previous(tmp_path):
    """INSERT OR REPLACE: marking same key twice updates metadata."""
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_processed("k", event_type="linear", metadata={"v": 1})
        dedup.mark_processed("k", event_type="linear", metadata={"v": 2})
        # Should still be marked (no error from unique constraint)
        assert dedup.is_processed("k")
    finally:
        dedup.close()


# ── Convenience helpers ─────────────────────────────────────────────
def test_mark_issue_dispatched(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_issue_dispatched("GRO-1", "agent:fred")
        assert dedup.is_issue_dispatched_recently("GRO-1", "agent:fred")
        assert not dedup.is_issue_dispatched_recently("GRO-1", "agent:kai")
    finally:
        dedup.close()


def test_mark_command_processed(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_command_processed("GRO-1", "comment-1")
        assert dedup.is_command_processed("GRO-1", "comment-1")
        assert not dedup.is_command_processed("GRO-1", "comment-2")
    finally:
        dedup.close()


# ── Cleanup ────────────────────────────────────────────────────────
def test_cleanup_expired_removes_old_entries(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        # Add expired + fresh entries
        dedup.mark_processed("old", event_type="linear", ttl=1)
        dedup.mark_processed("fresh", event_type="linear", ttl=3600)
        time.sleep(1.5)
        n_removed = dedup.cleanup_expired()
        assert n_removed == 1
        assert not dedup.is_processed("old")
        assert dedup.is_processed("fresh")
    finally:
        dedup.close()


def test_cleanup_expired_returns_zero_when_nothing_expired(tmp_path):
    dedup = EventRouterDedup(db_path=str(tmp_path / "d.db"))
    try:
        dedup.mark_processed("fresh", event_type="linear", ttl=3600)
        assert dedup.cleanup_expired() == 0
    finally:
        dedup.close()


# ── Concurrency: GRO-2401 regression test ─────────────────────────
def test_concurrent_mark_processed_no_database_locked_error(tmp_path):
    """GRO-2401: concurrent writers don't fail with 'database is locked'."""
    db = str(tmp_path / "concurrent.db")
    dedup = EventRouterDedup(db_path=db)
    try:
        errors: list[Exception] = []

        def writer(start_key: int) -> None:
            try:
                local = EventRouterDedup(db_path=db)
                for i in range(20):
                    local.mark_processed(
                        f"thread-{start_key}-key-{i}",
                        event_type="linear",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No thread should have hit "database is locked"
        for exc in errors:
            assert "locked" not in str(exc).lower(), f"Got: {exc}"
        assert not errors, f"Errors: {errors}"

        # All 100 entries should be present
        assert dedup.cleanup_expired() == 0
        conn = dedup._conn
        assert conn is not None
        cur = conn.execute("SELECT COUNT(*) FROM processed_events")
        assert cur.fetchone()[0] == 100
    finally:
        dedup.close()


# ── Default TTL sanity ─────────────────────────────────────────────
def test_default_ttls_keys():
    """All known event types have a default TTL."""
    assert "linear" in DEFAULT_TTLS
    assert "command" in DEFAULT_TTLS
    assert "github" in DEFAULT_TTLS
    assert "cron" in DEFAULT_TTLS
    assert "manual" in DEFAULT_TTLS
    # All TTLs are positive integers
    for k, v in DEFAULT_TTLS.items():
        assert v > 0, f"TTL for {k} must be positive, got {v}"