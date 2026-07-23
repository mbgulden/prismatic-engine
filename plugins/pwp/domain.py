"""PWP-owned domain services with no Prismatic Engine runtime dependency."""

from __future__ import annotations

from typing import Any, Dict, List, cast

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
    "connect_points": [
        "PE dashboard/API consumes PWP adapter integration status",
        "PE agents call registered PWP tools for additive workflows",
        "PWP manifest declares portable capabilities, governance, and disconnect behavior",
        "PWP reference lifecycle uses PE-owned plugin jobs/artifacts/provenance registries",
    ],
    "disconnect_points": [
        "PE marks PWP disconnected without deleting PWP code",
        "PE hides PWP readiness while preserving artifacts and core behavior",
        "PWP lifecycle artifacts and job history remain queryable after safe disconnect",
    ],
    "capabilities": [
        "theme validation/diff/compiler",
        "credential provider refresh/status",
        "visual/governance workflow augmentation",
        "full lifecycle reference demo: connect → job → artifact → approval → publish/export → disconnect",
    ],
}


class PWPDomainService:
    """Standalone PWP behavior; the PE adapter owns all PE lifecycle hooks."""

    def capability_contract(self) -> Dict[str, Any]:
        return dict(PWP_CAPABILITY_CONTRACT)

    def register_tools(self) -> List[Dict[str, Any]]:
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

    def render(self, template_name: str, tenant_id: str | None = None) -> str:
        return render_template(template_name, cast(str, tenant_id))

    def get_tokens(self, tenant_id: str | None = None) -> dict:
        return get_tokens_for_tenant(cast(str, tenant_id))

    def set_tokens(self, tenant_id: str, tokens: dict) -> None:
        set_tenant_tokens(tenant_id, tokens)

    def credentials_refresh(self, provider: str, verify: bool = True) -> Dict[str, Any]:
        provider_config = PROVIDERS[provider]
        verifier = verify_ubersuggest_mcp if provider == "ubersuggest" and verify else None
        result = refresh_oauth_token(
            provider_config, default_token_paths(provider), verifier=verifier
        )
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
