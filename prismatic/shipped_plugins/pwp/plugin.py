from __future__ import annotations

from typing import Any

from prismatic.interface.plugin import (
    PluginContext,
    PrismaticPlugin,
)

from .compiler import get_tokens_for_tenant, render_template, set_tenant_tokens
from .oauth_credentials import (
    PROVIDERS,
    TokenPaths,
    default_token_paths,
    refresh_oauth_token,
    validate_token_shape,
    verify_ubersuggest_mcp,
)

# Optional capability registration: publish-kpi-tracker is added as part of this plugin's
# extended capability surface. Import lazily so that the existing pwp-design-token-plugin
# does not require the new dependency tree at install time.
try:
    from plugins.pwp.capabilities.publish_kpi_tracker import (  # type: ignore
        PUBLISH_KPI_TRACKER_CAPABILITY_ID,
        PUBLISH_KPI_TRACKER_SITES_DIR,
        PUBLISH_KPI_TRACKER_VERSION,
        aggregate_publish_kpi,
        list_publish_kpi_sites,
        load_publish_kpi_schema,
        load_publish_kpi_site,
        publish_publish_kpi_dashboard,
        validate_publish_kpi_collection,
    )
    from plugins.pwp.capabilities.publish_kpi_tracker import (
        register_publish_kpi_plugin as _register_publish_kpi_plugin,
    )
except Exception:
    PUBLISH_KPI_TRACKER_CAPABILITY_ID = "pwp.publish-kpi-tracker"
    PUBLISH_KPI_TRACKER_VERSION = "1.0.0"
    PUBLISH_KPI_TRACKER_SITES_DIR = None
    PUBLISH_KPI_TRACKER_AVAILABLE = False

    def _missing(*_args, **_kwargs):
        return {
            "error": "pwp.publish-kpi-tracker not installed; pip-install or remove the routes."
        }

    list_publish_kpi_sites = _missing
    load_publish_kpi_site = _missing
    load_publish_kpi_schema = _missing
    validate_publish_kpi_collection = _missing
    aggregate_publish_kpi = _missing
    publish_publish_kpi_dashboard = _missing
    _register_publish_kpi_plugin = None
else:
    PUBLISH_KPI_TRACKER_AVAILABLE = True


PWP_CAPABILITY_CONTRACT: dict[str, Any] = {
    "plugin_id": "pwp-design-token-plugin",
    "connect_points": [
        "PE dashboard/API consumes prismatic.pwp_integration.integration_status",
        "PE agents call scripts/pwp and registered pwp_* tools for additive workflows",
        "PWP manifest declares portable capabilities, governance, and disconnect behavior",
        "PWP reference lifecycle uses universal plugin jobs/artifacts/provenance registries",
    ],
    "disconnect_points": [
        "POST /api/pwp/disconnect marks PWP disconnected without deleting plugin code",
        "Dashboard hides PWP readiness while preserving artifacts and core PE behavior",
        "PWP lifecycle artifacts and job history remain queryable after safe disconnect",
    ],
    "capabilities": [
        "theme validation/diff/compiler",
        "credential provider refresh/status",
        "visual/governance workflow augmentation",
        "full lifecycle reference demo: connect → job → artifact → approval → publish/export → disconnect",
    ],
}


class PWPDesignTokenPlugin(PrismaticPlugin):
    """PWPDesignTokenPlugin — Compiles design tokens to CSS custom variables and renders starter templates."""

    def on_init(self, context: PluginContext) -> None:
        """Called by the loader on initial scan."""
        self.context = context
        # Register the PWP publish-kpi-tracker capability (additive dashboard surface).
        if PUBLISH_KPI_TRACKER_AVAILABLE and _register_publish_kpi_plugin is not None:
            try:
                _register_publish_kpi_plugin(self)
            except Exception as exc:
                # Capability registration must never break plugin load.
                print(f"pwp.publish-kpi-tracker registration failed: {exc}")

    def capability_contract(self) -> dict[str, Any]:
        """Return the additive PWP capability contract for PE dashboards/agents."""
        return dict(PWP_CAPABILITY_CONTRACT)

    def connection_contract(self) -> dict[str, Any]:
        """Return explicit connect/disconnect semantics for PE governance surfaces."""
        return {
            "plugin_id": PWP_CAPABILITY_CONTRACT["plugin_id"],
            "connect_points": list(PWP_CAPABILITY_CONTRACT["connect_points"]),
            "disconnect_points": list(PWP_CAPABILITY_CONTRACT["disconnect_points"]),
        }

    def register_tools(self) -> list[dict[str, Any]]:
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

    def credentials_refresh(self, provider: str, verify: bool = True) -> dict[str, Any]:
        """Refresh a registered provider OAuth credential without exposing secrets."""
        provider_config = PROVIDERS[provider]
        verifier = (
            verify_ubersuggest_mcp if provider == "ubersuggest" and verify else None
        )
        result = refresh_oauth_token(
            provider_config,
            default_token_paths(provider),
            verifier=verifier,
        )
        return result.public_dict()

    def credentials_status(self, provider: str, verify: bool = False) -> dict[str, Any]:
        """Validate provider token files and optionally run a live smoke check."""
        provider_config = PROVIDERS[provider]
        paths: TokenPaths = default_token_paths(provider)
        access = paths.access_token.read_text(encoding="utf-8").strip()
        refresh = paths.refresh_token.read_text(encoding="utf-8").strip()
        validate_token_shape(access, label="access", provider=provider_config)
        validate_token_shape(refresh, label="refresh", provider=provider_config)
        payload: dict[str, Any] = {
            "status": "ok",
            "provider": provider,
            "access_token_len": len(access),
            "refresh_token_len": len(refresh),
        }
        if verify and provider == "ubersuggest":
            payload["verified"] = dict(verify_ubersuggest_mcp(access))
        return payload
