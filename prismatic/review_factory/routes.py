"""RF-5: Review Factory routes — sub-router on the existing merge_factory_router.

Adds review factory endpoints under ``/api/merge-factory/review/``
and ``/api/review-factory/``.

Endpoints:
    GET  /queue            — Queue depth by state
    GET  /jobs             — List jobs (?state=)
    GET  /job/{id}         — Job detail
    GET  /authorizations   — Authorization state
    POST /janitor          — Run stale lease janitor
    GET  /stats            — Queue statistics
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, HTTPException, Query

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from prismatic.review_factory.models import ReviewJobState
from prismatic.review_factory.queue import ReviewQueue


def _get_queue() -> ReviewQueue:
    """Lazy singleton for the review queue."""
    if not hasattr(_get_queue, "_instance"):
        _get_queue._instance = ReviewQueue()
    return _get_queue._instance


def _attach_routes(router: Any) -> None:
    """Attach all Review Factory routes to a given router."""
    if not _HAS_FASTAPI or router is None:
        return

    @router.get("/queue")
    async def get_queue_depth() -> Dict[str, Any]:
        """Queue depth breakdown by state."""
        q = _get_queue()
        stats = q.queue_depth()
        total = sum(stats.values())
        return {
            "total": total,
            "by_state": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/jobs")
    async def list_jobs(
        state: Optional[str] = Query(None, description="Filter by state"),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        """List review jobs, optionally filtered by state."""
        q = _get_queue()
        state_filter = None
        if state:
            try:
                state_filter = ReviewJobState(state)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid state: {state}. Valid: {[s.value for s in ReviewJobState]}",
                )

        jobs = q.db.list_review_jobs(state=state_filter, limit=limit)
        return {
            "count": len(jobs),
            "jobs": [
                {
                    "review_job_id": j.review_job_id,
                    "task_id": j.task_id,
                    "state": j.state,
                    "risk_tier": j.risk_tier,
                    "repository": j.repository,
                    "created_at": j.created_at,
                    "lease_owner": j.lease_owner,
                    "witnesses": f"{j.completed_witnesses}/{j.required_witnesses}",
                }
                for j in jobs
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/job/{job_id}")
    async def get_job_detail(job_id: str) -> Dict[str, Any]:
        """Get detail for a specific review job."""
        q = _get_queue()
        job = q.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"Review job {job_id} not found"
            )

        receipts = q.db.get_receipts_for_job(job_id)
        decisions = q.db.get_decisions_for_job(job_id)
        auth = q.db.get_authorization_for_job(job_id)

        return {
            "job": {
                "review_job_id": job.review_job_id,
                "completed_work_id": job.completed_work_id,
                "task_id": job.task_id,
                "repository": job.repository,
                "base_commit": job.base_commit,
                "base_tree": job.base_tree,
                "candidate_commit": job.candidate_commit,
                "candidate_tree": job.candidate_tree,
                "risk_tier": job.risk_tier,
                "policy_version": job.policy_version,
                "state": job.state,
                "required_witnesses": job.required_witnesses,
                "completed_witnesses": job.completed_witnesses,
                "created_at": job.created_at,
                "lease_owner": job.lease_owner,
                "lease_expires_at": job.lease_expires_at,
            },
            "receipts_count": len(receipts),
            "decisions_count": len(decisions),
            "has_authorization": auth is not None,
            "authorization_expired": auth.is_expired if auth else None,
            "authorization_consumed": auth.is_consumed if auth else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/authorizations")
    async def list_authorizations(
        consumed: Optional[bool] = Query(None, description="Filter by consumed status"),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
        """List merge authorizations."""
        q = _get_queue()
        query = "SELECT * FROM merge_authorizations"
        params: list[Any] = []

        if consumed is not None:
            if consumed:
                query += " WHERE consumed_at != ''"
            else:
                query += " WHERE consumed_at = ''"

        query += " ORDER BY expires_at DESC LIMIT ?"
        params.append(limit)

        cur = q.db.conn.execute(query, params)
        auths = [q.db._row_to_authorization(r) for r in cur.fetchall()]

        return {
            "count": len(auths),
            "authorizations": [
                {
                    "authorization_id": a.authorization_id,
                    "review_job_id": a.review_job_id,
                    "repository": a.repository,
                    "pr_number": a.pr_number,
                    "scope": a.scope,
                    "actor": a.actor,
                    "expires_at": a.expires_at,
                    "consumed_at": a.consumed_at,
                    "is_expired": a.is_expired,
                    "is_consumed": a.is_consumed,
                }
                for a in auths
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.post("/janitor")
    async def run_janitor() -> Dict[str, Any]:
        """Trigger the stale lease janitor to reset expired leases."""
        q = _get_queue()
        result = q.run_janitor()
        return {
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/stats")
    async def get_stats() -> Dict[str, Any]:
        """Aggregate review factory statistics."""
        q = _get_queue()
        stats = q.queue_depth()
        total = q.db.total_jobs()

        merged = stats.get("merged", 0)
        rejected = stats.get("rejected", 0)
        failed = stats.get("merge_verification_failed", 0)

        return {
            "total_jobs": total,
            "active_jobs": total - merged - rejected - failed,
            "merged": merged,
            "rejected": rejected,
            "merge_verification_failed": failed,
            "queue_depth": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _create_review_router() -> Any:
    if not _HAS_FASTAPI:
        return None
    r = APIRouter(prefix="/review", tags=["review-factory"])
    _attach_routes(r)
    return r


def _create_review_factory_canonical_router() -> Any:
    if not _HAS_FASTAPI:
        return None
    r = APIRouter(prefix="/review-factory", tags=["review-factory"])
    _attach_routes(r)
    return r


review_router = _create_review_router()
review_factory_canonical_router = _create_review_factory_canonical_router()
