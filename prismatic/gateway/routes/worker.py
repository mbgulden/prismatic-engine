"""Gateway routes for distributed job queues, worker node registration, and execution telemetry."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from prismatic.worker.queue import WorkerQueueManager

worker_router = APIRouter(tags=["worker"])
_queue_manager: WorkerQueueManager | None = None


def get_queue_manager() -> WorkerQueueManager:
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = WorkerQueueManager()
    return _queue_manager


@worker_router.post("/api/gateway/jobs/enqueue")
async def gateway_enqueue_job(request: Request) -> JSONResponse:
    """Enqueue a job into the distributed execution queue."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    task_id = body.get("task_id")
    command = body.get("command")
    if not task_id or not command:
        raise HTTPException(status_code=400, detail="task_id and command are required")

    mgr = get_queue_manager()
    job = mgr.enqueue_job(
        task_id=task_id,
        command=command,
        target=body.get("target", "default"),
        tags=body.get("tags"),
        timeout_seconds=body.get("timeout_seconds", 300),
    )
    return JSONResponse({"ok": True, "job": job.to_dict()})


@worker_router.post("/api/gateway/jobs/lease")
async def gateway_lease_job(request: Request) -> JSONResponse:
    """Worker node claims an eligible queued job."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    mgr = get_queue_manager()
    job = mgr.lease_next_job(
        node_id=node_id,
        tags=body.get("tags"),
        ttl_seconds=body.get("ttl_seconds", 60),
    )
    return JSONResponse({"ok": True, "job": job.to_dict() if job else None})


@worker_router.post("/api/gateway/jobs/heartbeat")
async def gateway_heartbeat_job(request: Request) -> JSONResponse:
    """Worker heartbeat to maintain active job lease and fencing token."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    job_id = body.get("job_id")
    node_id = body.get("node_id")
    fence_token = body.get("fence_token")
    if not job_id or not node_id or fence_token is None:
        raise HTTPException(status_code=400, detail="job_id, node_id, and fence_token are required")

    mgr = get_queue_manager()
    success = mgr.heartbeat_job(
        job_id=job_id,
        node_id=node_id,
        fence_token=int(fence_token),
        ttl_seconds=body.get("ttl_seconds", 60),
    )
    return JSONResponse({"ok": True, "extended": success})


@worker_router.post("/api/gateway/jobs/complete")
async def gateway_complete_job(request: Request) -> JSONResponse:
    """Worker delivers final execution receipt and terminates job lease."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    job_id = body.get("job_id")
    node_id = body.get("node_id")
    fence_token = body.get("fence_token")
    receipt = body.get("receipt")
    if not job_id or not node_id or fence_token is None or receipt is None:
        raise HTTPException(status_code=400, detail="job_id, node_id, fence_token, and receipt are required")

    mgr = get_queue_manager()
    success = mgr.complete_job(
        job_id=job_id,
        node_id=node_id,
        fence_token=int(fence_token),
        receipt=receipt,
    )
    return JSONResponse({"ok": True, "completed": success})


@worker_router.get("/api/gateway/jobs")
async def gateway_list_jobs(request: Request) -> JSONResponse:
    """List distributed jobs with optional status filter."""
    status = request.query_params.get("status")
    limit_str = request.query_params.get("limit", "50")
    try:
        limit = int(limit_str)
    except ValueError:
        limit = 50

    mgr = get_queue_manager()
    jobs = mgr.list_jobs(status=status, limit=limit)
    return JSONResponse({"ok": True, "total": len(jobs), "jobs": [j.to_dict() for j in jobs]})


@worker_router.get("/api/gateway/jobs/{job_id}")
async def gateway_get_job(job_id: str) -> JSONResponse:
    """Retrieve details and receipt for a single job."""
    mgr = get_queue_manager()
    job = mgr.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"ok": True, "job": job.to_dict()})


@worker_router.post("/api/gateway/workers/register")
async def gateway_register_worker(request: Request) -> JSONResponse:
    """Register or update a distributed compute worker node."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    node_id = body.get("node_id")
    hostname = body.get("hostname")
    if not node_id or not hostname:
        raise HTTPException(status_code=400, detail="node_id and hostname are required")

    mgr = get_queue_manager()
    worker = mgr.register_worker(
        node_id=node_id,
        hostname=hostname,
        ip=body.get("ip", request.client.host if request.client else "127.0.0.1"),
        tags=body.get("tags"),
        version=body.get("version", "0.2.0"),
    )
    return JSONResponse({"ok": True, "worker": worker.to_dict()})


@worker_router.post("/api/gateway/workers/heartbeat")
async def gateway_heartbeat_worker(request: Request) -> JSONResponse:
    """Record a node heartbeat to maintain active status."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    mgr = get_queue_manager()
    success = mgr.heartbeat_worker(node_id)
    return JSONResponse({"ok": True, "heartbeat": success})


@worker_router.get("/api/gateway/workers")
async def gateway_list_workers() -> JSONResponse:
    """List all registered worker nodes in the hypervisor mesh."""
    mgr = get_queue_manager()
    workers = mgr.list_workers()
    return JSONResponse({"ok": True, "total": len(workers), "workers": [w.to_dict() for w in workers]})
