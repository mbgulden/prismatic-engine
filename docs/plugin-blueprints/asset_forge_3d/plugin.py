from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class AssetForge3dPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        self.context = context

    def register_tools(self) -> list[dict[str, Any]]:
        return []

    def capability_contract(self) -> dict[str, Any]:
        return {'plugin_id': 'prismatic-asset-forge-3d', 'capabilities': [{'id': 'asset-forge-3d.generation', 'label': 'asset-forge-3d generation and management', 'category': 'asset-forge-3d', 'tools': ['forge_3d_asset', 'retopologize_mesh', 'bake_textures', 'rig_model', 'export_3d_asset']}]}

    def register_mcp_servers(self) -> list[dict[str, Any]]:
        return [{'name': 'asset-forge-3d-mcp', 'transport': 'http', 'command': None, 'url': '${ASSET_FORGE_3D_MCP_URL}', 'resources': ['asset_forge.jobs', 'asset_forge.assets', 'asset_forge.scenes', 'asset_forge.exports'], 'tools': ['forge_3d_asset', 'retopologize_mesh', 'bake_textures', 'rig_model', 'export_3d_asset'], 'auth_env': ['ASSET_FORGE_3D_API_KEY']}]
