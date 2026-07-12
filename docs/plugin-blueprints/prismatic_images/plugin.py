from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import PluginContext, PrismaticPlugin


class PrismaticImagesPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        self.context = context

    def register_tools(self) -> list[dict[str, Any]]:
        return []

    def capability_contract(self) -> dict[str, Any]:
        return {'plugin_id': 'prismatic-prismatic-images', 'capabilities': [{'id': 'images.generation', 'label': 'images generation and management', 'category': 'images', 'tools': ['generate_image', 'edit_image', 'upscale_image', 'analyze_image']}]}

    def register_mcp_servers(self) -> list[dict[str, Any]]:
        return [{'name': 'prismatic-images-mcp', 'transport': 'stdio', 'command': 'python3 -m prismatic_images.mcp_server', 'url': None, 'resources': ['image.jobs', 'image.assets', 'image.references'], 'tools': ['generate_image', 'edit_image', 'upscale_image', 'analyze_image'], 'auth_env': []}]
