"""
Prismatic Gateway Server — FastAPI application on port 9000 (live) / 9001 (sandbox).

Usage:
    prismatic-gateway                    # Start on port 9000
    prismatic-gateway --port 9001        # Start sandbox server
    prismatic-gateway --reload           # Dev mode with auto-reload
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from prismatic.gateway.webhook_router import router as webhook_router
from prismatic.gateway.grpc_server import serve_grpc
from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.run_records import AgentRunRecordStore

logger = logging.getLogger("prismatic.gateway")

# ── App Factory ─────────────────────────────────────────

app = FastAPI(
    title="Prismatic Engine Gateway",
    description="HTTP/gRPC gateway for the Prismatic Engine orchestration hub",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount webhook router
app.include_router(webhook_router, prefix="/api/gateway")

# ── Startup State ───────────────────────────────────────

_started_at: float = 0.0
_run_store: AgentRunRecordStore | None = None


@app.on_event("startup")
async def startup() -> None:
    global _started_at, _run_store
    _started_at = time.time()
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    store_path = os.path.join(state_dir, "run_records.json")
    _run_store = AgentRunRecordStore(store_path=store_path)
    logger.info(
        "Gateway started at %s, store=%s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_started_at)),
        store_path,
    )


# ── Health / Watchdog ───────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    """Watchdog health check — returns uptime and system status."""
    uptime = time.time() - _started_at
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 2),
        "started_at": _started_at,
    }


# ── Lock State REST API ─────────────────────────────────


@app.get("/locks")
async def list_locks() -> list[dict[str, Any]]:
    """Return all active file locks."""
    return read_swarm_locks()


@app.get("/locks/stale")
async def list_stale_locks() -> list[dict[str, Any]]:
    """Return locks whose heartbeat has expired (>5 min stale)."""
    from prismatic.lock import STALE_TTL_MS

    now_ms = time.time() * 1000
    stale: list[dict[str, Any]] = []
    for lock in read_swarm_locks():
        hb = lock.get("heartbeat_ms", 0)
        if now_ms - hb > STALE_TTL_MS:
            stale.append(lock)
    return stale


@app.get("/locks/{file_path:path}", response_model=None)
async def get_lock(file_path: str):
    """Return lock info for a specific file, or 404 if unlocked."""
    for lock in read_swarm_locks():
        if lock.get("file") == file_path:
            return lock
    return Response(status_code=404, content=json.dumps({"error": "not locked"}), media_type="application/json")


# ── Run Logs REST API ───────────────────────────────────


@app.get("/runs")
async def list_runs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent agent run records, optionally filtered by status."""
    if _run_store is None:
        return []
    records = _run_store.get_recent_runs(limit=limit)
    if status:
        records = [r for r in records if r.status == status]
    return [{"run_id": r.run_id, "issue_id": r.issue_id, "agent_name": r.agent_name, "status": r.status, "started_at": r.started_at, "completed_at": r.completed_at, "output_path": r.output_path, "error_message": r.error_message} for r in records]


@app.get("/runs/{run_id}", response_model=None)
async def get_run(run_id: str):
    """Return a single run record by ID, or 404."""
    if _run_store is None:
        return Response(status_code=500, content=json.dumps({"error": "store not initialized"}), media_type="application/json")
    record = _run_store.get_run(run_id)
    if record is None:
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")
    return {"run_id": record.run_id, "issue_id": record.issue_id, "agent_name": record.agent_name, "status": record.status, "started_at": record.started_at, "completed_at": record.completed_at, "output_path": record.output_path, "error_message": record.error_message}


@app.post("/runs")
async def create_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new agent run record."""
    if _run_store is None:
        return {"error": "store not initialized"}
    run_id = _run_store.create_run(
        issue_id=payload.get("issue_id", ""),
        agent_name=payload.get("agent_name", "unknown"),
    )
    return {"run_id": run_id, "status": "created"}


@app.post("/runs/{run_id}/complete", response_model=None)
async def complete_run(run_id: str, payload: dict[str, Any]):
    """Mark a run as completed or failed."""
    if _run_store is None:
        return Response(status_code=500, content=json.dumps({"error": "store not initialized"}), media_type="application/json")
    record = _run_store.get_run(run_id)
    if record is None:
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")
    _run_store.update_run(
        run_id,
        status=payload.get("status", "completed"),
        output_path=payload.get("output_path"),
        error=payload.get("error_message"),
    )
    return {"run_id": run_id, "status": payload.get("status", "completed")}


# ── CLI Entry Point ─────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Prismatic Engine Gateway Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PRISMATIC_PORT", "9000")), help="Port to bind HTTP")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    parser.add_argument("--grpc", action="store_true", help="Also start gRPC server")
    parser.add_argument("--grpc-port", type=int, default=int(os.environ.get("PRISMATIC_GRPC_PORT", "9002")), help="gRPC server port")
    parser.add_argument("--log-level", type=str, default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting Prismatic Gateway on %s:%d (reload=%s, grpc=%s)", args.host, args.port, args.reload, args.grpc)

    if args.grpc:
        # Start gRPC in background thread alongside uvicorn
        _run_store_local = _create_run_store()
        grpc_thread = threading.Thread(
            target=_run_grpc_loop,
            args=(args.grpc_port, _run_store_local),
            daemon=True,
        )
        grpc_thread.start()
        logger.info("gRPC server thread started on port %d", args.grpc_port)

    uvicorn.run(
        "prismatic.gateway.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


def _create_run_store() -> AgentRunRecordStore:
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    store_path = os.path.join(state_dir, "run_records.json")
    return AgentRunRecordStore(store_path=store_path)


def _run_grpc_loop(port: int, store: AgentRunRecordStore) -> None:
    """Run the gRPC server in a dedicated event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        server = loop.run_until_complete(serve_grpc(port, store))
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
