from __future__ import annotations

from typing import Any, Dict, List
from prismatic.interface.plugin import PluginContext, PrismaticPlugin

class CuratorTapPlugin(PrismaticPlugin):
    """Minimal Curator Tap validation plugin."""

    def on_init(self, context: PluginContext) -> None:
        pass

    def register_tools(self) -> List[Dict[str, Any]]:
        return []
