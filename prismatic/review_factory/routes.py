"""RF-5: Review Factory routes — sub-router on the existing merge_factory_router.

Adds review factory endpoints under ``/api/merge-factory/review/``
by creating a sub-router that is included by the existing
``merge_factory_router`` in ``prismatic/api/routers/merge_factory.py``.

Endpoints:
    GET  /api/merge-factory/review/queue            — Queue depth by state
    GET  /api/merge-factory/review/jobs              — List jobs (?state=)
    GET  /api/merge-factory/review/job/{id}          — Job detail
    GET  /api/merge-factory/review/authorizations    — Authorization state
    POST /api/merge-factory/review/janitor           — Run stale lease janitor

Usage
-----
In ``prismatic/api/routers/merge_factory.py``, add:

    from prismatic.review_factory.routes import review_router
    router.include_router(review_router)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.responses import JSONResponse

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import ReviewJobState
from prismatic.review_factory.queue import ReviewQueue


def _create_review_router() -> Any:
    """Create the review factory sub-router."""
    if not _HAS_FASTAPI:
        return None

    review_router = APIRouter(prefix="/review", tags=["review-factory"])

    def _get_queue() -> ReviewQueue:
        """Lazy singleton for the review queue."""
        if not hasattr(_get_queue, "_instance"):
            _get_queue._instance = ReviewQueue()
        return _get_queue._instance

    # ── Queue overview ───────────────────────────────────────────────

    @review_router.get("/queue")
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

    # ── Job listing ──────────────────────────────────────────────────

    @review_router.get("/jobs")
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
        }

    # ── Job detail ───────────────────────────────────────────────────

    @review_router.get("/job/{review_job_id}")
    async def get_job_detail(review_job_id: str) -> Dict[str, Any]:
        """Full job detail including receipts, decisions, and authorization."""
        q = _get_queue()
        job = q.db.get_review_job(review_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        receipts = q.db.get_receipts_for_job(review_job_id)
        decisions = q.db.get_decisions_for_job(review_job_id)
        auth = q.db.get_authorization_for_job(review_job_id)

        return {
            "job": {
                "review_job_id": job.review_job_id,
                "completed_work_id": job.completed_work_id,
                "task_id": job.task_id,
                "state": job.state,
                "risk_tier": job.risk_tier,
                "policy_version": job.policy_version,
                "repository": job.repository,
                "base_commit": job.base_commit,
                "candidate_commit": job.candidate_commit,
                "candidate_tree": job.candidate_tree,
                "changed_paths": job.changed_paths,
                "result_packet_path": job.result_packet_path,
                "required_witnesses": job.required_witnesses,
                "completed_witnesses": job.completed_witnesses,
                "created_at": job.created_at,
                "lease_owner": job.lease_owner,
                "lease_expires_at": job.lease_expires_at,
            },
            "receipts": [
                {
                    "receipt_id": r.receipt_id,
                    "classification": r.classification,
                    "changed_path_invariance_proof": r.changed_path_invariance_proof,
                    "created_at": r.created_at,
                }
                for r in receipts
            ],
            "decisions": [
                {
                    "decision_id": d.decision_id,
                    "reviewer_id": d.reviewer_id,
                    "verdict": d.verdict,
                    "created_at": d.created_at,
                }
                for d in decisions
            ],
            "authorization": {
                "authorization_id": auth.authorization_id,
                "actor": auth.actor,
                "scope": auth.scope,
                "is_consumed": auth.is_consumed,
                "is_expired": auth.is_expired,
                "expires_at": auth.expires_at,
            }
            if auth
            else None,
        }

    # ── Authorizations ───────────────────────────────────────────────

    @review_router.get("/authorizations")
    async def list_authorizations() -> Dict[str, Any]:
        """List all merge authorizations (pending and consumed)."""
        q = _get_queue()
        cur = q.db.conn.execute(
            "SELECT * FROM merge_authorizations ORDER BY expires_at DESC LIMIT 50"
        )
        rows = cur.fetchall()
        return {
            "count": len(rows),
            "authorizations": [
                {
                    "authorization_id": r["authorization_id"],
                    "review_job_id": r["review_job_id"],
                    "actor": r["actor"],
                    "scope": r["scope"],
                    "expires_at": r["expires_at"],
                    "consumed_at": r["consumed_at"],
                    "is_consumed": bool(r["consumed_at"]),
                }
                for r in rows
            ],
        }

    # ── Janitor ──────────────────────────────────────────────────────

    @review_router.post("/janitor")
    async def run_janitor() -> Dict[str, Any]:
        """Run the stale lease janitor. Returns count of reset leases."""
        q = _get_queue()
        result = q.run_janitor()
        return {
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Statistics ───────────────────────────────────────────────────

    @review_router.get("/stats")
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

    return review_router


# Module-level router instance
review_router = _create_review_router()
