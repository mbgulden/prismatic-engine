"""MyGreeter — minimal example plugin.

Registers a single review check. Demonstrates the smallest possible
PrismaticPlugin: on_init + register_tools, no side effects at import time.
"""

from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class GreeterPlugin(PrismaticPlugin):
    """Registers one no-op check proving the plugin loaded and ran on_init."""

    def on_init(self, context: PluginContext) -> None:
        self.context = context
        registry = getattr(context, "review_registry", None)
        if registry is None:
            return
        registry.register_check("greeter-check", self._check)

    def register_tools(self) -> List[Dict[str, Any]]:
        return []

    def _check(self, diff: str) -> list:
        return []
