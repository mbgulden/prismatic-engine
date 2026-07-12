from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class PrismaticMusicSfxPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        self.context = context

    def register_tools(self) -> list[dict[str, Any]]:
        return []

    def capability_contract(self) -> dict[str, Any]:
        return {'plugin_id': 'prismatic-prismatic-music-sfx', 'capabilities': [{'id': 'music-sfx.generation', 'label': 'music-sfx generation and management', 'category': 'music-sfx', 'tools': ['generate_music', 'generate_sfx', 'stem_audio', 'analyze_audio']}]}

    def register_mcp_servers(self) -> list[dict[str, Any]]:
        return [{'name': 'prismatic-music-sfx-mcp', 'transport': 'stdio', 'command': 'python3 -m prismatic_music_sfx.mcp_server', 'url': None, 'resources': ['audio.jobs', 'audio.assets', 'audio.cues'], 'tools': ['generate_music', 'generate_sfx', 'stem_audio', 'analyze_audio'], 'auth_env': []}]
