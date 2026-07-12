from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class PrismaticVideoPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        self.context = context

    def register_tools(self) -> list[dict[str, Any]]:
        return []

    def capability_contract(self) -> dict[str, Any]:
        return {'plugin_id': 'prismatic-prismatic-video', 'capabilities': [{'id': 'video.generation', 'label': 'video generation and management', 'category': 'video', 'tools': ['generate_video', 'edit_video', 'transcode_video', 'analyze_video']}]}

    def register_mcp_servers(self) -> list[dict[str, Any]]:
        return [{'name': 'prismatic-video-mcp', 'transport': 'stdio', 'command': 'python3 -m prismatic_video.mcp_server', 'url': None, 'resources': ['video.jobs', 'video.assets', 'video.timeline'], 'tools': ['generate_video', 'edit_video', 'transcode_video', 'analyze_video'], 'auth_env': []}]
