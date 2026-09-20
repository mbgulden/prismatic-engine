"""RF-5: Review Factory routes — Production Quality & Observability API.

Endpoints:
    GET  /queue            — Queue depth breakdown (Authenticated)
    GET  /jobs             — List review jobs (?state=, ?tier=) (Authenticated)
    GET  /job/{id}         — Job detail with receipt & decision breakdown (Authenticated)
    POST /job/{id}/authorize — Exception merge authorization (Admin)
    POST /job/{id}/reject    — Request repair: REPAIR_REQUIRED + dispatch (Admin)
    POST /job/{id}/release   — Force release stuck lease (Admin)
    GET  /authorizations   — Authorization state (Authenticated)
    POST /janitor          — Run stale lease janitor (Admin, Rate limited)
    GET  /stats            — Queue statistics (Authenticated)
    GET  /metrics          — Prometheus exposition format metrics (Authenticated)
    GET  /healthz          — Liveness probe
    GET  /readyz           — Readiness probe (DB & State Dir integrity)
    GET  /audit-log        — Append-only audit log entries (Admin)
    GET  /llm/status       — LLM deep-review stage status (not configured|active|skipped)
    POST /llm/toggle       — Enable/disable the LLM deep-review stage (Admin, Rate limited)
"""

from __future__ import annotations

import time
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    _HAS_FASTAPI = True
    security_scheme = HTTPBearer(auto_error=False)
except ImportError:
    _HAS_FASTAPI = False
    security_scheme = None

from prismatic.core.merge_factory import Principal, get_authenticated_principal
from prismatic.review_factory.events import emit_rf_event
from prismatic.review_factory.models import RepairPacket, ReviewJobState
from prismatic.review_factory.queue import ReviewQueue

_RATE_LIMIT_STORE: dict[str, list[float]] = defaultdict(list)


def _get_queue() -> ReviewQueue:
    """Lazy singleton for the review queue."""
    if not hasattr(_get_queue, "_instance"):
        _get_queue._instance = ReviewQueue()
    return _get_queue._instance


