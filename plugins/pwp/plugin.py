from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import (
    PluginContext,
    PrismaticPlugin,
)

from .compiler import render_template, get_tokens_for_tenant, set_tenant_tokens
from .oauth_credentials import (
    PROVIDERS,
    TokenPaths,
    default_token_paths,
    refresh_oauth_token,
    validate_token_shape,
    verify_ubersuggest_mcp,
)


class PWPDesignTokenPlugin(PrismaticPlugin):
    """PWPDesignTokenPlugin — Compiles design tokens to CSS custom variables and renders starter templates."""

    def on_init(self, context: PluginContext) -> None:
        """Called by the loader on initial scan."""
        pass

    def register_tools(self) -> List[Dict[str, Any]]:
        """Registers PWP theme and credential-maintenance tools."""
        return [
            {
                "name": "pwp_credentials_refresh",
                "description": "Rotate a registered PWP provider OAuth credential using its stored refresh token. Returns only non-secret metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "enum": sorted(PROVIDERS),
                            "description": "Credential provider to refresh.",
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "Run provider smoke verification after token rotation.",
                        },
                    },
                    "required": ["provider"],
                },
            },
            {
                "name": "pwp_credentials_status",
                "description": "Validate registered PWP provider token files and optionally run a live provider smoke check. Returns no token material.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "enum": sorted(PROVIDERS),
                            "description": "Credential provider to inspect.",
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "Run provider smoke verification with the current access token.",
                        },
                    },
                    "required": ["provider"],
                },
            },
        ]

    def render(self, template_name: str, tenant_id: str = None) -> str:
        """Expose template rendering to callers."""
        return render_template(template_name, tenant_id)

    def get_tokens(self, tenant_id: str = None) -> dict:
        """Get design tokens for tenant."""
        return get_tokens_for_tenant(tenant_id)

    def set_tokens(self, tenant_id: str, tokens: dict) -> None:
        """Set override tokens for tenant."""
        set_tenant_tokens(tenant_id, tokens)

    def credentials_refresh(self, provider: str, verify: bool = True) -> Dict[str, Any]:
        """Refresh a registered provider OAuth credential without exposing secrets."""
        provider_config = PROVIDERS[provider]
        verifier = verify_ubersuggest_mcp if provider == "ubersuggest" and verify else None
        result = refresh_oauth_token(
            provider_config,
            default_token_paths(provider),
            verifier=verifier,
        )
        return result.public_dict()

    def credentials_status(self, provider: str, verify: bool = False) -> Dict[str, Any]:
        """Validate provider token files and optionally run a live smoke check."""
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
