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
