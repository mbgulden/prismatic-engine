"""Workspace REST API routes for the Curated Workspace Plugin (WA-6).

Mounts under /api/workspace/.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, HTTPException, Query

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from prismatic.workspace.acceptance import AcceptanceProtocol
from prismatic.workspace.manifest import WorkspaceManifestEntry
from prismatic.workspace.static.share import WorkspaceShareManager
from prismatic.workspace.tree import WorkspaceTreeWalker, default_deployed_docs_root

_CACHE_TTL = 60.0  # 60 seconds cache per §16.8
_TREE_CACHE: dict[str, Any] = {"timestamp": 0, "entries": []}


def _get_tree_cached(walker: WorkspaceTreeWalker) -> list[WorkspaceManifestEntry]:
    now = time.time()
    if now - _TREE_CACHE["timestamp"] < _CACHE_TTL and _TREE_CACHE["entries"]:
        return _TREE_CACHE["entries"]

    entries = walker.walk()
    _TREE_CACHE["timestamp"] = now
    _TREE_CACHE["entries"] = entries
    return entries


def create_workspace_router(docs_root: Optional[Path] = None) -> Any:
    """Create FastAPI router for workspace plugin."""
    if not _HAS_FASTAPI:
        return None

    root = docs_root or default_deployed_docs_root()
    walker = WorkspaceTreeWalker(docs_root=root)
    share_mgr = WorkspaceShareManager()
    acceptance = AcceptanceProtocol(docs_root=root)

    router = APIRouter(prefix="/workspace", tags=["workspace"])

    @router.get("/tree")
    async def get_workspace_tree() -> Dict[str, Any]:
        """Return curated list of entries grouped by category."""
        entries = _get_tree_cached(walker)
        by_category: dict[str, list[dict[str, Any]]] = {}

        for entry in entries:
            cat = entry.category
            by_category.setdefault(cat, []).append(entry.to_dict())

        return {
            "total_entries": len(entries),
            "by_category": by_category,
            "timestamp": time.time(),
        }

    @router.get("/entry/{doc_id}")
    async def get_entry_detail(doc_id: str) -> Dict[str, Any]:
        """Get detail and content for a single document."""
        entries = _get_tree_cached(walker)
        target = next((e for e in entries if e.id == doc_id), None)
        if not target:
            raise HTTPException(
                status_code=404, detail=f"Document '{doc_id}' not found"
            )

        full_path = root / target.path
        content = ""
        if full_path.exists():
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                content = "Error reading content"

        return {
            "entry": target.to_dict(),
            "content": content,
        }

    @router.post("/acceptance")
    async def run_acceptance(
        path: str = Query(..., description="Relative doc path"),
    ) -> Dict[str, Any]:
        """Run acceptance protocol on a specific doc path."""
        full_path = root / path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail=f"File '{path}' not found")

        result = acceptance.validate(full_path)
        return {"path": path, "result": result}

    @router.post("/share")
    async def create_share_link(
        doc_id: str = Query(..., description="Document ID"),
        ttl_seconds: int = Query(86400, description="TTL in seconds (default 24h)"),
    ) -> Dict[str, Any]:
        """Generate a signed share link for a document."""
        entries = _get_tree_cached(walker)
        target = next((e for e in entries if e.id == doc_id), None)
        if not target:
            raise HTTPException(
                status_code=404, detail=f"Document '{doc_id}' not found"
            )

        return share_mgr.generate_token(doc_id=doc_id, ttl_seconds=ttl_seconds)

    @router.get("/share/{token}")
    async def view_shared_entry(token: str) -> Dict[str, Any]:
        """View a document via signed share token."""
        valid, doc_id, err = share_mgr.validate_token(token)
        if not valid:
            raise HTTPException(status_code=403, detail=f"Share link error: {err}")

        entries = _get_tree_cached(walker)
        target = next((e for e in entries if e.id == doc_id), None)
        if not target:
            raise HTTPException(
                status_code=404, detail=f"Shared document '{doc_id}' not found"
            )

        full_path = root / target.path
        content = full_path.read_text(encoding="utf-8") if full_path.exists() else ""

        return {
            "entry": target.to_dict(),
            "content": content,
        }

    @router.post("/acceptance/override")
    async def override_acceptance(
        path: str = Query(..., description="Relative doc path"),
        reason: str = Query(..., description="Manual operator override reason"),
    ) -> Dict[str, Any]:
        """Manually mark a document as accepted, overriding failed checks."""
        full_path = root / path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail=f"File '{path}' not found")

        result = acceptance.validate(full_path)
        result["passed"] = True
        result["status"] = "accepted_manual_override"
        result["override_reason"] = reason
        return {"path": path, "result": result}

    @router.post("/share/revoke")
    async def revoke_share_link(
        token: str = Query(..., description="Share token"),
    ) -> Dict[str, Any]:
        """Revoke a share token."""
        share_mgr.revoke_token(token)
        return {"status": "revoked", "token": token}

    @router.delete("/share/{token}")
    async def delete_share_token(token: str) -> Dict[str, Any]:
        """DELETE endpoint to revoke a share link token (Item 13)."""
        share_mgr.revoke_token(token)
        return {"status": "deleted", "token": token}

    return router


workspace_router = create_workspace_router()
