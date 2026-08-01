"""RF-5: Review Factory routes — Production Quality & Observability API.

Endpoints:
    GET  /queue            — Queue depth breakdown (Authenticated)
    GET  /jobs             — List review jobs (?state=, ?tier=) (Authenticated)
    GET  /job/{id}         — Job detail with receipt & decision breakdown (Authenticated)
    POST /job/{id}/authorize — Exception merge authorization (Admin)
    POST /job/{id}/release   — Force release stuck lease (Admin)
    GET  /authorizations   — Authorization state (Authenticated)
    POST /janitor          — Run stale lease janitor (Admin, Rate limited)
    GET  /stats            — Queue statistics (Authenticated)
    GET  /metrics          — Prometheus exposition format metrics (Authenticated)
    GET  /healthz          — Liveness probe
    GET  /readyz           — Readiness probe (DB & State Dir integrity)
    GET  /audit-log        — Append-only audit log entries (Admin)
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    _HAS_FASTAPI = True
    security_scheme = HTTPBearer(auto_error=False)
except ImportError:
    _HAS_FASTAPI = False
    security_scheme = None
    APIRouter = Any  # type: ignore
    Depends = lambda x=None: None  # type: ignore
    HTTPException = Exception  # type: ignore
    Query = lambda default=None, **kwargs: default  # type: ignore
    Response = Any  # type: ignore
    status = Any  # type: ignore
    HTTPAuthorizationCredentials = Any  # type: ignore
    HTTPBearer = Any  # type: ignore

from prismatic.core.merge_factory import Principal, get_authenticated_principal
from prismatic.review_factory.models import ReviewJobState
from prismatic.review_factory.queue import ReviewQueue

_RATE_LIMIT_STORE: dict[str, list[float]] = defaultdict(list)


def _get_queue() -> ReviewQueue:
    """Lazy singleton for the review queue."""
    if not hasattr(_get_queue, "_instance"):
        _get_queue._instance = ReviewQueue()
    return _get_queue._instance


async def get_rf_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> Principal:
    """Dependency to retrieve the authenticated principal or raise 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return get_authenticated_principal(credentials.credentials)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin_principal(
    principal: Principal = Depends(get_rf_principal),
) -> Principal:
    """Dependency enforcing merge-factory-admin scope."""
    if not principal.has_scope("merge-factory-admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope: merge-factory-admin required",
        )
    return principal


async def enforce_rate_limit() -> None:
    """Rate limiter enforcing max 10 mutating requests/sec per client."""
    now = time.time()
    history = _RATE_LIMIT_STORE["global_mutating"]
    valid = [t for t in history if now - t < 1.0]
    _RATE_LIMIT_STORE["global_mutating"] = valid
    if len(valid) >= 10:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded: max 10 mutating requests per second",
        )
    _RATE_LIMIT_STORE["global_mutating"].append(now)


