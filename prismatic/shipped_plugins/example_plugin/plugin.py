"""
ExamplePlugin — minimal reference implementation of PrismaticPlugin.

Used as a template for new plugin authors. Demonstrates the required
abstract methods and optional lifecycle hooks without any real
functionality.
"""

from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import (
    AgentContract,
    PluginContext,
    PrismaticPlugin,
)


class ExamplePlugin(PrismaticPlugin):
    """Reference plugin demonstrating the PrismaticPlugin contract."""

    def on_init(self, context: PluginContext) -> None:
        """Set up the plugin. No-op for the reference example."""

    def register_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions. Empty for the reference example."""
        return []

    # Optional lifecycle hooks

    def before_task_execution(self, contract: AgentContract) -> None:
        """Called before agent worker spawn. No-op."""

    def after_task_execution(
        self, contract: AgentContract, result: dict[str, Any]
    ) -> None:
        """Called after agent worker exits. No-op."""

    def on_state_transition(
        self, issue_id: str, from_state: str, to_state: str
    ) -> None:
        """Called on Linear ticket state change. No-op."""
