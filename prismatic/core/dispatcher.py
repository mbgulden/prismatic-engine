"""
Polling event loop and task router — plugin-aware dispatcher.

This is the **new** plugin-aware dispatcher that integrates with
``PluginLoader``.  For backward compatibility the legacy dispatcher in
``prismatic/dispatcher.py`` is kept alongside this one; the plugin
architecture is being phased in incrementally.

Architecture (spec §5–6)
------------------------
1. On start-up the dispatcher loads all plugins via ``PluginLoader``.
2. The event loop polls Linear for actionable issues.
3. Before spawning an agent worker, the dispatcher fires the
   ``before_task_execution`` hook on every loaded plugin.
4. After the worker exits, the dispatcher fires ``after_task_execution``.
5. State transitions on Linear tickets trigger ``on_state_transition``.

NOTE — Stub (GRO-1507 phase-1)
   This file is a structural placeholder.  The actual dispatching logic
   currently lives in ``prismatic/dispatcher.py``.  The plugin-aware
   path will be wired in a follow-up task.
"""

from __future__ import annotations

import logging
from prismatic.core.registry import PluginLoader

logger = logging.getLogger("prismatic.core.dispatcher")


class Dispatcher:
    """
    Plugin-aware dispatcher — placeholder.

    When fully wired this replaces the polling loop in
    ``prismatic/dispatcher.py`` with one that integrates plugin
    lifecycle hooks.
    """

    def __init__(self, plugin_loader: PluginLoader) -> None:
        self._loader = plugin_loader

    def run(self) -> None:
        """Start the polling event loop (NOT YET IMPLEMENTED)."""
        logger.warning(
            "prismatic.core.dispatcher is a structural placeholder. "
            "The active dispatcher is prismatic/dispatcher.py."
        )
