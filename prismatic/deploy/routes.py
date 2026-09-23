"""Deploy REST API routes for deployment tracking (WB-7).

Mounts under /api/deploy/.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'pe' package resolves cleanly
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Canonical deploy pipeline lives in pe.deploy (WS1 registry-aware). The
# legacy prismatic.deploy.* modules are stale copies and must not serve the
# API -- the manual trigger has to route through the same registry as the
# signed webhook intake.
# noqa: E402 -- repo-root sys.path bootstrap above must run first.
from pe.deploy.config import default_repo_full_name  # noqa: E402
from pe.deploy.manifest import DeployManifestStore  # noqa: E402
from pe.deploy.receiver import DeployReceiverPipeline  # noqa: E402


def create_deploy_router() -> Any:
    """Create FastAPI router for deployment history and triggering."""
    if not _HAS_FASTAPI:
        return None

    store = DeployManifestStore()
    router = APIRouter(prefix="/deploy", tags=["deploy"])

    def _pipeline() -> "DeployReceiverPipeline":
        """Build a deploy pipeline on demand.

        Deferred because constructing one requires PRISMATIC_DEPLOY_SOURCE_REPO
        (#528 fail-fast), which the gateway does not set. Only the manual
        /trigger endpoint needs a pipeline; read-only endpoints work without
        it. Fail-fast fires here, at request time, never at import.
        """
        return DeployReceiverPipeline()

    @router.get("/recent")
    async def get_recent_deploys(
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Get recent deployment records."""
        deploys = store.list_deploys(limit=limit)
        return {
            "count": len(deploys),
            "deploys": [d.to_dict() for d in deploys],
            "timestamp": time.time(),
        }

    @router.get("/latest")
    async def get_latest_deploy() -> dict[str, Any]:
        """Get the most recent deployment record."""
        latest = store.get_latest()
        if not latest:
            raise HTTPException(status_code=404, detail="No deploy records found")
        return {"deploy": latest.to_dict()}

    @router.get("/status")
    async def get_deploy_status() -> dict[str, Any]:
        """Get deployment system status and latest summary."""
        latest = store.get_latest()
        return {
            "status": "active",
            "has_deploys": bool(latest),
            "latest_deploy_id": latest.deploy_id if latest else "",
            "timestamp": time.time(),
        }

    @router.get("/{deploy_id}")
    async def get_deploy_detail(deploy_id: str) -> dict[str, Any]:
        """Get detail for a single deploy record."""
        deploys = store.list_deploys(limit=1000)
        target = next((d for d in deploys if d.deploy_id == deploy_id), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"Deploy '{deploy_id}' not found")
        return {"deploy": target.to_dict()}

    @router.post("/trigger")
    async def trigger_manual_deploy(
        pr_sha: str = Query(..., description="Commit SHA"),
        pr_title: str = Query("Manual Deploy", description="PR title"),
        dry_run: bool = Query(True, description="Dry-run mode (default True per §16.8)"),
    ) -> dict[str, Any]:
        """Trigger a manual deploy (supports dry-run mode)."""
        # WS1: route through the same repo registry as the HTTP receiver --
        # the manual trigger always targets the default repo.
        payload = {
            "pr_sha": pr_sha,
            "pr_title": pr_title,
            "deployer": "manual:user",
            "dry_run": dry_run,
            "repository": default_repo_full_name(),
        }
        pipeline = _pipeline()
        pipeline.deploy_runner.dry_run = dry_run
        record = pipeline.process_deploy(payload)
        return {"status": "success" if record.success else "failed", "record": record.to_dict()}

    return router


deploy_router = create_deploy_router()
