"""
prismatic/core/dispatcher.py — Polling event loop and task router — plugin-aware dispatcher.

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

GRO-1571-C (Phase 1.3)
-----------------------
6. Pipeline trigger events from external repos are accepted via
   ``handle_trigger_event()`` and forwarded to the target calculator,
   which determines affected downstream repos and dispatches
   validation runs.

NOTE — Stub (GRO-1507 phase-1)
   This file is a structural placeholder.  The actual dispatching logic
   currently lives in ``prismatic/dispatcher.py``.  The plugin-aware
   path will be wired in a follow-up task.
"""

from __future__ import annotations

import logging
from typing import Any

from prismatic.core.registry import PluginLoader
from prismatic.core.events import PipelineTriggerEvent
from prismatic.core.targets import (
    AffectedTarget,
    calculate_affected_targets,
)

logger = logging.getLogger("prismatic.core.dispatcher")


class Dispatcher:
    """
    Plugin-aware dispatcher — placeholder with pipeline trigger interface.

    When fully wired this replaces the polling loop in
    ``prismatic/dispatcher.py`` with one that integrates plugin
    lifecycle hooks and cross-project pipeline trigger events.
    """

    def __init__(self, plugin_loader: PluginLoader) -> None:
        self._loader = plugin_loader
        self._pipelines_config: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Pipeline trigger interface (GRO-1571-C)
    # ------------------------------------------------------------------

    def load_pipelines_config(self, config: dict[str, Any]) -> None:
        """
        Load or reload the pipeline definitions used by the trigger
        interface.

        Parameters
        ----------
        config:
            Parsed ``pipelines.yaml`` content (a dict with a top-level
            ``"pipelines"`` key containing pipeline definitions).
        """
        self._pipelines_config = config
        pipeline_count = len(config.get("pipelines", {}))
        logger.info(
            "Loaded %d pipeline definitions into dispatcher.",
            pipeline_count,
        )

    def handle_trigger_event(
        self,
        event: PipelineTriggerEvent,
    ) -> list[AffectedTarget]:
        """
        Accept a pipeline trigger event and calculate affected downstream
        targets.

        This is the primary entry-point for cross-project pipeline
        triggers.  Steps:

        1. Pass *event* plus the current pipeline config to
           :func:`~prismatic.core.targets.calculate_affected_targets`.
        2. Log the results.
        3. Return the list of downstream targets for the caller to
           enqueue / dispatch.

        Parameters
        ----------
        event:
            The source trigger event (repository, branch, type, …).

        Returns
        -------
        list[AffectedTarget]
            Zero or more downstream targets that should run validation
            or follow-up tasks.
        """
        targets = calculate_affected_targets(
            event,
            self._pipelines_config,
        )

        if targets:
            logger.info(
                "Trigger event %s (%s/%s) → %d downstream target(s): %s",
                event.event_id,
                event.source_repo,
                event.source_branch,
                len(targets),
                [t.target_repo for t in targets],
            )
        else:
            logger.info(
                "Trigger event %s (%s/%s) → no downstream targets.",
                event.event_id,
                event.source_repo,
                event.source_branch,
            )

        return targets

    # ------------------------------------------------------------------
    # Lifecycle (placeholder)
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the polling event loop (NOT YET IMPLEMENTED)."""
        logger.warning(
            "prismatic.core.dispatcher is a structural placeholder. "
            "The active dispatcher is prismatic/dispatcher.py."
        )
