"""Standalone PWP domain facade.

Prismatic Engine loader integration intentionally lives in
``prismatic_web_publisher.adapters.prismatic_engine`` and is only available
when the optional ``prismatic-engine`` extra is installed.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from prismatic.interface.plugin import PluginContext, PrismaticPlugin
except ImportError:
    class PluginContext:  # type: ignore
        pass
    class PrismaticPlugin:  # type: ignore
        pass

from .compiler import get_tokens_for_tenant, render_template, set_tenant_tokens
from .oauth_credentials import (
    PROVIDERS,
    TokenPaths,
    default_token_paths,
    refresh_oauth_token,
    validate_token_shape,
    verify_ubersuggest_mcp,
)

PWP_CAPABILITY_CONTRACT: Dict[str, Any] = {
    "plugin_id": "pwp-design-token-plugin",
    "capabilities": [
        "theme validation/diff/compiler",
        "credential provider refresh/status",
    ],
    "integration": "optional Prismatic Engine adapter",
}


class PWPDomainTools:
    """Standalone PWP tools with no Prismatic Engine runtime dependency."""

    def on_init(self, context: PluginContext) -> None:
        """Initialize plugin inside Prismatic Engine dispatcher."""
        return

    def capability_contract(self) -> Dict[str, Any]:
        return dict(PWP_CAPABILITY_CONTRACT)

    def connection_contract(self) -> Dict[str, Any]:
        return {
            "plugin_id": "pwp-design-token-plugin",
            "state": "connected",
            "connect_points": [
                "plugins/pwp/plugin-manifest.yaml",
                "plugins/pwp/plugin.py",
                "scripts/pwp",
                "prismatic/pwp_integration.py",
            ],
            "disconnect_points": [
                "api/pwp/disconnect",
                "dashboard/pwp-disconnect-button",
            ],
        }


    def register_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "pwp_credentials_refresh",
                "description": ("Rotate a registered PWP provider OAuth credential without returning token material."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "enum": sorted(PROVIDERS)},
                        "verify": {"type": "boolean"},
                    },
                    "required": ["provider"],
                },
            },
            {
                "name": "pwp_credentials_status",
                "description": ("Validate registered PWP provider token files without returning token material."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "enum": sorted(PROVIDERS)},
                        "verify": {"type": "boolean"},
                    },
                    "required": ["provider"],
                },
            },
        ]

    def render(self, template_name: str, tenant_id: str | None = None) -> str:
        return render_template(template_name, tenant_id or "default")

    def get_tokens(self, tenant_id: str | None = None) -> dict:
        return get_tokens_for_tenant(tenant_id or "default")

    def set_tokens(self, tenant_id: str, tokens: dict) -> None:
        set_tenant_tokens(tenant_id, tokens)

    def credentials_refresh(self, provider: str, verify: bool = True) -> Dict[str, Any]:
        provider_config = PROVIDERS[provider]
        verifier = verify_ubersuggest_mcp if provider == "ubersuggest" and verify else None
        result = refresh_oauth_token(provider_config, default_token_paths(provider), verifier=verifier)
        return result.public_dict()

    def credentials_status(self, provider: str, verify: bool = False) -> Dict[str, Any]:
        provider_config = PROVIDERS[provider]
        paths: TokenPaths = default_token_paths(provider)
        access = paths.access_token.read_text(encoding="utf-8").strip()
        refresh = paths.refresh_token.read_text(encoding="utf-8").strip()
        validate_token_shape(access, label="access", provider=provider_config)
        validate_token_shape(refresh, label="refresh", provider=provider_config)
        payload: Dict[str, Any] = {
            "status": "ok",
            "provider": provider,
            "access_token_len": len(access),
            "refresh_token_len": len(refresh),
        }
        if verify and provider == "ubersuggest":
            payload["verified"] = dict(verify_ubersuggest_mcp(access))
        return payload


class PWPDesignTokenPlugin(PWPDomainTools, PrismaticPlugin):
    """PWP Design Token Plugin fulfilling the PrismaticPlugin contract."""

    def on_init(self, context: PluginContext) -> None:
        """Initialize plugin inside Prismatic Engine dispatcher."""
        return None

    def register_tools(self) -> List[Dict[str, Any]]:
        """Return registered tools for PWP."""
        return super().register_tools()

