"""
MarketRegistryClient — programmatic client for the Plugin Marketplace Registry.

Provides a lightweight abstraction over ``PluginMarketplaceRegistry`` that
other engine components (dispatcher, router, gateway) can inject as a
dependency.  The client caches the registry reference and exposes the same
query interface with added convenience methods.

Usage
-----
.. code-block:: python

    from prismatic.plugins.registry_client import MarketRegistryClient

    client = MarketRegistryClient(db_path="./plugin_registry.db")
    client.index_plugins("./plugins")
    for plugin in client.search("observability"):
        print(plugin.name, plugin.version)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from prismatic.plugins.registry import (
    PluginMarketplaceRegistry,
    PluginInfo,
    SearchResult,
)

logger = logging.getLogger("prismatic.plugins.client")


class MarketRegistryClient:
    """Programmatic client for the plugin marketplace registry.

    Wraps ``PluginMarketplaceRegistry`` with convenience methods and
    singleton-like access so that other engine components can share a
    single registry instance.

    Parameters
    ----------
    registry : PluginMarketplaceRegistry, optional
        An existing registry instance.  If omitted, a new one is created
        using *db_path*.
    db_path : str
        Path to the SQLite database file (default: ``./plugin_registry.db``).
        Only used when *registry* is not provided.
    """

    def __init__(
        self,
        registry: Optional[PluginMarketplaceRegistry] = None,
        db_path: str = "./plugin_registry.db",
    ) -> None:
        self._registry = registry or PluginMarketplaceRegistry(db_path=db_path)

    # ── Registry Delegation ───────────────────────────────────────────────

    @property
    def registry(self) -> PluginMarketplaceRegistry:
        """The underlying registry instance."""
        return self._registry

    def index_plugins(self, plugins_dir: str) -> int:
        """Scan and index all plugins in *plugins_dir*.

        Returns the number of plugins indexed.
        """
        return self._registry.index_plugins(plugins_dir)

    def reindex(self, plugins_dir: str) -> int:
        """Drop and re-index all plugins from *plugins_dir*."""
        return self._registry.reindex(plugins_dir)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._registry.close()

    # ── Query Methods ─────────────────────────────────────────────────────

    def list_plugins(
        self, offset: int = 0, limit: int = 20
    ) -> SearchResult:
        """Return a paginated list of all indexed plugins."""
        return self._registry.list_plugins(offset=offset, limit=limit)

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Return metadata for a single plugin by *name*, or ``None``."""
        return self._registry.get_plugin(name)

    def search_plugins(
        self,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        hook: Optional[str] = None,
        author: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> SearchResult:
        """Search indexed plugins with optional filters.

        See ``PluginMarketplaceRegistry.search_plugins()`` for details.
        """
        return self._registry.search_plugins(
            query=query,
            tag=tag,
            hook=hook,
            author=author,
            offset=offset,
            limit=limit,
        )

    # ── Convenience Shortcuts ─────────────────────────────────────────────

    def search(
        self, query: str, limit: int = 10
    ) -> List[PluginInfo]:
        """Quick full-text search returning only the result items.

        This is the simplest way to find a plugin by name, author, or
        keyword.  Equivalent to ``search_plugins(query=query, limit=limit).items``.
        """
        return self.search_plugins(query=query, limit=limit).items

    def by_tag(self, tag: str, limit: int = 20) -> List[PluginInfo]:
        """Return plugins matching a given *tag*."""
        return self.search_plugins(tag=tag, limit=limit).items

    def by_author(self, author: str, limit: int = 20) -> List[PluginInfo]:
        """Return plugins by a specific *author*."""
        return self.search_plugins(author=author, limit=limit).items

    def all(self) -> List[PluginInfo]:
        """Return all indexed plugins (no pagination)."""
        result = self.list_plugins(offset=0, limit=10_000)
        return result.items

    def count(self) -> int:
        """Return the total number of indexed plugins."""
        return self.list_plugins(limit=1).total

    def health(self) -> Dict[str, Any]:
        """Return a health-check dictionary for the registry."""
        try:
            total = self.count()
            return {"status": "ok", "plugin_count": total, "db_path": self._registry._db_path}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
