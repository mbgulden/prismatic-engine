"""
prismatic/gateway/plugin_routes.py — Plugin Marketplace REST API.

Mount these routes on the FastAPI gateway to expose a searchable,
paginated plugin registry at ``/api/v1/plugins``.

Endpoints
---------
* ``GET  /api/v1/plugins``         — list all indexed plugins (paginated)
* ``GET  /api/v1/plugins/{name}``  — get a single plugin by name
* ``GET  /api/v1/plugins/search``  — search with filters (query, tag, hook, author)
* ``GET  /api/v1/plugins/stats``   — registry health + stats

Usage
-----
.. code-block:: python

    from fastapi import FastAPI
    from prismatic.gateway.plugin_routes import create_plugin_router

    app = FastAPI()
    app.include_router(create_plugin_router(db_path="./plugin_registry.db"))
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from prismatic.plugins.registry import PluginMarketplaceRegistry, SearchResult

logger = logging.getLogger("prismatic.gateway.plugins")


def create_plugin_router(
    db_path: Optional[str] = None,
    plugins_dir: Optional[str] = None,
) -> APIRouter:
    """Create a FastAPI ``APIRouter`` with plugin marketplace endpoints.

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite registry database.  Defaults to
        ``./plugin_registry.db``.
    plugins_dir : str, optional
        If provided, the registry will auto-index this directory on the
        first request (lazy initialisation).
    """
    registry = PluginMarketplaceRegistry(
        db_path=db_path or "./plugin_registry.db"
    )
    _auto_indexed = False

    def _ensure_indexed() -> None:
        nonlocal _auto_indexed
        if plugins_dir and not _auto_indexed:
            if os.path.isdir(plugins_dir):
                count = registry.index_plugins(plugins_dir)
                logger.info(
                    "Auto-indexed %d plugins from %s", count, plugins_dir
                )
            _auto_indexed = True

    router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])

    # ── List ──────────────────────────────────────────────────────────────

    @router.get("")
    async def list_plugins(
        offset: int = Query(0, ge=0, description="Number of items to skip"),
        limit: int = Query(20, ge=1, le=100, description="Max items to return"),
    ) -> Dict[str, Any]:
        """Return a paginated list of all indexed plugins."""
        _ensure_indexed()
        result = registry.list_plugins(offset=offset, limit=limit)
        return result.to_dict()

    # ── Get by Name ───────────────────────────────────────────────────────

    @router.get("/{name}")
    async def get_plugin(name: str) -> Dict[str, Any]:
        """Return metadata for a single plugin by *name*."""
        _ensure_indexed()
        info = registry.get_plugin(name)
        if info is None:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{name}' not found in registry",
            )
        return info.to_dict()

    # ── Search ────────────────────────────────────────────────────────────

    @router.get("/search")
    async def search_plugins(
        q: Optional[str] = Query(
            None, min_length=1, description="Full-text search query"
        ),
        tag: Optional[str] = Query(
            None, min_length=1, description="Filter by tag"
        ),
        hook: Optional[str] = Query(
            None, min_length=1, description="Filter by hook name"
        ),
        author: Optional[str] = Query(
            None, min_length=1, description="Filter by author (exact match)"
        ),
        offset: int = Query(0, ge=0, description="Number of items to skip"),
        limit: int = Query(
            20, ge=1, le=100, description="Max items to return"
        ),
    ) -> Dict[str, Any]:
        """Search indexed plugins with optional filters.

        At least one filter (*q*, *tag*, *hook*, or *author*) must be
        provided.  Filters are combined with AND logic.
        """
        _ensure_indexed()

        if not any([q, tag, hook, author]):
            raise HTTPException(
                status_code=400,
                detail="At least one search filter (q, tag, hook, or author) is required",
            )

        result = registry.search_plugins(
            query=q,
            tag=tag,
            hook=hook,
            author=author,
            offset=offset,
            limit=limit,
        )
        return result.to_dict()

    # ── Stats / Health ────────────────────────────────────────────────────

    @router.get("/stats")
    async def plugin_stats() -> Dict[str, Any]:
        """Return registry health and aggregate statistics."""
        _ensure_indexed()
        total = registry.list_plugins(limit=1).total
        return {
            "status": "ok",
            "plugin_count": total,
            "db_path": registry._db_path,
            "indexed": _auto_indexed,
        }

    return router
