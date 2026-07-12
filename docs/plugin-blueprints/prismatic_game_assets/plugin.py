from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class PrismaticGameAssetsPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        self.context = context

    def register_tools(self) -> list[dict[str, Any]]:
        return []

    def capability_contract(self) -> dict[str, Any]:
        return {'plugin_id': 'prismatic-prismatic-game-assets', 'capabilities': [{'id': 'game-assets.generation', 'label': 'game-assets generation and management', 'category': 'game-assets', 'tools': ['generate_game_asset', 'validate_game_asset', 'pack_asset_bundle', 'export_engine_asset']}]}

    def register_mcp_servers(self) -> list[dict[str, Any]]:
        return [{'name': 'prismatic-game-assets-mcp', 'transport': 'stdio', 'command': 'python3 -m prismatic_game_assets.mcp_server', 'url': None, 'resources': ['game.assets', 'game.bundles', 'game.engine_exports'], 'tools': ['generate_game_asset', 'validate_game_asset', 'pack_asset_bundle', 'export_engine_asset'], 'auth_env': []}]
