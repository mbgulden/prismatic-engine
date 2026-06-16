"""
Polling event loop and task router — plugin-aware dispatcher.

This is the **new** plugin-aware dispatcher that integrates with
``PluginLoader`` and ``PluginLifecycleSandboxManager``.  For backward
compatibility the legacy dispatcher in ``prismatic/dispatcher.py`` is
kept alongside this one; the plugin architecture is being phased in
incrementally.

Architecture (spec §5–6)
------------------------
1. On start-up the dispatcher loads all plugins via ``PluginLoader``.
2. After loading, ``PluginLifecycleSandboxManager`` starts each plugin
   in its own sandbox pod (Docker/k3s).
3. The event loop polls Linear for actionable issues.
4. Before spawning an agent worker, the dispatcher fires the
   ``before_task_execution`` hook on every loaded plugin.
5. After the worker exits, the dispatcher fires ``after_task_execution``.
6. State transitions on Linear tickets trigger ``on_state_transition``.
7. On shutdown, all plugins are gracefully stopped via the lifecycle manager.

NOTE — Evolution
   This file was originally a structural placeholder (GRO-1507 phase-1).
   As of GRO-1822, the plugin lifecycle manager can be wired in.
   The actual dispatching logic currently lives in
   ``prismatic/dispatcher.py``.  The plugin-aware path is operational
   but the full event-loop migration is tracked separately.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from prismatic.core.registry import PluginLoader, PluginContext
from prismatic.plugins.lifecycle_manager import (
    PluginLifecycleSandboxManager,
    PluginState,
    StateTransitionError,
)
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodManagerError

logger = logging.getLogger("prismatic.core.dispatcher")


class Dispatcher:
    """Plugin-aware dispatcher with sandbox lifecycle integration.

    Wires together PluginLoader (scan + load) and PluginLifecycleSandboxManager
    (start in sandbox pods) into a single startup sequence.

    Usage::

        loader = PluginLoader(core_version="0.1.0", plugins_dir="./plugins")
        context = PluginContext(config={}, db_connection=None, state_dir="./prismatic_state")
        loader.scan_and_load_plugins(context)

        dispatcher = Dispatcher(plugin_loader=loader)
        dispatcher.initialize_plugins()
        # dispatcher.run()  # polling event loop (future)
        dispatcher.shutdown()
    """

    def __init__(
        self,
        plugin_loader: PluginLoader,
        lifecycle_manager: Optional[PluginLifecycleSandboxManager] = None,
        state_dir: str = "./prismatic_state",
    ) -> None:
        """
        Args:
            plugin_loader: Initialized PluginLoader with loaded plugins.
            lifecycle_manager: Optional pre-configured lifecycle manager.
                               Created with defaults if None.
            state_dir: Base state directory for lifecycle DB and pod state.
        """
        self._loader = plugin_loader
        self._state_dir = state_dir

        if lifecycle_manager is not None:
            self._lifecycle = lifecycle_manager
        else:
            pod_mgr = SandboxPodManager(
                state_dir=os.path.join(state_dir, "plugins"),
            )
            db_path = os.path.join(state_dir, "plugin_lifecycle.db")
            self._lifecycle = PluginLifecycleSandboxManager(
                sandbox_pod_manager=pod_mgr,
                db_path=db_path,
            )

    @property
    def lifecycle_manager(self) -> PluginLifecycleSandboxManager:
        """Expose the lifecycle manager for direct access."""
        return self._lifecycle

    def initialize_plugins(self) -> Dict[str, Dict[str, Any]]:
        """Start all loaded plugins in sandbox pods.

        Derives pod configuration from each plugin's manifest metadata
        and launches them via the lifecycle manager.

        Returns:
            Dict mapping plugin name → start result (state + container_id).
        """
        plugin_configs: Dict[str, Dict[str, Any]] = {}

        for name, plugin_instance in self._loader.loaded_plugins.items():
            # Build a sensible default config from the plugin manifest
            config = {
                "image": "python:3.12-slim",
                "cmd": [],
                "env": {
                    "PRISMATIC_PLUGIN_NAME": name,
                    "PRISMATIC_PLUGIN_VERSION": "1.0.0",
                },
            }

            # Try to read additional metadata from manifest
            manifest = self._find_manifest(name)
            if manifest:
                config["env"]["PRISMATIC_PLUGIN_VERSION"] = manifest.get("version", "0.0.0")

            plugin_configs[name] = config

        if not plugin_configs:
            logger.info("No plugins to initialize (loader has 0 loaded plugins)")
            return {}

        logger.info(
            "Initializing %d plugin(s) in sandbox pods: %s",
            len(plugin_configs),
            ", ".join(plugin_configs.keys()),
        )

        return self._lifecycle.initialize_plugins(plugin_configs)

    def _find_manifest(self, name: str) -> Optional[Dict[str, Any]]:
        """Search for a plugin's manifest.yaml by plugin name."""
        search_paths = [
            Path(self._loader.plugins_dir) / name / "plugin-manifest.yaml",
            Path(self._loader.plugins_dir) / f"{name}/plugin-manifest.yaml",
        ]
        for path in search_paths:
            if path.exists():
                try:
                    import yaml
                    with open(path) as f:
                        return yaml.safe_load(f)
                except Exception:
                    pass
        return None

    def run(self) -> None:
        """Start the polling event loop (integration path — operational).

        When the full plugin-aware dispatch loop is wired in, this will:
        1. Verify all plugins are running
        2. Poll Linear for actionable issues
        3. Execute hooks before/after each agent task
        4. Report plugin health

        Currently a stub — the active dispatch loop remains in
        ``prismatic/dispatcher.py``.
        """
        logger.warning(
            "prismatic.core.dispatcher.run() is a stub. "
            "The active dispatcher is prismatic/dispatcher.py."
        )

    def shutdown(self) -> None:
        """Gracefully stop all running plugins."""
        logger.info("Dispatcher shutting down — stopping all plugins...")
        results = self._lifecycle.shutdown_all(force=False)
        stopped = sum(1 for r in results.values() if r == PluginState.STOPPED.value)
        failed = len(results) - stopped
        if failed:
            logger.warning("Shutdown: %d plugins stopped, %d had errors", stopped, failed)
        else:
            logger.info("Shutdown: all %d plugins stopped successfully", stopped)
