"""FastAPI router for the Merge Factory (admission, lease, merge locks, and judge attestation)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from prismatic.core.merge_factory import (
    MergeFactoryStore,
    Principal,
    get_authenticated_principal,
)

router = APIRouter(prefix="/merge-factory", tags=["merge-factory"])
security_scheme = HTTPBearer(auto_error=False)


async def get_principal(
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


class PolicyRequest(BaseModel):
    stage_cap: int | None = Field(None, description="Stage cap limit (1, 2, or 3)")
    cron_paused: bool | None = Field(
        None, description="Whether generic cron is paused"
    )


class CohortAddRequest(BaseModel):
    issue_id: str = Field(..., description="Unique issue identifier")
    stage: int = Field(..., description="Stage number (1, 2, or 3)")
    sequence: int = Field(..., description="Sequence number for prioritization")
    allow_update: bool = Field(
        False, description="Explicitly allow updating existing non-leased cohort"
    )


class CohortStatusRequest(BaseModel):
    issue_id: str = Field(..., description="Issue identifier")
    status: str = Field(
        ..., description="Status (PENDING, ADMITTED, COMPLETED, EXCLUDED)"
    )


class LeaseAcquireRequest(BaseModel):
    issue_id: str = Field(..., description="Issue identifier")
    stage: int = Field(..., description="Stage number")
    ttl_seconds: int = Field(..., description="Time to live in seconds")


class AttestationRequest(BaseModel):
    issue_id: str = Field(..., description="Issue identifier")
    decision: str = Field(
        ..., description="APPROVE_MERGE, REPAIR, REJECT, SUPERSEDED, MANUAL_REVIEW"
    )
    base_sha: str = Field(..., description="Base Git commit hash")
    candidate_sha: str = Field(..., description="Candidate Git commit hash")
    manifest_digest: str = Field(..., description="Manifest SHA-256 digest")
    evidence_digest: str = Field(..., description="Evidence SHA-256 digest")
    repository: str = Field(..., description="Git repository name")
    target: str = Field(..., description="Target branch/destination name")


class LockAcquireRequest(BaseModel):
    repository: str = Field(..., description="Repository name")
    target: str = Field(..., description="Target branch/destination name")
    issue_id: str = Field(..., description="Issue identifier")
    base_sha: str = Field(..., description="Base Git commit hash")
    candidate_sha: str = Field(..., description="Candidate Git commit hash")
    manifest_digest: str = Field(..., description="Manifest SHA-256 digest")
    evidence_digest: str = Field(..., description="Evidence SHA-256 digest")
    approval_attestation_id: str = Field(..., description="Approval attestation ID")
    ttl_seconds: int = Field(..., description="Time to live in seconds")


class LockHeartbeatRequest(BaseModel):
    repository: str = Field(..., description="Repository name")
    target: str = Field(..., description="Target branch/destination name")
    issue_id: str = Field(..., description="Issue identifier")
    base_sha: str = Field(..., description="Base Git commit hash")
    candidate_sha: str = Field(..., description="Candidate Git commit hash")
    manifest_digest: str = Field(..., description="Manifest SHA-256 digest")
    evidence_digest: str = Field(..., description="Evidence SHA-256 digest")
    approval_attestation_id: str = Field(..., description="Approval attestation ID")


# ─────────────────────────────────────────────────────────────────────────
# ── Policy & Cohorts
# ─────────────────────────────────────────────────────────────────────────


@router.get("/policy")
async def get_policy() -> dict[str, Any]:
    """Retrieve the current operator policy."""
    return MergeFactoryStore().get_policy()


@router.post("/policy")
async def set_policy(
    request: PolicyRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Update the operator policy. Requires merge-factory-admin scope."""
    payload = request.dict(exclude_none=True)
    try:
        return MergeFactoryStore().set_policy(payload, principal)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400,
            detail=str(exc),
        )


@router.get("/cohort")
async def list_cohort() -> dict[str, Any]:
    """List all admission cohort items."""
    cohort = MergeFactoryStore().get_cohort()
    return {"cohort": cohort, "count": len(cohort)}


