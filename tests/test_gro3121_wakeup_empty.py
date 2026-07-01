"""
tests/test_gro3121_wakeup_empty.py — GRO-3121 wakeup-empty metric.

Tests for the wakeup-empty telemetry metric:
  - TelemetryCollector.record_wakeup_empty() inserts rows
  - The telemetry_wakeup_empty SQLite table exists with the right schema
  - get_dashboard_data() exposes a wakeup_empty block with count/per_hour/by_agent
  - Dispatcher main_loop records a wakeup_empty row on empty cycles

Reference: GRO-3121 / Linear issue body — measure polling cost before
deciding to replace polling with webhook subscription.
"""

from __future__ import annotations

import sqlite3
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from prismatic.telemetry import TelemetryCollector


# ── Fixture: isolated in-memory collector ────────────────────────────────────


@pytest.fixture()
def collector(tmp_path):
    """TelemetryCollector backed by a temp DB — isolated per test."""
    db_path = str(tmp_path / "test_telemetry.db")
    c = TelemetryCollector(db_path=db_path)
    yield c, db_path
    c._running = False


def _wait_drain(c: TelemetryCollector, db_path: str, table: str,
                timeout: float = 3.0) -> list[dict]:
    """Block until at least one row appears in *table*, or timeout."""
    deadline = time.monotonic() + timeout
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        while time.monotonic() < deadline:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            if rows:
                return [dict(r) for r in rows]
            time.sleep(0.05)
        return []
    finally:
        conn.close()


def _wait_count(c: TelemetryCollector, db_path: str, table: str,
                expected: int, timeout: float = 3.0) -> int:
    """Block until *table* contains >= *expected* rows."""
    deadline = time.monotonic() + timeout
    conn = sqlite3.connect(db_path)
    try:
        while time.monotonic() < deadline:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if cnt >= expected:
                return cnt
            time.sleep(0.05)
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ── TestSchema ──────────────────────────────────────────────────────────────


class TestSchema:
    def test_wakeup_empty_table_exists(self, collector):
        """telemetry_wakeup_empty table is created by _ensure_tables()."""
        _, db_path = collector
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='telemetry_wakeup_empty'"
            ).fetchone()
            assert row is not None, "telemetry_wakeup_empty table missing"
        finally:
            conn.close()

    def test_wakeup_empty_columns(self, collector):
        """Table has expected columns."""
        _, db_path = collector
        conn = sqlite3.connect(db_path)
        try:
            cols = {
                row[1] for row in
                conn.execute("PRAGMA table_info(telemetry_wakeup_empty)").fetchall()
            }
            expected = {"id", "agent", "cycle_id", "duration_sec",
                        "reason", "created_at"}
            assert expected.issubset(cols), f"missing columns: {expected - cols}"
        finally:
            conn.close()

    def test_wakeup_empty_indexes_exist(self, collector):
        """Indexes for (agent, created_at) and created_at exist."""
        _, db_path = collector
        conn = sqlite3.connect(db_path)
        try:
            indexes = {
                row[0] for row in
                conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='telemetry_wakeup_empty'"
                ).fetchall()
            }
            assert "idx_wakeup_empty_agent" in indexes
            assert "idx_wakeup_empty_time" in indexes
        finally:
            conn.close()


# ── TestRecordWakeupEmpty ───────────────────────────────────────────────────


class TestRecordWakeupEmpty:
    def test_inserts_row_with_defaults(self, collector):
        """record_wakeup_empty() inserts one row with defaults."""
        c, db_path = collector
        c.record_wakeup_empty(agent="ned", cycle_id="20260701000000")
        rows = _wait_drain(c, db_path, "telemetry_wakeup_empty")
        assert len(rows) == 1
        row = rows[0]
        assert row["agent"] == "ned"
        assert row["cycle_id"] == "20260701000000"
        assert row["duration_sec"] == 0.0
        assert row["reason"] == "queue_empty"
        assert row["created_at"]

    def test_inserts_row_with_full_args(self, collector):
        """record_wakeup_empty() respects all kwargs."""
        c, db_path = collector
        c.record_wakeup_empty(
            agent="dispatcher",
            cycle_id="20260701120000",
            duration_sec=0.42,
            reason="network_timeout",
        )
        rows = _wait_drain(c, db_path, "telemetry_wakeup_empty")
        assert len(rows) == 1
        assert rows[0]["agent"] == "dispatcher"
        assert rows[0]["cycle_id"] == "20260701120000"
        assert rows[0]["duration_sec"] == pytest.approx(0.42)
        assert rows[0]["reason"] == "network_timeout"

    def test_multiple_records_accumulate(self, collector):
        """Three records → three rows."""
        c, db_path = collector
        for i in range(3):
            c.record_wakeup_empty(agent="fred", cycle_id=f"cyc-{i:03d}")
        cnt = _wait_count(c, db_path, "telemetry_wakeup_empty", expected=3)
        assert cnt == 3

    def test_dropped_when_queue_full(self, collector):
        """A full queue silently drops, never raises."""
        c, db_path = collector
        # Block the writer by stopping it temporarily so the queue fills.
        # Easier: just confirm no exception when pushing more than capacity.
        c._running = False  # stop drain
        try:
            # Push > 10000 events; we just need to confirm no crash.
            for i in range(10050):
                c.record_wakeup_empty(agent="spam", cycle_id=f"s-{i}")
        finally:
            c._running = True  # restart for fixture teardown
            c._writer = __import__("threading").Thread(
                target=c._drain, daemon=True
            )
            c._writer.start()


