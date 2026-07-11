from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import PluginContext, PrismaticPlugin

from .core import VisualVerifier


class VisualVerifierPlugin(PrismaticPlugin):
    """Prismatic plugin that exposes visual verification MCP-style tools."""

    def on_init(self, context: PluginContext) -> None:
        self.context = context
        config = (
            context.config.get("visual_verifier", {})
            if isinstance(context.config, dict)
            else {}
        )
        self.verifier = VisualVerifier(
            output_dir=config.get("output_dir"),
            cache_ttl_seconds=int(config.get("cache_ttl_seconds", 300)),
            require_browsermcp=bool(config.get("require_browsermcp", False)),
        )

    def register_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "verify_url",
                "description": "Render a URL at one or more viewports, capture screenshots, and return structured visual grades.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Target http(s) URL."},
                        "viewports": {"type": "array", "items": {"type": "object"}},
                        "checks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "verify_file",
                "description": "Render a local HTML file and return structured visual grades.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Local HTML file path.",
                        },
                        "viewports": {"type": "array", "items": {"type": "object"}},
                        "checks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "grade_screenshot",
                "description": "Grade a single base64-encoded PNG screenshot with Nano Banana 2 or fallback heuristics.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "screenshot_b64": {"type": "string"},
                        "checks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["screenshot_b64"],
                },
            },
            {
                "name": "compare_images",
                "description": "Compare two image paths/base64 payloads for brand or regression consistency.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_a": {"type": "string"},
                        "image_b": {"type": "string"},
                        "criteria": {
                            "type": ["array", "string"],
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["image_a", "image_b"],
                },
            },
        ]

    def verify_url(
        self,
        url: str,
        viewports: List[Dict[str, Any]] | None = None,
        checks: List[str] | None = None,
    ) -> Dict[str, Any]:
        return self.verifier.verify_url(url, viewports=viewports, checks=checks)

    def verify_file(
        self,
        path: str,
        viewports: List[Dict[str, Any]] | None = None,
        checks: List[str] | None = None,
    ) -> Dict[str, Any]:
        return self.verifier.verify_file(path, viewports=viewports, checks=checks)

    def grade_screenshot(
        self, screenshot_b64: str, checks: List[str] | None = None
    ) -> Dict[str, Any]:
        return self.verifier.grade_screenshot(screenshot_b64, checks=checks)

    def compare_images(
        self, image_a: str, image_b: str, criteria: List[str] | str | None = None
    ) -> Dict[str, Any]:
        return self.verifier.compare_images(image_a, image_b, criteria=criteria)
