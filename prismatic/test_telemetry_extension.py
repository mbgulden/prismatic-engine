"""
prismatic/test_telemetry_extension.py — Gap 12 telemetry extension tests

Tests for the 4 new record_* methods, 4 new SQLite tables, and extended
get_dashboard_data() with review/hooks/plugins blocks.

Tests #22 and #23 (end-to-end through RealPRReviewer / PipelineOrchestrator)
are DEFERRED to Gap 11 — they require telemetry integration in
pr_reviewer_impl.py and pipeline.py (Ned's lane).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from prismatic.telemetry import TelemetryCollector


# ── Fixture: isolated in-memory collector ────────────────────────────────────


@pytest.fixture()
def collector(tmp_path):
    """TelemetryCollector backed by a temp DB — isolated per test."""
    db_path = str(tmp_path / "test_telemetry.db")
    c = TelemetryCollector(db_path=db_path)
    yield c, db_path
    c._running = False  # stop daemon thread


def _wait_drain(c: TelemetryCollector, db_path: str, table: str, timeout: float = 3.0):
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


# ── TestRecordReviewCompleted ────────────────────────────────────────────────


class TestRecordReviewCompleted:
    def test_record_review_completed_inserts_row(self, collector):
        """Push a review.completed event and verify the row appears."""
        c, db_path = collector
        c.record_review_completed(
            run_id="run-001",
            issue_id="GRO-100",
            reviewer="ned",
            verdict="approve",
            impact="trivial",
        )
        rows = _wait_drain(c, db_path, "telemetry_review_completed")
        assert len(rows) >= 1
        row = rows[0]
        assert row["run_id"] == "run-001"
        assert row["issue_id"] == "GRO-100"
        assert row["reviewer"] == "ned"
        assert row["verdict"] == "approve"
        assert row["impact"] == "trivial"

    def test_record_review_completed_with_rework_attempt(self, collector):
        """rework_attempt column is populated correctly."""
        c, db_path = collector
        c.record_review_completed(
            run_id="run-002",
            issue_id="GRO-101",
            reviewer="ned",
            verdict="request_changes",
            impact="minor",
            rework_attempt=2,
        )
        rows = _wait_drain(c, db_path, "telemetry_review_completed")
        assert rows, "No rows drained in time"
        assert rows[0]["rework_attempt"] == 2

    def test_record_review_completed_with_zero_duration(self, collector):
        """duration_sec=0.0 is accepted without error."""
        c, db_path = collector
        c.record_review_completed(
            run_id="run-003",
            issue_id="GRO-102",
            reviewer="ned",
            verdict="approve",
            impact="trivial",
            duration_sec=0.0,
        )
        rows = _wait_drain(c, db_path, "telemetry_review_completed")
        assert rows, "No rows drained in time"
        assert rows[0]["duration_sec"] == 0.0

    def test_record_review_completed_uses_telemetry_prefix(self, collector):
        """Table name begins with telemetry_ (not an inconsistency like agy_live_state)."""
        c, db_path = collector
        c.record_review_completed(
            run_id="run-004",
            issue_id="GRO-103",
            reviewer="ned",
            verdict="approve",
            impact="trivial",
        )
        _wait_drain(c, db_path, "telemetry_review_completed")
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                ("telemetry_review_completed",),
            )
            assert cursor.fetchone() is not None, (
                "Table telemetry_review_completed missing"
            )
        finally:
            conn.close()


# ── TestRecordPluginRegistered ───────────────────────────────────────────────


class TestRecordPluginRegistered:
    def test_record_plugin_registered_success_path(self, collector):
        """success=True → row with success=1."""
        c, db_path = collector
        c.record_plugin_registered(
            plugin_name="prismatic-hello-world",
            plugin_version="1.0.0",
            source="entry_points",
            success=True,
        )
        rows = _wait_drain(c, db_path, "telemetry_plugin_registered")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["plugin_name"] == "prismatic-hello-world"
        assert row["success"] == 1
        assert row["error"] is None

    def test_record_plugin_registered_failure_path_with_error(self, collector):
        """success=False + error message → success=0, error populated."""
        c, db_path = collector
        c.record_plugin_registered(
            plugin_name="bad-plugin",
            success=False,
            error="ImportError: cannot import name 'register'",
        )
        rows = _wait_drain(c, db_path, "telemetry_plugin_registered")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["success"] == 0
        assert "ImportError" in row["error"]

    def test_record_plugin_registered_with_version_metadata(self, collector):
        """plugin_version and source fields are persisted."""
        c, db_path = collector
        c.record_plugin_registered(
            plugin_name="prismatic-web-plugin",
            plugin_version="0.1.0",
            source="pip",
            success=True,
        )
        rows = _wait_drain(c, db_path, "telemetry_plugin_registered")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["plugin_version"] == "0.1.0"
        assert row["source"] == "pip"


# ── TestRecordHookFired ──────────────────────────────────────────────────────


class TestRecordHookFired:
    def test_record_hook_fired_success_path(self, collector):
        """Hook success → success=1 in the table."""
        c, db_path = collector
        c.record_hook_fired(
            hook_name="HOOK_BEFORE_NED_REVIEW",
            event_type="hook.fired",
            run_id="run-010",
            issue_id="GRO-200",
            success=True,
            duration_ms=12.5,
        )
        rows = _wait_drain(c, db_path, "telemetry_hook_fired")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["hook_name"] == "HOOK_BEFORE_NED_REVIEW"
        assert row["success"] == 1
        assert row["duration_ms"] == pytest.approx(12.5)

    def test_record_hook_fired_failure_path(self, collector):
        """Hook failure → success=0, error populated, duration_ms preserved."""
        c, db_path = collector
        c.record_hook_fired(
            hook_name="HOOK_AFTER_NED_REVIEW",
            event_type="hook.failed",
            run_id="run-011",
            issue_id="GRO-201",
            success=False,
            error="TimeoutError: hook took too long",
            duration_ms=500.0,
        )
        rows = _wait_drain(c, db_path, "telemetry_hook_fired")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["success"] == 0
        assert "TimeoutError" in row["error"]
        assert row["duration_ms"] == pytest.approx(500.0)

    def test_record_hook_fired_with_optional_run_id_issue_id(self, collector):
        """Omitting run_id / issue_id stores NULL in the table."""
        c, db_path = collector
        c.record_hook_fired(
            hook_name="HOOK_BEFORE_CLASSIFY_IMPACT",
            event_type="hook.fired",
        )
        rows = _wait_drain(c, db_path, "telemetry_hook_fired")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["run_id"] is None
        assert row["issue_id"] is None


# ── TestRecordPipelineAction ─────────────────────────────────────────────────


class TestRecordPipelineAction:
    def test_record_pipeline_action_inserts_row(self, collector):
        """All 4 ACTION_* values are accepted without error."""
        c, db_path = collector
        for action in [
            "ACTION_ADVANCE",
            "ACTION_HOLD",
            "ACTION_REWORK",
            "ACTION_GIVE_UP",
        ]:
            c.record_pipeline_action(
                action=action,
                run_id=f"run-{action}",
                issue_id="GRO-300",
            )
        rows = _wait_drain(c, db_path, "telemetry_pipeline_action")
        # We expect 4 rows eventually; wait for them
        deadline = time.monotonic() + 3.0
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            while time.monotonic() < deadline:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM telemetry_pipeline_action"
                    ).fetchall()
                ]
                if len(rows) >= 4:
                    break
                time.sleep(0.05)
        finally:
            conn.close()
        actions_stored = {r["action"] for r in rows}
        assert "ACTION_ADVANCE" in actions_stored
        assert "ACTION_REWORK" in actions_stored

    def test_record_pipeline_action_with_details_json(self, collector):
        """details TEXT column accepts a JSON-serialized complex payload."""
        c, db_path = collector
        details = '{"rework_payload": {"prompt": "Please fix the loop.", "depth": 1}}'
        c.record_pipeline_action(
            action="ACTION_REWORK",
            run_id="run-details",
            issue_id="GRO-301",
            actor="review-orchestrator",
            details=details,
        )
        rows = _wait_drain(c, db_path, "telemetry_pipeline_action")
        assert rows, "No rows drained"
        row = rows[0]
        assert row["details"] == details
        assert row["actor"] == "review-orchestrator"


# ── TestDashboardExtension ───────────────────────────────────────────────────


class TestDashboardExtension:
    def test_get_dashboard_data_includes_review_block(self, collector):
        """get_dashboard_data() returns a 'review' key."""
        c, db_path = collector
        # Insert a row and wait for it to drain before querying
        c.record_review_completed(
            run_id="dash-run-1",
            issue_id="GRO-400",
            reviewer="ned",
            verdict="approve",
            impact="trivial",
        )
        _wait_drain(c, db_path, "telemetry_review_completed")
        data = c.get_dashboard_data(hours=24)
        assert "review" in data
        assert isinstance(data["review"], list)
        # Should have at least one entry with verdict=approve
        verdicts = {r["verdict"] for r in data["review"]}
        assert "approve" in verdicts

    def test_get_dashboard_data_includes_hooks_block(self, collector):
        """get_dashboard_data() returns a 'hooks' block with succeeded/failed counts."""
        c, db_path = collector
        c.record_hook_fired(
            hook_name="HOOK_BEFORE_NED_REVIEW",
            event_type="hook.fired",
            success=True,
        )
        c.record_hook_fired(
            hook_name="HOOK_BEFORE_NED_REVIEW",
            event_type="hook.failed",
            success=False,
        )
        _wait_drain(c, db_path, "telemetry_hook_fired")
        # Allow second row to appear
        time.sleep(0.3)
        data = c.get_dashboard_data(hours=24)
        assert "hooks" in data
        hooks = data["hooks"]
        assert "succeeded" in hooks
        assert "failed" in hooks
        assert hooks["succeeded"] >= 1
        assert hooks["failed"] >= 1

    def test_get_dashboard_data_includes_plugins_block(self, collector):
        """get_dashboard_data() returns a 'plugins' block with registered/failed counts."""
        c, db_path = collector
        c.record_plugin_registered(plugin_name="plugin-ok", success=True)
        c.record_plugin_registered(
            plugin_name="plugin-bad", success=False, error="boom"
        )
        _wait_drain(c, db_path, "telemetry_plugin_registered")
        time.sleep(0.3)
        data = c.get_dashboard_data(hours=24)
        assert "plugins" in data
        plugins = data["plugins"]
        assert "registered" in plugins
        assert "register_failed" in plugins
        assert plugins["registered"] >= 1
        assert plugins["register_failed"] >= 1


# ── TestNewTablesExist ───────────────────────────────────────────────────────


class TestNewTablesExist:
    """Verify all 4 new tables are created idempotently by _ensure_tables."""

    @pytest.mark.parametrize(
        "table",
        [
            "telemetry_review_completed",
            "telemetry_plugin_registered",
            "telemetry_hook_fired",
            "telemetry_pipeline_action",
        ],
    )
    def test_table_exists_after_collector_init(self, collector, table):
        """Table should exist immediately after TelemetryCollector.__init__."""
        _, db_path = collector
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert cursor.fetchone() is not None, f"Table {table!r} was not created"
        finally:
            conn.close()


# ── TestRecordMethodsExist ───────────────────────────────────────────────────


class TestRecordMethodsExist:
    """Smoke-test: all 4 new record_* methods are present on TelemetryCollector."""

    @pytest.mark.parametrize(
        "method_name",
        [
            "record_review_completed",
            "record_plugin_registered",
            "record_hook_fired",
            "record_pipeline_action",
        ],
    )
    def test_method_exists(self, collector, method_name):
        c, _ = collector
        assert hasattr(c, method_name), f"Missing method: {method_name}"
        assert callable(getattr(c, method_name))


# ── TestCleanupExpired ───────────────────────────────────────────────────────


class TestCleanupExpired:
    """Gap 12 tables appear in cleanup_expired's retention map."""

    def test_cleanup_expired_returns_gap12_tables(self, collector):
        """cleanup_expired(dry_run=True) returns all 4 new tables in the result dict."""
        c, _ = collector
        result = c.cleanup_expired(dry_run=True)
        assert "telemetry_review_completed" in result
        assert "telemetry_plugin_registered" in result
        assert "telemetry_hook_fired" in result
        assert "telemetry_pipeline_action" in result


# ── Deferred tests (Gap 11) ─────────────────────────────────────────────────
#
# Test #22: test_real_reviewer_completing_emits_review_completed_event
#   Requires Gap 11's telemetry integration in pr_reviewer_impl.py
#   (RealPRReviewer.__init__ accepts optional telemetry param).
#
# Test #23: test_pipeline_action_advance_emits_pipeline_action_event
#   Requires Gap 11's telemetry integration in pipeline.py
#   (PipelineOrchestrator.__init__ accepts optional telemetry param).
#
# Both will be added to test_telemetry_extension.py as part of Gap 11 PR.
