"""
Prismatic Plugin Marketplace — registry, search, and client modules.

This package provides a SQLite-backed registry of all installed plugins,
REST API integration for the gateway server, and a lightweight client
class for programmatic access from other engine components.

Contents
--------
* **registry.py**       — ``PluginMarketplaceRegistry`` — SQLite-backed index
* **registry_client.py** — ``MarketRegistryClient`` — programmatic client
"""

from __future__ import annotations

from .registry import PluginMarketplaceRegistry, PluginInfo, SearchResult
from .registry_client import MarketRegistryClient

__all__ = [
    "PluginMarketplaceRegistry",
    "PluginInfo",
    "SearchResult",
    "MarketRegistryClient",
]