# ── TestDashboardBlock ──────────────────────────────────────────────────────


class TestDashboardBlock:
    def test_dashboard_includes_wakeup_empty(self, collector):
        """get_dashboard_data() returns a wakeup_empty block."""
        c, db_path = collector
        c.record_wakeup_empty(agent="ned", cycle_id="d-1")
        _wait_drain(c, db_path, "telemetry_wakeup_empty")
        data = c.get_dashboard_data(hours=24)
        assert "wakeup_empty" in data
        block = data["wakeup_empty"]
        assert "count" in block
        assert "per_hour" in block
        assert "by_agent" in block
        assert isinstance(block["count"], int)
        assert isinstance(block["per_hour"], float)
        assert isinstance(block["by_agent"], list)

    def test_dashboard_count_matches_inserts(self, collector):
        """wakeup_empty.count equals the number of rows in window."""
        c, db_path = collector
        for i in range(5):
            c.record_wakeup_empty(agent="ned", cycle_id=f"n-{i}")
        for i in range(3):
            c.record_wakeup_empty(agent="fred", cycle_id=f"f-{i}")
        _wait_count(c, db_path, "telemetry_wakeup_empty", expected=8)
        data = c.get_dashboard_data(hours=24)
        assert data["wakeup_empty"]["count"] == 8
        by_agent = {r["agent"]: r["cnt"]
                    for r in data["wakeup_empty"]["by_agent"]}
        assert by_agent == {"ned": 5, "fred": 3}

    def test_per_hour_within_24h_window(self, collector):
        """per_hour = count / hours."""
        c, db_path = collector
        for i in range(96):
            c.record_wakeup_empty(agent="ned", cycle_id=f"24h-{i}")
        _wait_count(c, db_path, "telemetry_wakeup_empty", expected=96)
        data = c.get_dashboard_data(hours=24)
        assert data["wakeup_empty"]["per_hour"] == pytest.approx(4.0)
        assert data["wakeup_empty"]["count"] == 96

    def test_dashboard_excludes_rows_older_than_window(self, collector):
        """Rows older than the window are not counted."""
        c, db_path = collector
        # Insert a row dated 48h ago directly into the DB so we can test
        # the cutoff filter without waiting in real time.
        conn = sqlite3.connect(db_path)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        conn.execute(
            "INSERT INTO telemetry_wakeup_empty "
            "(agent, cycle_id, duration_sec, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("stale", "old-1", 0.0, "queue_empty", old_ts),
        )
        conn.commit()
        conn.close()
        # Add one fresh row through the API.
        c.record_wakeup_empty(agent="fresh", cycle_id="new-1")
        _wait_drain(c, db_path, "telemetry_wakeup_empty")
        data = c.get_dashboard_data(hours=24)
        # 24h window: only the fresh row counts.
        assert data["wakeup_empty"]["count"] == 1
        assert data["wakeup_empty"]["by_agent"][0]["agent"] == "fresh"

    def test_dashboard_empty_when_no_rows(self, collector):
        """Empty DB → wakeup_empty.count == 0, by_agent == []."""
        c, _ = collector
        data = c.get_dashboard_data(hours=24)
        assert data["wakeup_empty"]["count"] == 0
        assert data["wakeup_empty"]["by_agent"] == []
        assert data["wakeup_empty"]["per_hour"] == 0.0


# ── TestDispatcherWiring ────────────────────────────────────────────────────