@router.post("/cohort")
async def add_to_cohort(
    request: CohortAddRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Add a task to the cohort. Requires merge-factory-admin scope."""
    try:
        return MergeFactoryStore().add_to_cohort(
            request.issue_id,
            request.stage,
            request.sequence,
            principal,
            request.allow_update,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400,
            detail=str(exc),
        )


@router.post("/cohort/status")
async def update_cohort_status(
    request: CohortStatusRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Update cohort status. Requires merge-factory-admin scope."""
    try:
        return MergeFactoryStore().update_cohort_status(
            request.issue_id, request.status, principal
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────
# ── Concurrency Leases
# ─────────────────────────────────────────────────────────────────────────


@router.post("/lease/acquire")
async def acquire_lease(
    request: LeaseAcquireRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Acquire concurrency lease. Enforces global stage cap."""
    try:
        return MergeFactoryStore().acquire_lease(
            request.issue_id, request.stage, request.ttl_seconds, principal
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (ValueError, BlockingIOError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/lease/heartbeat")
async def heartbeat_lease(
    issue_id: str = Query(..., description="Issue identifier"),
    lease_id: str = Query(..., description="Lease identifier"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Extend lease expiration."""
    try:
        return MergeFactoryStore().heartbeat_lease(issue_id, lease_id, principal)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/lease/release")
async def release_lease(
    issue_id: str = Query(..., description="Issue identifier"),
    lease_id: str = Query(..., description="Lease identifier"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Release concurrency lease."""
    try:
        MergeFactoryStore().release_lease(issue_id, lease_id, principal)
        return {"status": "released", "issue_id": issue_id, "lease_id": lease_id}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────
# ── George Merge-Judge
# ─────────────────────────────────────────────────────────────────────────


@router.post("/judge/attestation")
async def submit_attestation(
    request: AttestationRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Submit an append-only decision attestation. Requires merge-judge scope."""
    try:
        return MergeFactoryStore().submit_attestation(
            issue_id=request.issue_id,
            decision=request.decision,
            base_sha=request.base_sha,
            candidate_sha=request.candidate_sha,
            manifest_digest=request.manifest_digest,
            evidence_digest=request.evidence_digest,
            repository=request.repository,
            target=request.target,
            principal=principal,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/judge/attestation/{issue_id}")
async def get_attestation(
    issue_id: str,
    base_sha: str = Query(..., description="Base Git commit hash"),
    candidate_sha: str = Query(..., description="Candidate Git commit hash"),
    manifest_digest: str = Query(..., description="Manifest SHA-256 digest"),
    evidence_digest: str = Query(..., description="Evidence SHA-256 digest"),
    repository: str = Query(..., description="Git repository name"),
    target: str = Query(..., description="Target branch/destination name"),
) -> dict[str, Any]:
    """Pure/read-only validation of active candidate approval. Open to ordinary callers."""
    return MergeFactoryStore().validate_approval(
        issue_id=issue_id,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        evidence_digest=evidence_digest,
        repository=repository,
        target=target,
    )


# ─────────────────────────────────────────────────────────────────────────
# ── Merge Locks
# ─────────────────────────────────────────────────────────────────────────


@router.post("/lock/acquire")
async def acquire_lock(
    request: LockAcquireRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Acquire merge lock. Requires valid exact-candidate approval attestation."""
    try:
        return MergeFactoryStore().acquire_lock(
            repository=request.repository,
            target=request.target,
            issue_id=request.issue_id,
            base_sha=request.base_sha,
            candidate_sha=request.candidate_sha,
            manifest_digest=request.manifest_digest,
            evidence_digest=request.evidence_digest,
            approval_attestation_id=request.approval_attestation_id,
            ttl_seconds=request.ttl_seconds,
            principal=principal,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (ValueError, BlockingIOError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/lock/heartbeat")
async def heartbeat_lock(
    request: LockHeartbeatRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Extend lock expiration. Invalidates lock if bindings or approval changed."""
    try:
        return MergeFactoryStore().heartbeat_lock(
            repository=request.repository,
            target=request.target,
            issue_id=request.issue_id,
            base_sha=request.base_sha,
            candidate_sha=request.candidate_sha,
            manifest_digest=request.manifest_digest,
            evidence_digest=request.evidence_digest,
            approval_attestation_id=request.approval_attestation_id,
            principal=principal,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/lock/release")
async def release_lock(
    repository: str = Query(..., description="Repository name"),
    target: str = Query(..., description="Target branch/destination name"),
    issue_id: str = Query(..., description="Issue identifier"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Release merge lock."""
    MergeFactoryStore().release_lock(repository, target, issue_id, principal)
    return {"status": "released", "repository": repository, "target": target}
