"""
PWPHookTestPlugin — registers for all 4 PWP hooks and records events.

Used by ``tests/test_pwp_hooks.py`` to verify that the
:class:`PWPPluginRunner` fires hooks in the correct order, even when
one of the hooks itself raises.

The plugin stores every hook invocation on ``cls.events`` (a class-level
list) so tests can inspect the ordering without needing a mock runner.

This file lives under ``plugins/`` (not ``prismatic/``) because it is
loaded as a normal plugin via the engine's discovery mechanism, and
because it depends on the runtime manifest discovery code in
``prismatic.core.registry``.  The plugin is intentionally not installed
in production; it is a fixture used by the test suite only.
"""

from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import (
    AgentContract,
    PluginContext,
    PrismaticPlugin,
)


class PWPHookTestPlugin(PrismaticPlugin):
    """Records every PWP hook invocation on a class-level list."""

    events: List[Dict[str, Any]] = []

    def on_init(self, context: PluginContext) -> None:
        # Re-initialise events for each fresh loader run.
        PWPHookTestPlugin.events = []

    def register_tools(self) -> List[Dict[str, Any]]:
        return []

    def on_pre_pipeline(
        self, pipeline_id: str, context: Dict[str, Any]
    ) -> None:
        PWPHookTestPlugin.events.append(
            {"hook": "on_pre_pipeline", "pipeline_id": pipeline_id}
        )

    def on_post_pipeline(
        self, pipeline_id: str, result: Dict[str, Any]
    ) -> None:
        PWPHookTestPlugin.events.append(
            {
                "hook": "on_post_pipeline",
                "pipeline_id": pipeline_id,
                "status": result.get("status"),
            }
        )

    def on_error(
        self, pipeline_id: str, exc: BaseException, stage: str
    ) -> None:
        PWPHookTestPlugin.events.append(
            {
                "hook": "on_error",
                "pipeline_id": pipeline_id,
                "stage": stage,
                "exc_type": type(exc).__name__,
            }
        )

    def on_deploy(
        self, pipeline_id: str, target: str, artifact: Dict[str, Any]
    ) -> None:
        PWPHookTestPlugin.events.append(
            {
                "hook": "on_deploy",
                "pipeline_id": pipeline_id,
                "target": target,
            }
        )
