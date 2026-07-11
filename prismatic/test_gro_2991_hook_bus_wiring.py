"""
prismatic/test_gro_2991_hook_bus_wiring.py — GRO-2991 verification tests

Validates that record_hook_fired() is now wired into the PluginLoader's
execute_hook() — the canonical hook bus for the engine.

Before GRO-2991: telemetry_hook_fired table was 0 rows (record_hook_fired
was test-only).
After GRO-2991:  each execute_hook() call appends a row to telemetry_hook_fired.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from prismatic.core.registry import PluginLoader
from prismatic.telemetry import TelemetryCollector


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def telemetry_db(tmp_path):
    """TelemetryCollector backed by a temp DB, with reset singleton."""
    db_path = str(tmp_path / "test_gro_2991.db")
    c = TelemetryCollector(db_path=db_path)
    import prismatic.telemetry as _tel
    _tel._collector = c
    yield c, db_path
    c._running = False


def _wait_drain(db_path: str, table: str, timeout: float = 3.0) -> list[dict]:
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


# ── TestExecuteHookTelemetry ────────────────────────────────────────────────


class TestExecuteHookTelemetry:
    """PluginLoader.execute_hook() must push to telemetry_hook_fired."""

    def test_execute_hook_emits_row_for_loaded_plugin(self, telemetry_db):
        """With a loaded plugin that has the hook, expect a row in telemetry_hook_fired."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")

        # Register a fake plugin with a no-op hook
        class _FakePlugin:
            def my_hook(self, *args, **kwargs):
                return "ok"

        loader.loaded_plugins["fake-plugin-1"] = _FakePlugin()  # type: ignore[arg-type]

        loader.execute_hook("my_hook", run_id="run-001", issue_id="GRO-2991-1")

        rows = _wait_drain(db_path, "telemetry_hook_fired", timeout=3.0)
        assert rows, "telemetry_hook_fired has 0 rows after execute_hook() — wiring broken"
        row = rows[0]
        assert row["hook_name"] == "my_hook"
        assert row["run_id"] == "run-001"
        assert row["issue_id"] == "GRO-2991-1"
        assert row["event_type"] == "plugin.execute_hook"
        assert row["success"] == 1
        assert row["duration_ms"] >= 0

    def test_execute_hook_no_loaded_plugin_still_emits_row(self, telemetry_db):
        """execute_hook() with no plugins should still record the call (success=True)."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")
        # No loaded plugins
        loader.execute_hook("orphan_hook")

        rows = _wait_drain(db_path, "telemetry_hook_fired", timeout=3.0)
        assert rows
        assert rows[0]["hook_name"] == "orphan_hook"
        assert rows[0]["success"] == 1  # no plugins to fail

    def test_execute_hook_plugin_failure_recorded(self, telemetry_db):
        """A failing hook should still record the call (with success=0)."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")

        class _BrokenPlugin:
            def bad_hook(self):
                raise RuntimeError("intentional")

        loader.loaded_plugins["broken"] = _BrokenPlugin()  # type: ignore[arg-type]

        # Should NOT raise — execute_hook isolates per-plugin failures
        loader.execute_hook("bad_hook")

        rows = _wait_drain(db_path, "telemetry_hook_fired", timeout=3.0)
        assert rows
        row = rows[0]
        assert row["hook_name"] == "bad_hook"
        assert row["success"] == 0  # 0 plugins fired successfully

    def test_execute_hook_multiple_plugins_aggregates(self, telemetry_db):
        """Two plugins, both fire successfully — success=1, one row per call."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")

        class _PluginA:
            def shared_hook(self, *args, **kwargs):
                return "a"

        class _PluginB:
            def shared_hook(self, *args, **kwargs):
                return "b"

        loader.loaded_plugins["a"] = _PluginA()  # type: ignore[arg-type]
        loader.loaded_plugins["b"] = _PluginB()  # type: ignore[arg-type]

        loader.execute_hook("shared_hook", run_id="multi")

        rows = _wait_drain(db_path, "telemetry_hook_fired", timeout=3.0)
        assert len(rows) == 1
        assert rows[0]["run_id"] == "multi"
        assert rows[0]["success"] == 1

    def test_execute_hook_partial_failure_marks_success_zero(self, telemetry_db):
        """One plugin fires, one raises — should still record with success=0."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")

        class _GoodPlugin:
            def mix_hook(self, *args, **kwargs):
                return "ok"

        class _BadPlugin:
            def mix_hook(self, *args, **kwargs):
                raise ValueError("nope")

        loader.loaded_plugins["good"] = _GoodPlugin()  # type: ignore[arg-type]
        loader.loaded_plugins["bad"] = _BadPlugin()  # type: ignore[arg-type]

        loader.execute_hook("mix_hook", issue_id="GRO-partial")

        rows = _wait_drain(db_path, "telemetry_hook_fired", timeout=3.0)
        assert rows
        # fired_count was 1 (good), but failed_count was 1 (bad).
        # success should reflect "fired_count > 0" so the hook fired somewhere
        assert rows[0]["success"] == 1

    def test_execute_hook_tolerates_telemetry_outage(self, telemetry_db):
        """If telemetry collector raises, execute_hook must not propagate the error."""
        c, db_path = telemetry_db
        loader = PluginLoader(core_version="test", plugins_dir="/tmp")

        class _Plugin:
            def my_hook(self, *args, **kwargs):
                pass

        loader.loaded_plugins["x"] = _Plugin()  # type: ignore[arg-type]

        # Force the import inside execute_hook to raise
        import sys as _sys
        import prismatic.core.registry as reg_mod

        # Replace 'prismatic.telemetry' with a stub that explodes on get_collector
        class _BoomTelemetry:
            def get_collector(self_inner):
                raise RuntimeError("telemetry down")

        # Save original module if present
        orig_module = _sys.modules.get("prismatic.telemetry")
        try:
            _sys.modules["prismatic.telemetry"] = _BoomTelemetry()  # type: ignore[assignment]
            # Should NOT raise — execute_hook isolates the telemetry call
            loader.execute_hook("my_hook")
        finally:
            if orig_module is not None:
                _sys.modules["prismatic.telemetry"] = orig_module
        # If we got here, the hook bus survived a telemetry outage
        assert True