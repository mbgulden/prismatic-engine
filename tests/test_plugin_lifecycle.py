"""
Tests for PluginLifecycleSandboxManager — state machine transitions,
sandbox pod integration, forced-stop recovery, and orphan cleanup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module under test
from prismatic.plugins.sandbox_pod_manager import (
    PodState,
    PodManagerError,
    SandboxPodManager,
)
from prismatic.plugins.lifecycle_manager import (
    PluginState,
    StateTransitionError,
    PluginLifecycleSandboxManager,
    PluginLifecycleRecord,
    _ALLOWED_TRANSITIONS,
)

# ── generic plugin lifecycle manager (Part A) test imports ──────────────
import importlib

import pytest

from prismatic.core.registry import (
    PluginLoader,
    get_default_plugin_loader,
    plugin_state_file,
    set_default_plugin_loader,
)
from prismatic.interface.plugin import PluginContext, PluginValidationError


class TestStateMachineTransitions(unittest.TestCase):
    """Verify that the state machine enforces valid transitions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pod_mgr = SandboxPodManager(state_dir=self.tmpdir)
        self.lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=self.pod_mgr,
            db_path=os.path.join(self.tmpdir, "test_lifecycle.db"),
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_state(self, name: str, state: PluginState) -> None:
        """Force-set a plugin's state for testing."""
        record = self.lifecycle._plugins.setdefault(
            name, PluginLifecycleRecord(name=name)
        )
        record.state = state

    def _get_state(self, name: str) -> PluginState:
        record = self.lifecycle._plugins.get(name)
        if record is None:
            return PluginState.STOPPED
        return record.state

    # ── Valid transitions ─────────────────────────────────────────

    def test_stopped_to_starting(self):
        """STOPPED → STARTING is valid."""
        self._set_state("p1", PluginState.STOPPED)
        try:
            self.lifecycle._transition("p1", PluginState.STARTING)
        except StateTransitionError:
            self.fail("STOPPED → STARTING should be valid")

    def test_starting_to_running(self):
        """STARTING → RUNNING is valid."""
        self._set_state("p1", PluginState.STARTING)
        try:
            self.lifecycle._transition("p1", PluginState.RUNNING)
        except StateTransitionError:
            self.fail("STARTING → RUNNING should be valid")

    def test_starting_to_failed(self):
        """STARTING → FAILED is valid."""
        self._set_state("p1", PluginState.STARTING)
        try:
            self.lifecycle._transition("p1", PluginState.FAILED, "start failure")
        except StateTransitionError:
            self.fail("STARTING → FAILED should be valid")

    def test_running_to_stopping(self):
        """RUNNING → STOPPING is valid."""
        self._set_state("p1", PluginState.RUNNING)
        try:
            self.lifecycle._transition("p1", PluginState.STOPPING)
        except StateTransitionError:
            self.fail("RUNNING → STOPPING should be valid")

    def test_running_to_failed(self):
        """RUNNING → FAILED is valid."""
        self._set_state("p1", PluginState.RUNNING)
        try:
            self.lifecycle._transition("p1", PluginState.FAILED, "crash")
        except StateTransitionError:
            self.fail("RUNNING → FAILED should be valid")

    def test_stopping_to_stopped(self):
        """STOPPING → STOPPED is valid."""
        self._set_state("p1", PluginState.STOPPING)
        try:
            self.lifecycle._transition("p1", PluginState.STOPPED)
        except StateTransitionError:
            self.fail("STOPPING → STOPPED should be valid")

    def test_stopping_to_failed(self):
        """STOPPING → FAILED is valid."""
        self._set_state("p1", PluginState.STOPPING)
        try:
            self.lifecycle._transition("p1", PluginState.FAILED, "stop error")
        except StateTransitionError:
            self.fail("STOPPING → FAILED should be valid")

    def test_failed_to_stopped(self):
        """FAILED → STOPPED is valid (reset for retry)."""
        self._set_state("p1", PluginState.FAILED)
        try:
            self.lifecycle._transition("p1", PluginState.STOPPED)
        except StateTransitionError:
            self.fail("FAILED → STOPPED should be valid")

    def test_failed_to_purged(self):
        """FAILED → PURGED is valid."""
        self._set_state("p1", PluginState.FAILED)
        try:
            self.lifecycle._transition("p1", PluginState.PURGED)
        except StateTransitionError:
            self.fail("FAILED → PURGED should be valid")

    # ── Invalid transitions ───────────────────────────────────────

    def test_stopped_to_running_invalid(self):
        """STOPPED → RUNNING is invalid (must go through STARTING)."""
        self._set_state("p1", PluginState.STOPPED)
        with self.assertRaises(StateTransitionError):
            self.lifecycle._transition("p1", PluginState.RUNNING)

    def test_running_to_starting_invalid(self):
        """RUNNING → STARTING is invalid."""
        self._set_state("p1", PluginState.RUNNING)
        with self.assertRaises(StateTransitionError):
            self.lifecycle._transition("p1", PluginState.STARTING)

    def test_purged_is_terminal(self):
        """PURGED has no valid outgoing transitions."""
        self._set_state("p1", PluginState.PURGED)
        for target in PluginState:
            if target == PluginState.PURGED:
                continue
            with self.assertRaises(StateTransitionError):
                self.lifecycle._transition("p1", target)

    def test_failed_to_starting_invalid(self):
        """FAILED → STARTING is invalid (must go through STOPPED first)."""
        self._set_state("p1", PluginState.FAILED)
        with self.assertRaises(StateTransitionError):
            self.lifecycle._transition("p1", PluginState.STARTING)

    # ── Start/stop lifecycle ──────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_full_start_stop_cycle(self, mock_detect):
        """Full lifecycle: STOPPED → STARTING → RUNNING → STOPPING → STOPPED."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "cycle.db"),
        )

        # Start
        result = lifecycle.start_plugin("test-plugin", {})
        self.assertEqual(result["state"], PluginState.RUNNING.value)
        self.assertIn("container_id", result)

        record = lifecycle.get_plugin_status("test-plugin")
        self.assertEqual(record["state"], PluginState.RUNNING.value)

        # Stop
        result = lifecycle.stop_plugin("test-plugin")
        self.assertEqual(result["state"], PluginState.STOPPED.value)

        record = lifecycle.get_plugin_status("test-plugin")
        self.assertEqual(record["state"], PluginState.STOPPED.value)

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_restart_plugin(self, mock_detect):
        """Restart stops and starts a plugin."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "restart.db"),
        )
        lifecycle.start_plugin("test-plugin", {"image": "python:3.12"})
        self.assertEqual(
            lifecycle.get_plugin_status("test-plugin")["state"],
            PluginState.RUNNING.value,
        )

        result = lifecycle.restart_plugin("test-plugin")
        self.assertEqual(result["state"], PluginState.RUNNING.value)

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_purge_plugin(self, mock_detect):
        """Purge removes plugin from tracking entirely."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "purge.db"),
        )
        lifecycle.start_plugin("test-plugin", {})
        result = lifecycle.purge_plugin("test-plugin")
        self.assertEqual(result["state"], PluginState.PURGED.value)

        status = lifecycle.get_plugin_status("test-plugin")
        self.assertEqual(status["state"], "NOT_FOUND")

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_double_start_raises_error(self, mock_detect):
        """Starting an already-running plugin raises an error."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "double.db"),
        )
        lifecycle.start_plugin("test-plugin", {})
        with self.assertRaises(StateTransitionError):
            lifecycle.start_plugin("test-plugin", {})

    # ── Forced-stop recovery ───────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_forced_stop(self, mock_detect):
        """Stop with force=True still reaches STOPPED."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "force.db"),
        )
        lifecycle.start_plugin("test-plugin", {})
        result = lifecycle.stop_plugin("test-plugin", force=True)
        self.assertEqual(result["state"], PluginState.STOPPED.value)

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_stop_from_failed(self, mock_detect):
        """Stopping a FAILED plugin transitions to STOPPED (cleanup path)."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "failstop.db"),
        )
        # Start then manually set to FAILED
        lifecycle.start_plugin("test-plugin", {})
        self.lifecycle = lifecycle
        self._set_state_in(lifecycle, "test-plugin", PluginState.FAILED)

        result = lifecycle.stop_plugin("test-plugin")
        self.assertEqual(result["state"], PluginState.STOPPED.value)

    def _set_state_in(self, lifecycle, name, state):
        """Helper to force-set state on a specific lifecycle instance."""
        record = lifecycle._plugins.setdefault(
            name, PluginLifecycleRecord(name=name)
        )
        record.state = state

    # ── SQLite persistence ─────────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_sqlite_persistence(self, mock_detect):
        """State changes are persisted to SQLite."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "persist.db"),
        )
        lifecycle.start_plugin("persist-test", {})

        # Verify in DB
        conn = sqlite3.connect(os.path.join(self.tmpdir, "persist.db"))
        try:
            row = conn.execute(
                "SELECT state FROM plugin_states WHERE name = ?",
                ("persist-test",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], PluginState.RUNNING.value)
        finally:
            conn.close()

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_recovery_from_sqlite(self, mock_detect):
        """State survives lifecycle manager re-creation."""
        db_path = os.path.join(self.tmpdir, "recovery.db")
        pod_mgr = SandboxPodManager(state_dir=self.tmpdir)

        # First lifecycle manager: start a plugin
        lc1 = PluginLifecycleSandboxManager(
            sandbox_pod_manager=pod_mgr,
            db_path=db_path,
        )
        lc1.start_plugin("recovery-test", {})
        self.assertEqual(
            lc1.get_plugin_status("recovery-test")["state"],
            PluginState.RUNNING.value,
        )

        # Second lifecycle manager: should load state from DB
        lc2 = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=db_path,
        )
        status = lc2.get_plugin_status("recovery-test")
        self.assertEqual(status["state"], PluginState.RUNNING.value)
        self.assertEqual(status["name"], "recovery-test")

    # ── Orphan cleanup ─────────────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_purge_cleans_db_and_memory(self, mock_detect):
        """Purge removes both in-memory and SQLite records."""
        db_path = os.path.join(self.tmpdir, "orphan.db")
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=db_path,
        )
        lifecycle.start_plugin("orphan-test", {})
        lifecycle.purge_plugin("orphan-test")

        # Memory: gone
        status = lifecycle.get_plugin_status("orphan-test")
        self.assertEqual(status["state"], "NOT_FOUND")

        # SQLite: gone
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM plugin_states WHERE name = ?",
                ("orphan-test",),
            ).fetchone()
            self.assertEqual(row[0], 0)
        finally:
            conn.close()

    # ── Initialize all plugins ─────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_initialize_plugins_all_start(self, mock_detect):
        """initialize_plugins starts all provided plugin configs."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "init.db"),
        )
        configs = {
            "plugin-a": {"image": "python:3.12"},
            "plugin-b": {"image": "python:3.11"},
        }
        results = lifecycle.initialize_plugins(configs)
        self.assertIn("plugin-a", results)
        self.assertIn("plugin-b", results)
        self.assertEqual(results["plugin-a"]["state"], PluginState.RUNNING.value)
        self.assertEqual(results["plugin-b"]["state"], PluginState.RUNNING.value)

    # ── List plugins ───────────────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_list_plugins(self, mock_detect):
        """list_plugins returns all registered plugin states."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "list.db"),
        )
        lifecycle.start_plugin("list-test-a", {})
        lifecycle.start_plugin("list-test-b", {})

        plugins = lifecycle.list_plugins()
        names = [p["name"] for p in plugins]
        self.assertIn("list-test-a", names)
        self.assertIn("list-test-b", names)

    # ── Shutdown all ───────────────────────────────────────────────

    @patch.object(SandboxPodManager, "_detect_runtime", return_value="none")
    def test_shutdown_all_stops_plugins(self, mock_detect):
        """shutdown_all stops every running plugin."""
        lifecycle = PluginLifecycleSandboxManager(
            sandbox_pod_manager=SandboxPodManager(state_dir=self.tmpdir),
            db_path=os.path.join(self.tmpdir, "shutdown.db"),
        )
        lifecycle.start_plugin("shutdown-a", {})
        lifecycle.start_plugin("shutdown-b", {})

        results = lifecycle.shutdown_all()
        self.assertEqual(results["shutdown-a"], PluginState.STOPPED.value)
        self.assertEqual(results["shutdown-b"], PluginState.STOPPED.value)


class TestSandboxPodManager(unittest.TestCase):
    """Tests for the underlying SandboxPodManager (simulated mode)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with patch.object(SandboxPodManager, "_detect_runtime", return_value="none"):
            self.pod_mgr = SandboxPodManager(state_dir=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_simulated_pod(self):
        """Simulated pod start returns RUNNING state."""
        result = self.pod_mgr.start_pod("sim-test", {"image": "python:3.12"})
        self.assertEqual(result["state"], PodState.RUNNING.value)
        self.assertIn("container_id", result)

    def test_stop_simulated_pod(self):
        """Simulated pod stop returns STOPPED state."""
        self.pod_mgr.start_pod("sim-test", {})
        result = self.pod_mgr.stop_pod("sim-test")
        self.assertEqual(result["state"], PodState.STOPPED.value)

    def test_purge_simulated_pod(self):
        """Simulated pod purge returns PURGED state."""
        self.pod_mgr.start_pod("sim-test", {})
        result = self.pod_mgr.purge_pod("sim-test")
        self.assertEqual(result["state"], PodState.PURGED.value)

    def test_get_pod_status_not_found(self):
        """get_pod_status returns NOT_FOUND for unknown pod."""
        result = self.pod_mgr.get_pod_status("nonexistent")
        self.assertEqual(result["state"], "NOT_FOUND")

    def test_health_check_on_simulated(self):
        """Simulated pod health check returns True."""
        self.pod_mgr.start_pod("sim-test", {})
        self.assertTrue(self.pod_mgr.health_check("sim-test"))

    def test_list_pods(self):
        """list_pods returns all started pods."""
        self.pod_mgr.start_pod("pod-a", {})
        self.pod_mgr.start_pod("pod-b", {})
        pods = self.pod_mgr.list_pods()
        self.assertEqual(len(pods), 2)

    def test_double_start_raises(self):
        """Starting an already-running pod raises PodManagerError."""
        self.pod_mgr.start_pod("double-test", {})
        with self.assertRaises(PodManagerError):
            self.pod_mgr.start_pod("double-test", {})

    def test_stop_nonexistent_pod(self):
        """Stopping a non-existent pod raises PodManagerError."""
        with self.assertRaises(PodManagerError):
            self.pod_mgr.stop_pod("nonexistent")


class TestStateTransitionMatrix(unittest.TestCase):
    """Verify that _ALLOWED_TRANSITIONS covers every state with sane rules."""

    def test_every_state_has_entry(self):
        """Every PluginState appears as a key in _ALLOWED_TRANSITIONS."""
        for state in PluginState:
            self.assertIn(state, _ALLOWED_TRANSITIONS, f"Missing entry for {state}")

    def test_no_self_transitions(self):
        """No state should allow self-transition."""
        for state, targets in _ALLOWED_TRANSITIONS.items():
            self.assertNotIn(
                state, targets,
                f"{state.value} allows self-transition",
            )

    def test_transitions_are_symmetric_inverse_check(self):
        """If A→B is valid, B→A should be validated separately.

        Not a rule — just verifying known patterns:
        - STOPPED ↔ STARTING (only if going through full cycle)
        - FAILED → STOPPED is valid (reset)
        """
        # STOPPED can reach STARTING but not vice versa without going through RUNNING→STOPPING
        self.assertIn(PluginState.STARTING, _ALLOWED_TRANSITIONS[PluginState.STOPPED])
        self.assertIn(PluginState.STOPPED, _ALLOWED_TRANSITIONS[PluginState.STOPPING])

    def test_purged_is_truly_terminal(self):
        """PURGED has an empty allowed set."""
        self.assertEqual(_ALLOWED_TRANSITIONS[PluginState.PURGED], set())


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────────────────────────────────────────────────
# Generic plugin lifecycle manager — PluginLoader.attach / enable /
# disable / unload with suspend/resume state (Part A).
#
# These tests exercise the contract in docs/plugin-lifecycle.md with a
# tiny fixture plugin written to a tmp plugins dir. They are appended to
# this file (rather than replacing it) so the sandbox-lifecycle tests
# above keep running unchanged.
# ─────────────────────────────────────────────────────────────────────

_LIFECYCLE_FIXTURE_SEQ = 0


def _lifecycle_fixture_plugin(tmp_path, *, name, extra_manifest=""):
    """Write a tiny fixture plugin dir; return its paths and identifiers.

    Each call uses a unique package/module name so import caching can
    never leak plugin classes across tests.
    """
    global _LIFECYCLE_FIXTURE_SEQ
    _LIFECYCLE_FIXTURE_SEQ += 1
    seq = _LIFECYCLE_FIXTURE_SEQ
    package = f"lifecycle_fixture_{seq}"
    class_name = f"LifecycleFixture{seq}Plugin"
    tool_name = f"{name}-tool"

    plugin_py = (
        "from __future__ import annotations\n"
        "\n"
        "from typing import Any, Dict, List\n"
        "\n"
        "from prismatic.interface.plugin import PluginContext, PrismaticPlugin\n"
        "\n"
        "\n"
        f"class {class_name}(PrismaticPlugin):\n"
        "    hook_calls: List = []\n"
        "    resumed_with: List = []\n"
        "    raise_on_suspend: bool = False\n"
        "\n"
        "    def on_init(self, context: PluginContext) -> None:\n"
        "        self.context = context\n"
        "\n"
        "    def register_tools(self) -> List[Dict[str, Any]]:\n"
        f"        return [{{\"name\": \"{tool_name}\","
        ' "description": "lifecycle fixture tool"}]\n'
        "\n"
        "    def on_pre_pipeline(self, pipeline_id: str,\n"
        "                        context: Dict[str, Any]) -> None:\n"
        '        type(self).hook_calls.append(("on_pre_pipeline", pipeline_id))\n'
        "\n"
        "    def on_suspend(self) -> Dict[str, Any]:\n"
        "        if type(self).raise_on_suspend:\n"
        '            raise RuntimeError("boom in on_suspend")\n'
        '        return {"marker": "suspended", "count": 7}\n'
        "\n"
        "    def on_resume(self, state: Dict[str, Any]) -> None:\n"
        "        type(self).resumed_with.append(dict(state))\n"
    )

    manifest = (
        'schema_version: "1.0.0"\n'
        f'name: "{name}"\n'
        'version: "0.3.1"\n'
        'description: "lifecycle fixture plugin"\n'
        f'entry_point: "{package}.plugin:{class_name}"\n'
        'core_version_constraint: ">=0.1.0, <2.0.0"\n' + extra_manifest
    )

    plugin_dir = tmp_path / "plugins" / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.py").write_text(plugin_py, encoding="utf-8")
    manifest_path = plugin_dir / "plugin-manifest.yaml"
    manifest_path.write_text(manifest, encoding="utf-8")
    return {
        "name": name,
        "package": package,
        "class_name": class_name,
        "tool_name": tool_name,
        "manifest_path": manifest_path,
        "plugin_dir": plugin_dir,
    }


def _lifecycle_plugin_class(fixture):
    """Import the fixture plugin class and reset its class-level spies."""
    module = importlib.import_module(f"{fixture['package']}.plugin")
    cls = getattr(module, fixture["class_name"])
    cls.hook_calls.clear()
    cls.resumed_with.clear()
    cls.raise_on_suspend = False
    return cls


def _lifecycle_context(tmp_path, config=None):
    return PluginContext(
        config=dict(config or {}),
        db_connection=None,
        state_dir=str(tmp_path),
    )


def _lifecycle_loader(tmp_path):
    return PluginLoader(
        core_version="0.2.0", plugins_dir=str(tmp_path / "plugins")
    )


@pytest.fixture(autouse=True)
def _reset_default_plugin_loader():
    """PluginLoader registers itself as the process default on init;
    reset it after every test so loader instances never leak between
    tests in this file."""
    yield
    set_default_plugin_loader(None)


class TestGenericPluginLifecycle:
    """Full attach → hook → disable → enable → unload → re-attach cycle."""

    def test_full_lifecycle_cycle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        fx = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-demo")
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)

        # attach → returns the plugin name; loaded and enabled.
        assert loader.attach(fx["manifest_path"], context=ctx) == "lifecycle-demo"
        cls = _lifecycle_plugin_class(fx)
        assert loader.plugin_status("lifecycle-demo") == {
            "enabled": True,
            "loaded": True,
            "state_preserved": False,
            "version": "0.3.1",
        }

        # Hook fires while the plugin is enabled.
        loader.execute_hook("on_pre_pipeline", "pipe-1", {})
        assert cls.hook_calls == [("on_pre_pipeline", "pipe-1")]

        # disable → hook stops firing; state.json written with the
        # suspend payload.
        payload = loader.disable("lifecycle-demo")
        assert payload["version"] == 1
        assert payload["state"] == {"marker": "suspended", "count": 7}
        assert payload["saved_at"]
        loader.execute_hook("on_pre_pipeline", "pipe-2", {})
        assert cls.hook_calls == [("on_pre_pipeline", "pipe-1")]

        state_path = plugin_state_file("lifecycle-demo")
        assert state_path.exists()
        on_disk = json.loads(state_path.read_text(encoding="utf-8"))
        assert on_disk["version"] == 1
        assert on_disk["state"] == {"marker": "suspended", "count": 7}
        assert on_disk["saved_at"] == payload["saved_at"]

        status = loader.plugin_status("lifecycle-demo")
        assert status["enabled"] is False
        assert status["loaded"] is True
        assert status["state_preserved"] is True

        # enable → on_resume receives the exact preserved state; the
        # hook bus dispatches to the plugin again.
        loader.enable("lifecycle-demo")
        assert cls.resumed_with[-1] == {"marker": "suspended", "count": 7}
        loader.execute_hook("on_pre_pipeline", "pipe-3", {})
        assert cls.hook_calls[-1] == ("on_pre_pipeline", "pipe-3")
        assert loader.plugin_status("lifecycle-demo")["enabled"] is True

        # unload → instance dropped from loaded_plugins; state file stays.
        loader.unload("lifecycle-demo")
        assert "lifecycle-demo" not in loader.loaded_plugins
        assert state_path.exists()
        status = loader.plugin_status("lifecycle-demo")
        assert status["loaded"] is False
        assert status["state_preserved"] is True
        assert status["enabled"] is False

        # attach again → instance back, tools NOT duplicated, state
        # intact; enable() resumes with the original suspend payload.
        loader.attach(fx["manifest_path"], context=ctx)
        assert "lifecycle-demo" in loader.loaded_plugins
        assert (
            sum(1 for t in loader.registered_tools if t["name"] == fx["tool_name"])
            == 1
        )
        loader.enable("lifecycle-demo")
        assert cls.resumed_with[-1] == {"marker": "suspended", "count": 7}

    def test_config_schema_valid_config_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        extra = (
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    retries:\n"
            "      type: integer\n"
            "      minimum: 0\n"
            "    mode:\n"
            "      type: string\n"
            "      enum: [fast, slow]\n"
            "  required: [retries]\n"
        )
        fx = _lifecycle_fixture_plugin(
            tmp_path, name="lifecycle-cfg", extra_manifest=extra
        )
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        config = {"retries": 3, "mode": "fast"}
        assert (
            loader.attach(fx["manifest_path"], config=config, context=ctx)
            == "lifecycle-cfg"
        )
        assert loader.plugin_configs["lifecycle-cfg"] == config
        # The plugin sees its validated config under
        # context.config["plugin_configs"][name].
        plugin = loader.loaded_plugins["lifecycle-cfg"]
        assert plugin.context.config["plugin_configs"]["lifecycle-cfg"] == config

    def test_config_schema_invalid_config_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        extra = (
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    retries:\n"
            "      type: integer\n"
            "      minimum: 0\n"
            "  required: [retries]\n"
        )
        fx = _lifecycle_fixture_plugin(
            tmp_path, name="lifecycle-badcfg", extra_manifest=extra
        )
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        with pytest.raises(PluginValidationError):
            loader.attach(
                fx["manifest_path"], config={"retries": -1}, context=ctx
            )
        assert "lifecycle-badcfg" not in loader.loaded_plugins

    def test_config_schema_required_config_missing_raises(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        extra = (
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    retries:\n"
            "      type: integer\n"
            "  required: [retries]\n"
        )
        fx = _lifecycle_fixture_plugin(
            tmp_path, name="lifecycle-reqcfg", extra_manifest=extra
        )
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        with pytest.raises(PluginValidationError):
            loader.attach(fx["manifest_path"], config=None, context=ctx)
        assert "lifecycle-reqcfg" not in loader.loaded_plugins

    def test_scan_uses_plugin_configs_from_context(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        extra = (
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    retries:\n"
            "      type: integer\n"
            "  required: [retries]\n"
        )
        fx = _lifecycle_fixture_plugin(
            tmp_path, name="lifecycle-scan-cfg", extra_manifest=extra
        )
        ctx = _lifecycle_context(
            tmp_path,
            config={"plugin_configs": {"lifecycle-scan-cfg": {"retries": 1}}},
        )
        loader = _lifecycle_loader(tmp_path)
        loader.scan_and_load_plugins(ctx)
        assert "lifecycle-scan-cfg" in loader.loaded_plugins
        assert loader.plugin_configs["lifecycle-scan-cfg"] == {"retries": 1}

    def test_auto_enable_false_starts_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        fx = _lifecycle_fixture_plugin(
            tmp_path,
            name="lifecycle-quiet",
            extra_manifest="auto_enable: false\n",
        )
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        loader.scan_and_load_plugins(ctx)
        cls = _lifecycle_plugin_class(fx)

        # Registered (instance loaded) but DISABLED at scan time.
        assert "lifecycle-quiet" in loader.loaded_plugins
        status = loader.plugin_status("lifecycle-quiet")
        assert status["enabled"] is False
        assert status["loaded"] is True

        # Hook bus skips it until enable().
        loader.execute_hook("on_pre_pipeline", "pipe-1", {})
        assert cls.hook_calls == []

        loader.enable("lifecycle-quiet")
        # No state file existed → on_resume got {}.
        assert cls.resumed_with[-1] == {}
        loader.execute_hook("on_pre_pipeline", "pipe-2", {})
        assert cls.hook_calls == [("on_pre_pipeline", "pipe-2")]

    def test_enable_reloads_after_unload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        fx = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-reload")
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        loader.attach(fx["manifest_path"], context=ctx)
        cls = _lifecycle_plugin_class(fx)
        first_instance = loader.loaded_plugins["lifecycle-reload"]

        loader.unload("lifecycle-reload")
        assert "lifecycle-reload" not in loader.loaded_plugins

        # enable() re-loads the dropped instance (on_init fires again)
        # and resumes with the preserved suspend state.
        loader.enable("lifecycle-reload")
        assert "lifecycle-reload" in loader.loaded_plugins
        assert loader.loaded_plugins["lifecycle-reload"] is not first_instance
        assert cls.resumed_with[-1] == {"marker": "suspended", "count": 7}
        assert loader.plugin_status("lifecycle-reload")["enabled"] is True

    def test_disable_isolates_on_suspend_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        fx = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-flaky")
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        loader.attach(fx["manifest_path"], context=ctx)
        cls = _lifecycle_plugin_class(fx)

        cls.raise_on_suspend = True
        try:
            payload = loader.disable("lifecycle-flaky")
        finally:
            cls.raise_on_suspend = False

        # A crashing on_suspend never breaks disable(): empty state is
        # preserved and the plugin is still marked disabled.
        assert payload["state"] == {}
        assert plugin_state_file("lifecycle-flaky").exists()
        assert loader.plugin_status("lifecycle-flaky")["enabled"] is False

    def test_unknown_plugin_operations_raise(self, tmp_path):
        loader = _lifecycle_loader(tmp_path)
        with pytest.raises(PluginValidationError):
            loader.enable("no-such-plugin")
        with pytest.raises(PluginValidationError):
            loader.disable("no-such-plugin")
        with pytest.raises(PluginValidationError):
            loader.unload("no-such-plugin")
        assert loader.plugin_status("no-such-plugin") == {
            "enabled": False,
            "loaded": False,
            "state_preserved": False,
            "version": "",
        }

    def test_all_plugin_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        fx1 = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-a")
        fx2 = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-b")
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        loader.attach(fx1["manifest_path"], context=ctx)
        loader.attach(fx2["manifest_path"], context=ctx)
        loader.disable("lifecycle-b")

        statuses = loader.all_plugin_status()
        assert set(statuses) == {"lifecycle-a", "lifecycle-b"}
        assert statuses["lifecycle-a"] == {
            "enabled": True,
            "loaded": True,
            "state_preserved": False,
            "version": "0.3.1",
        }
        assert statuses["lifecycle-b"]["enabled"] is False
        assert statuses["lifecycle-b"]["state_preserved"] is True


class TestPluginLifecycleDashboard:
    """Dashboard surfaces expose per-plugin enabled + state_preserved."""

    def test_lifecycle_endpoint_merges_loader_status(
        self, tmp_path, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from prismatic.gateway import server as gateway_server

        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PRISMATIC_PLUGINS_DIR", str(tmp_path / "plugins"))
        fx = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-dash")
        ctx = _lifecycle_context(tmp_path)
        loader = _lifecycle_loader(tmp_path)
        loader.attach(fx["manifest_path"], context=ctx)
        loader.disable("lifecycle-dash")

        client = TestClient(gateway_server.app)
        resp = client.get("/api/plugins/lifecycle")
        assert resp.status_code == 200
        item = next(
            p for p in resp.json()["plugins"] if p["name"] == "lifecycle-dash"
        )
        assert item["enabled"] is False
        assert item["loaded"] is True
        assert item["state_preserved"] is True
        assert item["version"] == "0.3.1"
        assert item["lifecycle"]["enabled"] is False

        # Governance items carry the same operator-visible fields, with
        # all pre-existing fields intact.
        gov = client.get("/api/plugins/governance")
        assert gov.status_code == 200
        gitem = next(
            p for p in gov.json()["plugins"] if p["name"] == "lifecycle-dash"
        )
        assert gitem["enabled"] is False
        assert gitem["state_preserved"] is True
        assert "governance" in gitem
        assert "status" in gitem

    def test_lifecycle_endpoint_without_loader_uses_disk_fallback(
        self, tmp_path, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from prismatic.gateway import server as gateway_server

        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PRISMATIC_PLUGINS_DIR", str(tmp_path / "plugins"))
        fx = _lifecycle_fixture_plugin(tmp_path, name="lifecycle-disk")
        # Write a state file directly — no loader running in this process.
        state_path = plugin_state_file("lifecycle-disk")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"version": 1, "saved_at": "t", "state": {}}),
            encoding="utf-8",
        )
        assert get_default_plugin_loader() is None

        client = TestClient(gateway_server.app)
        resp = client.get("/api/plugins/lifecycle")
        assert resp.status_code == 200
        item = next(
            p for p in resp.json()["plugins"] if p["name"] == "lifecycle-disk"
        )
        assert item["state_preserved"] is True
        assert item["loaded"] is False