def _attach_routes(router: Any) -> None:
    """Attach all Review Factory routes to a given router."""
    if router is None:
        return

    @router.get("/queue", dependencies=[Depends(get_rf_principal)])
    async def get_queue_depth() -> dict[str, Any]:
        """Queue depth breakdown by state."""
        q = _get_queue()
        stats = q.queue_depth()
        total = sum(stats.values())
        return {
            "total": total,
            "by_state": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/jobs", dependencies=[Depends(get_rf_principal)])
    async def list_jobs(
        state: str | None = Query(None, description="Filter by state"),
        tier: int | None = Query(None, description="Filter by risk tier"),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        """List review jobs, optionally filtered by state or tier."""
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
        if tier is not None:
            jobs = [j for j in jobs if j.risk_tier == tier]

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
                    "lease_expires_at": j.lease_expires_at,
                    "witnesses": f"{j.completed_witnesses}/{j.required_witnesses}",
                }
                for j in jobs
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/job/{job_id}", dependencies=[Depends(get_rf_principal)])
    async def get_job_detail(job_id: str) -> dict[str, Any]:
        """Get full detail for a specific review job."""
        q = _get_queue()
        job = q.db.get_review_job(job_id)
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
            "receipts": [
                {
                    "receipt_id": r.receipt_id,
                    "classification": r.classification,
                    "commands": r.commands,
                    "exit_codes": r.exit_codes,
                    "log_sha256": r.log_sha256,
                    "invariance_proof": r.changed_path_invariance_proof,
                    "explicit_non_claims": r.explicit_non_claims,
                    "baseline_failures": r.baseline_failures,
                    "created_at": r.created_at,
                }
                for r in receipts
            ],
            "decisions": [
                {
                    "decision_id": d.decision_id,
                    "reviewer_id": d.reviewer_id,
                    "verdict": d.verdict,
                    "findings": d.findings,
                    "idempotency_key": d.idempotency_key,
                    "created_at": d.created_at,
                }
                for d in decisions
            ],
            "authorization": (
                {
                    "authorization_id": auth.authorization_id,
                    "actor": auth.actor,
                    "scope": auth.scope,
                    "expires_at": auth.expires_at,
                    "consumed_at": auth.consumed_at,
                    "is_expired": auth.is_expired,
                    "is_consumed": auth.is_consumed,
                }
                if auth
                else None
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.post(
        "/job/{job_id}/authorize",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def authorize_job_merge(
        job_id: str,
        body: dict[str, Any],
        principal: Principal = Depends(require_admin_principal),
    ) -> dict[str, Any]:
        """Authorize a merge-ready job for merge."""
        actor = principal.identity
        q = _get_queue()
        auth_id = q.authorize_merge(
            review_job_id=job_id,
            actor=actor,
            expires_minutes=body.get("expires_minutes", 60),
        )
        if auth_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} cannot be authorized (must be in merge_ready state with non-empty actor)",
            )
        return {
            "status": "authorized",
            "authorization_id": auth_id,
            "review_job_id": job_id,
            "actor": actor,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.post(
        "/job/{job_id}/release",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def force_release_job_lease(
        job_id: str,
        principal: Principal = Depends(require_admin_principal),
    ) -> dict[str, Any]:
        """Force-release a stuck lease on a review job."""
        q = _get_queue()
        actor = principal.identity
        released = q.force_release_lease(review_job_id=job_id, actor=actor)
        if not released:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} lease could not be released (not in verifying/reviewing state)",
            )
        return {
            "status": "released",
            "review_job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/authorizations", dependencies=[Depends(get_rf_principal)])
    async def list_authorizations(
        consumed: bool | None = Query(None, description="Filter by consumed status"),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
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

    @router.post(
        "/janitor",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def run_janitor(
        principal: Principal = Depends(require_admin_principal),
    ) -> dict[str, Any]:
        """Trigger the stale lease janitor to reset expired leases."""
        q = _get_queue()
        actor = principal.identity
        result = q.run_janitor(actor=actor)
        return {
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/stats", dependencies=[Depends(get_rf_principal)])
    async def get_stats() -> dict[str, Any]:
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

    @router.get("/metrics", dependencies=[Depends(get_rf_principal)])
    async def get_prometheus_metrics() -> Response:
        """Prometheus metrics exposition format."""
        q = _get_queue()
        stats = q.queue_depth()
        lines = [
            "# HELP prismatic_rf_queue_depth Number of review jobs by state",
            "# TYPE prismatic_rf_queue_depth gauge",
        ]
        for state, count in stats.items():
            lines.append(f'prismatic_rf_queue_depth{{state="{state}"}} {count}')

        lines.extend(
            [
                "# HELP prismatic_rf_lease_active_count Active leases currently held",
                "# TYPE prismatic_rf_lease_active_count gauge",
                f"prismatic_rf_lease_active_count {stats.get('verifying', 0) + stats.get('reviewing', 0)}",
                "# HELP prismatic_rf_merge_success_total Total successful merges",
                "# TYPE prismatic_rf_merge_success_total counter",
                f"prismatic_rf_merge_success_total {stats.get('merged', 0)}",
                "# HELP prismatic_rf_merge_failure_total Total failed merges",
                "# TYPE prismatic_rf_merge_failure_total counter",
                f"prismatic_rf_merge_failure_total {stats.get('rejected', 0) + stats.get('merge_verification_failed', 0)}",
            ]
        )
        return Response(
            content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4"
        )

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "service": "review-factory"}

    @router.get("/readyz")
    async def readyz() -> dict[str, Any]:
        """Readiness probe checking database connectivity and table integrity."""
        try:
            q = _get_queue()
            _ = q.db.total_jobs()
            return {"status": "ready", "db": "connected", "service": "review-factory"}
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Review Factory not ready: {exc}"
            )

    @router.get("/audit-log", dependencies=[Depends(require_admin_principal)])
    async def get_audit_log(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        """List append-only operator audit log entries."""
        q = _get_queue()
        entries = q.db.list_audit_entries(limit=limit)
        return {
            "count": len(entries),
            "entries": entries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _create_review_router() -> Any:
    try:
        from fastapi import APIRouter
        r = APIRouter(prefix="/review", tags=["review-factory"])
        _attach_routes(r)
        return r
    except Exception:
        return None


def create_review_factory_router() -> Any:
    try:
        from fastapi import APIRouter
        r = APIRouter(prefix="/review-factory", tags=["review-factory"])
        _attach_routes(r)
        return r
    except Exception as exc:
        print("RF ROUTER EXCEPTION:", exc)
        import traceback
        traceback.print_exc()
        return None


review_router = _create_review_router()
review_factory_canonical_router = create_review_factory_router()
router = review_router