class TestDispatcherWiring:
    """Verify the dispatcher main_loop records wakeup_empty on empty cycles.

    We patch out everything heavy (Linear API, AGY, signal providers) and
    drive a single cycle through dispatch_once. Then check that the
    telemetry table got a row.
    """

    def _mock_modules(self):
        """Mock heavy provider/policy modules so the dispatcher imports clean."""
        # These modules are imported by prismatic.dispatcher at top of file.
        # Patch them so the import doesn't fail or hit the network.
        sys.modules.setdefault(
            "prismatic.providers.signals", MagicMock(),
        )
        sys.modules.setdefault(
            "prismatic.credit_policy_engine", MagicMock(),
        )

    def test_main_loop_records_empty_wakeup(self, tmp_path, monkeypatch):
        """An empty cycle (dispatched=0, errors=0) records a wakeup_empty."""
        self._mock_modules()
        db_path = str(tmp_path / "dispatcher_test.db")

        # Build a collector backed by the temp DB.
        collector = TelemetryCollector(db_path=db_path)
        try:
            # Build a dispatcher module with main_loop + patched dependencies.
            import prismatic.dispatcher as dispatcher

            counts = {"dispatched": 0, "pipeline_setup": 0,
                      "stale_killed": 0, "errors": 0}

            # Patch out everything main_loop touches: dispatch_once,
            # collector, and the time.sleep so the loop exits after 1 cycle.
            with patch.object(dispatcher, "dispatch_once",
                              return_value=counts), \
                 patch.object(dispatcher, "get_collector",
                              return_value=collector), \
                 patch.object(dispatcher.time, "sleep",
                              side_effect=KeyboardInterrupt):
                try:
                    dispatcher.main_loop(interval=0, once=False)
                except KeyboardInterrupt:
                    pass  # expected, breaks the loop

            # Drain and verify exactly one wakeup_empty row.
            deadline = time.monotonic() + 3.0
            conn = sqlite3.connect(db_path)
            try:
                while time.monotonic() < deadline:
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM telemetry_wakeup_empty"
                    ).fetchone()[0]
                    if cnt >= 1:
                        break
                    time.sleep(0.05)
                rows = conn.execute(
                    "SELECT agent, reason FROM telemetry_wakeup_empty"
                ).fetchall()
            finally:
                conn.close()

            assert len(rows) >= 1, "expected wakeup_empty row to be recorded"
            assert rows[0][0] == "dispatcher"
            assert rows[0][1] == "queue_empty"
        finally:
            collector._running = False

    def test_main_loop_skips_when_dispatched(self, tmp_path, monkeypatch):
        """If dispatched > 0, no wakeup_empty row is recorded."""
        self._mock_modules()
        db_path = str(tmp_path / "dispatcher_test2.db")
        collector = TelemetryCollector(db_path=db_path)
        try:
            import prismatic.dispatcher as dispatcher
            counts = {"dispatched": 1, "pipeline_setup": 0,
                      "stale_killed": 0, "errors": 0}
            with patch.object(dispatcher, "dispatch_once",
                              return_value=counts), \
                 patch.object(dispatcher, "get_collector",
                              return_value=collector), \
                 patch.object(dispatcher.time, "sleep",
                              side_effect=KeyboardInterrupt):
                try:
                    dispatcher.main_loop(interval=0, once=False)
                except KeyboardInterrupt:
                    pass

            # Give the drain thread time to settle, then count.
            time.sleep(0.3)
            conn = sqlite3.connect(db_path)
            try:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM telemetry_wakeup_empty"
                ).fetchone()[0]
            finally:
                conn.close()
            assert cnt == 0, (
                f"expected 0 wakeup_empty rows when dispatched>0, got {cnt}"
            )
        finally:
            collector._running = False

    def test_main_loop_skips_when_errors(self, tmp_path, monkeypatch):
        """If errors > 0 (regardless of dispatched), no wakeup_empty row."""
        self._mock_modules()
        db_path = str(tmp_path / "dispatcher_test3.db")
        collector = TelemetryCollector(db_path=db_path)
        try:
            import prismatic.dispatcher as dispatcher
            counts = {"dispatched": 0, "pipeline_setup": 0,
                      "stale_killed": 0, "errors": 1}
            with patch.object(dispatcher, "dispatch_once",
                              return_value=counts), \
                 patch.object(dispatcher, "get_collector",
                              return_value=collector), \
                 patch.object(dispatcher.time, "sleep",
                              side_effect=KeyboardInterrupt):
                try:
                    dispatcher.main_loop(interval=0, once=False)
                except KeyboardInterrupt:
                    pass

            time.sleep(0.3)
            conn = sqlite3.connect(db_path)
            try:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM telemetry_wakeup_empty"
                ).fetchone()[0]
            finally:
                conn.close()
            assert cnt == 0, (
                f"expected 0 wakeup_empty rows when errors>0, got {cnt}"
            )
        finally:
            collector._running = False