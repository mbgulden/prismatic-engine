"""Optional Prismatic Engine adapter for the standalone PWP domain package.

This is the only PWP module allowed to import PE runtime protocol types. It
implements the stable plugin protocol and exposes PWP metadata for PE's
capability-router consumer; it does not import or vendor router implementation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import PluginContext, PrismaticPlugin

from .domain import PWP_CAPABILITY_CONTRACT, PWPDomainService


PE_PLUGIN_PROTOCOL = "prismatic.interface.plugin >=0.2.0,<2.0.0"
CAPABILITY_ROUTER_CONSUMER = "prismatic.capability_router (PE-owned consumer)"


class PWPDesignTokenPlugin(PrismaticPlugin):
    """PE lifecycle wrapper around independently testable PWP domain behavior."""

    def __init__(self, domain: PWPDomainService | None = None) -> None:
        self.domain = domain or PWPDomainService()

    def on_init(self, context: PluginContext) -> None:
        """Receive PE context without exposing it to the PWP domain service."""
        self.context = context

    def capability_contract(self) -> Dict[str, Any]:
        return self.domain.capability_contract()

    def connection_contract(self) -> Dict[str, Any]:
        contract = self.domain.capability_contract()
        return {
            "plugin_id": contract["plugin_id"],
            "connect_points": list(contract["connect_points"]),
            "disconnect_points": list(contract["disconnect_points"]),
            "pe_plugin_protocol": PE_PLUGIN_PROTOCOL,
            "capability_router_consumer": CAPABILITY_ROUTER_CONSUMER,
        }

    def register_tools(self) -> List[Dict[str, Any]]:
        return self.domain.register_tools()

    def render(self, template_name: str, tenant_id: str | None = None) -> str:
        return self.domain.render(template_name, tenant_id)

    def get_tokens(self, tenant_id: str | None = None) -> dict:
        return self.domain.get_tokens(tenant_id)

    def set_tokens(self, tenant_id: str, tokens: dict) -> None:
        self.domain.set_tokens(tenant_id, tokens)

    def credentials_refresh(self, provider: str, verify: bool = True) -> Dict[str, Any]:
        return self.domain.credentials_refresh(provider, verify)

    def credentials_status(self, provider: str, verify: bool = False) -> Dict[str, Any]:
        return self.domain.credentials_status(provider, verify)


__all__ = [
    "CAPABILITY_ROUTER_CONSUMER",
    "PE_PLUGIN_PROTOCOL",
    "PWP_CAPABILITY_CONTRACT",
    "PWPDesignTokenPlugin",
]
