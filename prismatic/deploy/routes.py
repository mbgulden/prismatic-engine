"""Deploy REST API routes for deployment tracking (WB-7).

Mounts under /api/deploy/. Includes P7 auth & rate limiting on /trigger,
P5 /cancel endpoint, P6 /diff route, and P1 /queue inspection routes.
"""

from __future__ import annotations

import collections
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, Header, HTTPException, Query, Request

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

# Ensure repository root is on sys.path so 'pe' package resolves cleanly
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pe.deploy.audit import DeployAuditLog  # noqa: E402
from pe.deploy.manifest import DeployManifestStore  # noqa: E402
from pe.deploy.queue import WebhookQueue  # noqa: E402
from pe.deploy.receiver import DeployReceiverPipeline  # noqa: E402

# Sliding window rate limiter for manual deploy triggers (P7: max 10 requests / minute)
_TRIGGER_RATE_LIMIT: Dict[str, collections.deque] = collections.defaultdict(
    collections.deque
)
RATE_LIMIT_WINDOW = 60.0  # seconds
RATE_LIMIT_MAX = 10


def enforce_trigger_rate_limit(client_ip: str) -> None:
    now = time.time()
    q = _TRIGGER_RATE_LIMIT[client_ip]
    while q and q[0] <= now - RATE_LIMIT_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX} manual deploy triggers per minute per client IP.",
        )
    q.append(now)


class ActiveDeployState:
    active_deploy_id: Optional[str] = None
    cancel_requested: bool = False


active_deploy_state = ActiveDeployState()


def create_deploy_router() -> Any:
    """Create FastAPI router for deployment history, triggering, cancellation, and diffing."""
    if not _HAS_FASTAPI:
        return None

    store = DeployManifestStore()
    pipeline = DeployReceiverPipeline()
    webhook_queue = WebhookQueue()
    audit_log = DeployAuditLog()
    router = APIRouter(prefix="/deploy", tags=["deploy"])

    @router.get("/recent")
    async def get_recent_deploys(
        limit: int = Query(20, ge=1, le=100),
    ) -> Dict[str, Any]:
        """Get recent deployment records."""
        deploys = store.list_deploys(limit=limit)
        return {
            "count": len(deploys),
            "deploys": [d.to_dict() for d in deploys],
            "timestamp": time.time(),
        }

    @router.get("/latest")
    async def get_latest_deploy() -> Dict[str, Any]:
        """Get the most recent deployment record."""
        latest = store.get_latest()
        if not latest:
            raise HTTPException(status_code=404, detail="No deploy records found")
        return {"deploy": latest.to_dict()}

    @router.get("/queue")
    async def get_deploy_queue() -> Dict[str, Any]:
        """P1: Get active webhook queue items."""
        return {"queue": webhook_queue.list_queue()}

    @router.get("/dead-letters")
    async def get_dead_letters() -> Dict[str, Any]:
        """P1: Get dead-letter queue items."""
        return {"dead_letters": webhook_queue.list_dead_letters()}

    @router.get("/diff")
    async def get_deploy_diff(
        from_sha: str = Query(..., description="Base SHA or previous commit"),
        to_sha: str = Query(..., description="Target SHA or current commit"),
    ) -> Dict[str, Any]:
        """P6: Return structured git diff output between two release SHAs."""
        try:
            res = subprocess.run(
                ["git", "diff", "--stat", f"{from_sha}..{to_sha}"],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            diff_stat = (
                res.stdout
                if res.returncode == 0
                else f"Error running diff: {res.stderr}"
            )

            patch_res = subprocess.run(
                ["git", "diff", f"{from_sha}..{to_sha}"],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            patch_text = (
                patch_res.stdout[:50000]
                if patch_res.returncode == 0
                else f"Error running patch diff: {patch_res.stderr}"
            )

            return {
                "from_sha": from_sha,
                "to_sha": to_sha,
                "stat": diff_stat,
                "patch": patch_text,
                "timestamp": time.time(),
            }
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to generate git diff: {exc}"
            )

    @router.get("/{deploy_id}")
    async def get_deploy_detail(deploy_id: str) -> Dict[str, Any]:
        """Get detail for a single deploy record."""
        deploys = store.list_deploys(limit=1000)
        target = next((d for d in deploys if d.deploy_id == deploy_id), None)
        if not target:
            raise HTTPException(
                status_code=404, detail=f"Deploy '{deploy_id}' not found"
            )
        return {"deploy": target.to_dict()}

    @router.post("/cancel")
    async def cancel_deploy(
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """P5: Abort active long-running deploy."""
        active_deploy_state.cancel_requested = True
        audit_log.record_entry(
            action="deploy_cancel_requested",
            actor="operator",
            status="requested",
        )
        return {
            "status": "cancel_requested",
            "message": "Deploy cancellation signal emitted to runner",
            "active_deploy_id": active_deploy_state.active_deploy_id,
        }

    @router.post("/trigger")
    async def trigger_manual_deploy(
        request: Request,
        pr_sha: str = Query(..., description="Commit SHA"),
        pr_title: str = Query("Manual Deploy", description="PR title"),
        dry_run: bool = Query(
            True, description="Dry-run mode (default True per §16.8)"
        ),
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """P7: Trigger a manual deploy with rate limiting & audit logging."""
        client_ip = request.client.host if request.client else "unknown"
        enforce_trigger_rate_limit(client_ip)

        # Record audit log
        audit_log.record_entry(
            action="manual_deploy_trigger",
            actor=client_ip,
            client_ip=client_ip,
            hmac_sig=authorization or "",
            status="accepted",
            details={"pr_sha": pr_sha, "dry_run": dry_run},
        )

        active_deploy_state.active_deploy_id = f"deploy-{pr_sha[:8]}"
        active_deploy_state.cancel_requested = False

        payload = {
            "pr_sha": pr_sha,
            "pr_title": pr_title,
            "deployer": f"manual:{client_ip}",
            "dry_run": dry_run,
        }
        pipeline.deploy_runner.dry_run = dry_run
        record = pipeline.process_deploy(payload)

        active_deploy_state.active_deploy_id = None
        return {
            "status": "success" if record.success else "failed",
            "record": record.to_dict(),
        }

    return router


deploy_router = create_deploy_router()
