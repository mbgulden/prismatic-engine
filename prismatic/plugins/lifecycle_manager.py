"""
prismatic/plugins/lifecycle_manager.py — Plugin lifecycle state machine.

Manages plugin state transitions through the lifecycle:
    STOPPED → STARTING → RUNNING → STOPPING → FAILED → PURGED

Integrates with SandboxPodManager for actual container orchestration and
maintains a persistent state registry (in-memory + SQLite).

Usage
-----
.. code-block:: python

    mgr = PluginLifecycleSandboxManager(
        sandbox_pod_manager=SandboxPodManager(),
        db_path="./prismatic_state/plugin_lifecycle.db"
    )
    mgr.start_plugin("my-plugin", {"image": "python:3.12", "cmd": ["python", "-m", "my_plugin"]})
    mgr.stop_plugin("my-plugin")
    mgr.purge_plugin("my-plugin")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sandbox_pod_manager import SandboxPodManager, PodManagerError
from ..telemetry import get_collector

logger = logging.getLogger("prismatic.plugins.lifecycle")


class PluginState(str, Enum):
    """Valid states in the plugin lifecycle state machine.

    Transition matrix:
        STOPPED  → STARTING
        STARTING → RUNNING | FAILED
        RUNNING  → STOPPING | FAILED
        STOPPING → STOPPED | FAILED
        FAILED   → STOPPED | PURGED
        PURGED   → (terminal — no transitions out)
    """

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    PURGED = "PURGED"


# ── Allowed transitions ────────────────────────────────────────────
_ALLOWED_TRANSITIONS: Dict[PluginState, set[PluginState]] = {
    PluginState.STOPPED: {PluginState.STARTING},
    PluginState.STARTING: {PluginState.RUNNING, PluginState.FAILED},
    PluginState.RUNNING: {PluginState.STOPPING, PluginState.FAILED},
    PluginState.STOPPING: {PluginState.STOPPED, PluginState.FAILED},
    PluginState.FAILED: {PluginState.STOPPED, PluginState.PURGED},
    PluginState.PURGED: set(),  # Terminal state
}


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


@dataclass
class PluginLifecycleRecord:
    """Persistent record of a plugin's lifecycle state."""

    name: str
    state: PluginState = PluginState.STOPPED
    container_id: str = ""
    runtime: str = ""
    started_at: float = 0.0
    last_error: str = ""
    updated_at: float = 0.0
    config_json: str = "{}"  # JSON-serialized plugin config

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class PluginLifecycleSandboxManager:
    """Manages plugin state transitions, sandbox pod lifecycle, and state persistence.

    This is the primary entry point for controlling plugin lifecycle within
    the Prismatic Engine dispatcher.

    Integration with the dispatcher happens via the ``on_init`` hook in
    ``PluginLoader`` — after all plugins are loaded, the lifecycle manager
    starts them in sandboxed pods.

    State is stored in both an in-memory dict (fast access) and a SQLite
    database (crash recovery).
    """

    def __init__(
        self,
        sandbox_pod_manager: SandboxPodManager | None = None,
        db_path: str | None = None,
    ) -> None:
        """
        Args:
            sandbox_pod_manager: Backend pod manager. Created with defaults if None.
            db_path: Path to SQLite database for state persistence.
                     Default: ``./prismatic_state/plugin_lifecycle.db``
        """
        self._pod_manager = sandbox_pod_manager or SandboxPodManager()
        self._db_path = db_path or os.path.join(
            os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
            "plugin_lifecycle.db",
        )
        self._plugins: Dict[str, PluginLifecycleRecord] = {}

        # Initialize SQLite
        self._init_db()

        # Load existing records from DB into memory
        self._load_from_db()

    # ── Database Setup ─────────────────────────────────────────────

    def _init_db(self) -> None:
        """Ensure the SQLite database and schema exist."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_states (
                    name TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'STOPPED',
                    container_id TEXT DEFAULT '',
                    runtime TEXT DEFAULT '',
                    started_at REAL DEFAULT 0.0,
                    last_error TEXT DEFAULT '',
                    updated_at REAL DEFAULT 0.0,
                    config_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _load_from_db(self) -> None:
        """Load all plugin state records from SQLite into memory."""
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute("SELECT * FROM plugin_states").fetchall()
            columns = [d[1] for d in conn.execute("PRAGMA table_info(plugin_states)").fetchall()]
            for row in rows:
                record = PluginLifecycleRecord(**dict(zip(columns, row)))
                # Ensure state is an enum, not a raw string from DB
                if isinstance(record.state, str):
                    record.state = PluginState(record.state)
                self._plugins[record.name] = record
        except Exception as exc:
            logger.warning("Failed to load plugin states from DB: %s", exc)
        finally:
            conn.close()

    def _persist_state(self, name: str) -> None:
        """Write a plugin's current state to SQLite."""
        record = self._plugins.get(name)
        if record is None:
            return
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO plugin_states
                    (name, state, container_id, runtime, started_at, last_error, updated_at, config_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.name,
                    record.state.value,
                    record.container_id,
                    record.runtime,
                    record.started_at,
                    record.last_error,
                    record.updated_at,
                    record.config_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ── State Machine ──────────────────────────────────────────────

    def _transition(
        self, name: str, to_state: PluginState, error_msg: str = ""
    ) -> PluginLifecycleRecord:
        """Validate and execute a state transition.

        Args:
            name: Plugin name.
            to_state: Target state.
            error_msg: Optional error message (set when transitioning to FAILED).

        Returns:
            Updated PluginLifecycleRecord.

        Raises:
            StateTransitionError: If the transition is invalid.
        """
        record = self._plugins.setdefault(
            name,
            PluginLifecycleRecord(name=name),
        )

        current = record.state
        allowed = _ALLOWED_TRANSITIONS.get(current, set())

        if to_state not in allowed:
            raise StateTransitionError(
                f"Invalid transition: {current.value} → {to_state.value} "
                f"for plugin '{name}'. Allowed from {current.value}: "
                f"{[s.value for s in allowed]}"
            )

        record.state = to_state
        record.updated_at = time.time()
        if error_msg:
            record.last_error = error_msg
        if to_state == PluginState.FAILED:
            record.last_error = error_msg or "Unknown failure"

        # ── Telemetry: record lifecycle event ─────────────────────────
        try:
            event_type_map = {
                PluginState.STARTING: "start",
                PluginState.RUNNING: "start",
                PluginState.STOPPED: "stop",
                PluginState.FAILED: "crash",
            }
            telem_event = event_type_map.get(to_state, "heartbeat")
            crash_count = 1 if to_state == PluginState.FAILED else 0
            start_count = 1 if current == PluginState.STARTING and to_state == PluginState.RUNNING else 0

            collector = get_collector()
            collector.record_plugin_event(
                plugin_name=name,
                event_type=telem_event,
                start_count=start_count,
                crash_count=crash_count,
                state=to_state.value,
                error_message=record.last_error if to_state == PluginState.FAILED else "",
            )
        except Exception:
            logger.warning("Failed to record telemetry for plugin '%s'", name, exc_info=True)
        # ── End telemetry ─────────────────────────────────────────────

        self._persist_state(name)
        logger.info(
            "Plugin '%s' state: %s → %s (err=%s)",
            name, current.value, to_state.value, error_msg or "-",
        )
        return record

    # ── Public Lifecycle Commands ──────────────────────────────────

    def start_plugin(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Start a plugin in a sandbox pod.

        State transition: STOPPED → STARTING → RUNNING (or FAILED).

        Args:
            name: Plugin name (must match plugin-manifest name).
            config: Pod configuration dict (image, cmd, env, ports, volumes).

        Returns:
            Dict with state, container_id, runtime.

        Raises:
            StateTransitionError: If plugin is not in STOPPED or FAILED state.
            PodManagerError: If sandbox pod fails to start.
        """
        record = self._plugins.get(name)

        # Allow restart from FAILED
        if record and record.state == PluginState.FAILED:
            # Clear error and transition to STOPPED first
            record.last_error = ""
            record.state = PluginState.STOPPED

        self._transition(name, PluginState.STARTING)

        try:
            # Store config before starting
            record = self._plugins[name]
            record.config_json = json.dumps(config, default=str)

            result = self._pod_manager.start_pod(name, config)
            record.container_id = result.get("container_id", "")
            record.runtime = result.get("runtime", self._pod_manager._runtime)
            record.started_at = time.time()

            self._transition(name, PluginState.RUNNING)
            return {
                "state": PluginState.RUNNING.value,
                "container_id": record.container_id,
                "runtime": record.runtime,
            }

        except PodManagerError as exc:
            self._transition(name, PluginState.FAILED, str(exc))
            raise

        except Exception as exc:
            error_msg = f"Unexpected error starting plugin: {exc}"
            self._transition(name, PluginState.FAILED, error_msg)
            raise PodManagerError(error_msg) from exc

    def stop_plugin(self, name: str, force: bool = False) -> Dict[str, Any]:
        """Stop a running plugin.

        State transition: RUNNING → STOPPING → STOPPED (or FAILED).

        Args:
            name: Plugin name.
            force: If True, force-kill the pod instead of graceful shutdown.

        Returns:
            Dict with final state.

        Raises:
            StateTransitionError: If plugin is not in RUNNING state.
            PodManagerError: If pod cannot be stopped.
        """
        record = self._plugins.get(name)
        if record is None:
            raise StateTransitionError(f"Plugin '{name}' not found")

        # Allow stop from FAILED (cleanup path)
        if record.state in (PluginState.FAILED, PluginState.STOPPING):
            self._transition(name, PluginState.STOPPED)
            return {"state": PluginState.STOPPED.value}

        self._transition(name, PluginState.STOPPING)

        try:
            result = self._pod_manager.stop_pod(name, force=force)
            self._transition(name, PluginState.STOPPED)
            return {"state": PluginState.STOPPED.value}

        except PodManagerError as exc:
            self._transition(name, PluginState.FAILED, str(exc))
            raise

        except Exception as exc:
            error_msg = f"Unexpected error stopping plugin: {exc}"
            self._transition(name, PluginState.FAILED, error_msg)
            raise PodManagerError(error_msg) from exc

    def restart_plugin(self, name: str) -> Dict[str, Any]:
        """Stop and restart a plugin.

        Shortcut for: stop_plugin(name) + start_plugin(name, last_config).

        Args:
            name: Plugin name.

        Returns:
            Dict from start_plugin on success.
        """
        record = self._plugins.get(name)
        if record is None:
            raise StateTransitionError(f"Plugin '{name}' not found")

        # Save config before stopping
        config = {}
        try:
            config = json.loads(record.config_json) if record.config_json else {}
        except json.JSONDecodeError:
            config = {}

        # Stop
        if record.state in (PluginState.RUNNING, PluginState.STARTING, PluginState.FAILED):
            try:
                self.stop_plugin(name, force=True)
            except (StateTransitionError, PodManagerError):
                pass

        # Start
        return self.start_plugin(name, config)

    def purge_plugin(self, name: str) -> Dict[str, Any]:
        """Completely remove a plugin (stop + delete sandbox + clean state).

        State transition: any → PURGED.

        Args:
            name: Plugin name.

        Returns:
            Dict confirming purge.
        """
        # Stop if running
        record = self._plugins.get(name)
        if record is not None and record.state in (
            PluginState.RUNNING, PluginState.STARTING, PluginState.STOPPING
        ):
            try:
                self._pod_manager.stop_pod(name, force=True)
            except PodManagerError:
                pass

        # Purge sandbox
        try:
            self._pod_manager.purge_pod(name)
        except PodManagerError:
            pass

        # Clear SQLite record
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("DELETE FROM plugin_states WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()

        # Remove from memory
        if name in self._plugins:
            del self._plugins[name]

        logger.info("Plugin '%s' purged", name)
        return {"state": PluginState.PURGED.value}

    # ── Query Commands ─────────────────────────────────────────────

    def get_plugin_status(self, name: str) -> Dict[str, Any]:
        """Return current state and metadata for a plugin.

        Args:
            name: Plugin name.

        Returns:
            Dict with plugin state info, or {"state": "NOT_FOUND"}.
        """
        record = self._plugins.get(name)
        if record is None:
            return {"state": "NOT_FOUND", "name": name}

        return record.to_dict()

    def list_plugins(self) -> List[Dict[str, Any]]:
        """Return state for all registered plugins."""
        return [r.to_dict() for r in self._plugins.values()]

    # ── Dispatcher Integration ─────────────────────────────────────

    def initialize_plugins(
        self,
        plugin_configs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Initialize and start all registered plugins in sandbox pods.

        Called by the dispatcher after PluginLoader completes scanning
        and loading.  Each plugin config is derived from the plugin's
        manifest.yaml plus any user-provided overrides.

        Args:
            plugin_configs: Dict mapping plugin name → pod config dict.
                           Keys should match loaded plugin names.

        Returns:
            Dict mapping plugin name → start result (state + container_id).
        """
        results: Dict[str, Dict[str, Any]] = {}
        for name, config in plugin_configs.items():
            try:
                result = self.start_plugin(name, config)
                results[name] = result
                logger.info("Plugin '%s' initialized successfully", name)
            except (StateTransitionError, PodManagerError) as exc:
                results[name] = {"state": "FAILED", "error": str(exc)}
                logger.error("Plugin '%s' initialization failed: %s", name, exc)

        return results

    def shutdown_all(self, force: bool = False) -> Dict[str, str]:
        """Gracefully stop all running plugins.

        Called on dispatcher shutdown.

        Args:
            force: If True, force-kill all plugins.

        Returns:
            Dict mapping plugin name → final state.
        """
        results: Dict[str, str] = {}
        for name in list(self._plugins.keys()):
            try:
                self.stop_plugin(name, force=force)
                results[name] = PluginState.STOPPED.value
            except (StateTransitionError, PodManagerError) as exc:
                results[name] = f"FAILED: {exc}"
                logger.error("Plugin '%s' shutdown failed: %s", name, exc)

        return results