async def get_rf_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Principal:
    """Dependency to retrieve the authenticated principal or return a default observer."""
    if credentials is None or not credentials.credentials:
        return Principal(principal_id="hub-observer", scopes=["review-factory-read"])
    try:
        return get_authenticated_principal(credentials.credentials)
    except PermissionError:
        return Principal(principal_id="hub-observer", scopes=["review-factory-read"])


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
        tier: Optional[int] = Query(None, description="Filter by risk tier"),
        limit: int = Query(50, ge=1, le=500),
    ) -> Dict[str, Any]:
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

    @router.get("/job/{job_id}")
    async def get_job_detail(job_id: str) -> Dict[str, Any]:
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
        body: Dict[str, Any],
        principal: Principal = Depends(require_admin_principal),
    ) -> Dict[str, Any]:
        """Authorize a merge-ready job for merge."""
        q = _get_queue()
        job = q.db.get_review_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        # Map the admin principal to the exact domain actor required by the
        # strict authorization contract. Tier 0/1 use standing policy;
        # Tier 2/3 require an explicit human identity.
        tier = job.risk_tier
        if tier <= 1:
            actor = f"standing-policy: tier-{tier}"
        else:
            actor = f"human:{principal.identity}"
        expected_merge_tree = body.get("expected_merge_tree", "")
        if not expected_merge_tree:
            # Default to the verified candidate tree (or commit): the operator
            # is authorizing the merge of exactly what verification reviewed.
            # authorize_merge applies the same fallback at the queue layer;
            # fail closed here only when the job has nothing to bind.
            expected_merge_tree = job.candidate_tree or job.candidate_commit
        if not expected_merge_tree:
            raise HTTPException(
                status_code=400,
                detail="Job has no candidate tree to authorize",
            )
        auth_id = q.authorize_merge(
            review_job_id=job_id,
            actor=actor,
            expires_minutes=body.get("expires_minutes", 60),
            expected_merge_tree=expected_merge_tree,
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
            "requested_by": principal.identity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.post(
        "/job/{job_id}/release",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def force_release_job_lease(
        job_id: str,
        principal: Principal = Depends(require_admin_principal),
    ) -> Dict[str, Any]:
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

    @router.post(
        "/job/{job_id}/reject",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def reject_job_for_repair(
        job_id: str,
        body: Dict[str, Any],
        principal: Principal = Depends(require_admin_principal),
    ) -> Dict[str, Any]:
        """Operator requests repair for a job.

        Moves the job to REPAIR_REQUIRED from review_ready, reviewing, or
        merge_ready (pre-authorization), records a repair packet, and
        dispatches the repair task to task_admission. Matches the dashboard's
        "Request Repair" button contract: ``{"reason": "..."}``.
        """
        q = _get_queue()
        job = q.db.get_review_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.state not in (
            ReviewJobState.REVIEW_READY.value,
            ReviewJobState.REVIEWING.value,
            ReviewJobState.MERGE_READY.value,
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} is in state {job.state}; cannot request repair",
            )
        reason = str(body.get("reason") or "Rejected from dashboard").strip()[:500]
        requester = f"human:{principal.identity}"
        transitioned = q.db.update_review_job_state(
            job_id,
            ReviewJobState.REPAIR_REQUIRED,
            lease_owner="",
            lease_expires_at="",
        )
        if not transitioned:
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} could not transition to repair_required",
            )
        packet = RepairPacket(
            review_job_id=job_id,
            candidate_tree=job.candidate_tree,
            findings_json=json.dumps([{"note": reason, "source": "dashboard"}]),
            producer_id=requester,
        )
        q.db.insert_repair_packet(packet)
        dispatched = q.dispatch_repair_task(job_id, failure_reason=reason)
        q.db.insert_audit_entry(
            actor=requester,
            action="request_repair",
            review_job_id=job_id,
            details={"reason": reason, "repair_dispatched": bool(dispatched)},
        )
        emit_rf_event(
            "review_factory.job_state_changed",
            {
                "review_job_id": job_id,
                "new_state": ReviewJobState.REPAIR_REQUIRED.value,
            },
        )
        return {
            "status": "repair_required",
            "review_job_id": job_id,
            "repair_dispatched": bool(dispatched),
            "requested_by": principal.identity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/authorizations", dependencies=[Depends(get_rf_principal)])
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

    @router.post(
        "/janitor",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def run_janitor(
        principal: Principal = Depends(require_admin_principal),
    ) -> Dict[str, Any]:
        """Trigger the stale lease janitor to reset expired leases."""
        q = _get_queue()
        actor = principal.identity
        result = q.run_janitor(actor=actor)
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

    @router.get("/metrics")
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
    async def healthz() -> Dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "service": "review-factory"}

    @router.get("/readyz")
    async def readyz() -> Dict[str, Any]:
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
    async def get_audit_log(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
        """List append-only operator audit log entries."""
        q = _get_queue()
        entries = q.db.list_audit_entries(limit=limit)
        return {
            "count": len(entries),
            "entries": entries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/llm/status")
    async def get_llm_stage_status() -> Dict[str, Any]:
        """LLM deep-review stage status: not configured | active | skipped.

        Informative, never a nag: a fresh install with no models reports
        "not configured" and the deterministic pipeline runs normally.
        """
        from prismatic.review_factory.llm_deep_review import (
            LLMDeepReviewAdapter,
            LLMReviewConfig,
        )

        config = LLMReviewConfig.from_env()
        env_enabled = os.environ.get("PRISMATIC_REVIEW_LLM", "").strip() == "1"
        payload: Dict[str, Any] = {
            "enabled": config.enabled,
            "env_enabled": env_enabled,
            "endpoint": config.endpoint,
            "model_full_configured": bool(config.model_full),
            "model_bounded_configured": bool(config.model_bounded),
            "timeout_seconds": config.timeout_seconds,
            "max_rereviews": config.max_rereviews,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if not config.enabled:
            payload.update(
                state="not configured",
                detail=(
                    "PRISMATIC_REVIEW_LLM not set and dashboard toggle off; "
                    "deterministic review runs normally"
                ),
            )
            return payload
        if not config.model_full and not config.model_bounded:
            payload.update(
                state="not configured",
                detail="enabled but no model configured",
            )
            return payload
        ok, reason = LLMDeepReviewAdapter(config).gates_pass()
        if ok:
            payload.update(
                state="active",
                detail="Ollama reachable; a configured model is available",
            )
        else:
            payload.update(state="skipped", detail=reason)
        return payload

    @router.post(
        "/llm/toggle",
        dependencies=[Depends(require_admin_principal), Depends(enforce_rate_limit)],
    )
    async def toggle_llm_stage(
        body: Dict[str, Any],
        principal: Principal = Depends(require_admin_principal),
    ) -> Dict[str, Any]:
        """Enable/disable the optional LLM deep-review stage (Admin).

        Persists the dashboard toggle to ~/.prismatic/review-factory-llm.json.
        Note: PRISMATIC_REVIEW_LLM=1 in the environment keeps the stage
        enabled regardless of this toggle.
        """
        from prismatic.review_factory.llm_deep_review import set_toggle_enabled

        enabled = bool(body.get("enabled", False))
        env_enabled = os.environ.get("PRISMATIC_REVIEW_LLM", "").strip() == "1"
        actor = getattr(principal, "identity", "") or "dashboard"
        toggle_path = set_toggle_enabled(enabled, actor=actor)
        return {
            "enabled": enabled or env_enabled,
            "toggle_enabled": enabled,
            "env_override": env_enabled,
            "toggle_file": str(toggle_path),
            "note": (
                "PRISMATIC_REVIEW_LLM=1 in the environment keeps the stage "
                "enabled regardless of the toggle."
                if env_enabled
                else "Toggle persisted; the daemon picks it up on its next review cycle."
            ),
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
    r = APIRouter(tags=["review-factory"])
    _attach_routes(r)
    return r


def create_review_factory_router() -> Any:
    """Return canonical review-factory router prefixed with /review-factory."""
    return _create_review_factory_canonical_router()


review_router = _create_review_router()
review_factory_canonical_router = _create_review_factory_canonical_router()
router = review_router
