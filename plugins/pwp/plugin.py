from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import (
    PluginContext,
    PrismaticPlugin,
)

from .compiler import (
    get_token_provenance_for_tenant,
    get_tokens_for_tenant,
    render_template,
    set_tenant_tokens,
)


class PWPDesignTokenPlugin(PrismaticPlugin):
    """PWPDesignTokenPlugin — Compiles design tokens to CSS custom variables and renders starter templates."""

    def on_init(self, context: PluginContext) -> None:
        """Called by the loader on initial scan."""
        pass

    def register_tools(self) -> List[Dict[str, Any]]:
        """Optionally registers tools for the supervisor or CLI."""
        return []

    def render(self, template_name: str, tenant_id: str = None) -> str:
        """Expose template rendering to callers."""
        return render_template(template_name, tenant_id)

    def get_tokens(self, tenant_id: str = None) -> dict:
        """Get design tokens for tenant."""
        return get_tokens_for_tenant(tenant_id)

    def set_tokens(self, tenant_id: str, tokens: dict) -> None:
        """Set override tokens for tenant."""
        set_tenant_tokens(tenant_id, tokens)

    def get_token_provenance(self, tenant_id: str | None = None) -> dict:
        """Get deterministic token provenance for deploy metadata."""
        return get_token_provenance_for_tenant(tenant_id)
