"""
prismatic/gateway/server.py — Prismatic Engine Gateway Server

FastAPI/uvicorn gateway that wires together:
    - EventBus (async pub/sub)
    - IPC bridge (Unix socket + HTTP event ingest)
    - WebSocket broadcaster (real-time event streaming for dashboards)
    - Lock management API
    - Agent run records API
    - Health check

Integration:
    - prismatic/lock.py — pushes lock/unlock/heartbeat events via Unix socket
    - prismatic/dispatcher.py — pushes agent lifecycle events via Unix socket
    - Dashboard clients — connect via WebSocket for real-time event streaming
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from prismatic.gateway.event_bus import get_event_bus, set_event_bus, EventBus
from prismatic.gateway.ipc_bridge import (
    UnixSocketListener,
    create_event_ingest_route,
    DEFAULT_SOCKET_PATH,
)
from prismatic.gateway.ws_broadcaster import (
    start_ws_broadcaster,
    stop_ws_broadcaster,
)
from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.run_records import AgentRunRecordStore

logger = logging.getLogger("prismatic.gateway.server")

# ── FastAPI Application ──────────────────────────────────────────────

app = FastAPI(
    title="Prismatic Engine Gateway",
    description="HTTP/gRPC gateway for the Prismatic Engine orchestration hub",
    version="0.1.0",
    openapi_url=None,  # Disable OpenAPI schema generation — internal gateway
)

# CORS — allow all origins (internal orchestration gateway)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the IPC bridge event ingest route (POST /events, GET /events/history)
# The router's @router.post("/events") defines the full path — no prefix needed
app.include_router(create_event_ingest_route())

# ── Startup timestamp ──────────────────────────────────────────────
_started_at: float = 0.0

# ── Global state ───────────────────────────────────────────────────
_run_store: AgentRunRecordStore | None = None
_slack_bot: Any = None  # placeholder for future Slack integration
_ipc_listener: UnixSocketListener | None = None
_ws_clients: set[WebSocket] = set()


# ── Lifecycle Events ──────────────────────────────────────────────


@app.on_event("startup")
async def startup() -> None:
    """Initialize EventBus, IPC bridge, WebSocket broadcaster, and store."""
    global _started_at, _run_store, _ipc_listener

    _started_at = time.time()

    # Initialize EventBus (ensure singleton)
    bus = get_event_bus()

    # Start IPC bridge Unix socket listener
    _ipc_listener = UnixSocketListener()
    await _ipc_listener.start()

    # Start WebSocket broadcaster (daemon thread with its own event loop)
    start_ws_broadcaster()

    # Initialize run records store
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    store_path = os.path.join(state_dir, "run_records.json")
    _run_store = AgentRunRecordStore(store_path)

    logger.info(
        "Gateway started at %s, store=%s, ipc=%s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        store_path,
        _ipc_listener.socket_path,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop the IPC bridge listener on gateway shutdown."""
    global _ipc_listener

    if _ipc_listener:
        await _ipc_listener.stop()
        _ipc_listener = None

    stop_ws_broadcaster()

    logger.info("Gateway shutdown complete")


# ── Health ──────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    """Watchdog health check — returns uptime and system status."""
    uptime = time.time() - _started_at
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "started_at": _started_at,
    }


# ── WebSocket Endpoint ──────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time swarm event streaming.

    Clients connect to receive live telemetry: lock/unlock events,
    agent lifecycle transitions, governor allocations, and circuit
    breaker state changes.

    The connection stays open until the client disconnects.
    Events are broadcast to all connected clients.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected (total=%d)", len(_ws_clients))

    # Send initial connection metadata
    connect_msg: dict[str, Any] = {
        "type": "connected",
        "source": "gateway",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "payload": {"client_count": len(_ws_clients), "recent_events": []},
    }
    try:
        await websocket.send_json(connect_msg)
    except Exception:
        pass

    try:
        while True:
            data = await websocket.receive_text()
            # Ping/pong for keepalive
            if data.strip().lower() == "ping":
                pong: dict[str, Any] = {
                    "type": "pong",
                    "source": "gateway",
                    "payload": {"clients": len(_ws_clients)},
                }
                await websocket.send_json(pong)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (total=%d)", len(_ws_clients))
    except Exception:
        logger.warning("WebSocket error", exc_info=True)
    finally:
        _ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (total=%d)", len(_ws_clients))


# ── Lock Management API ─────────────────────────────────────────────


@app.get("/locks")
async def list_locks() -> list[dict[str, Any]]:
    """Return all active file locks."""
    return read_swarm_locks()


@app.get("/locks/stale")
async def list_stale_locks() -> list[dict[str, Any]]:
    """Return locks whose heartbeat has expired (>5 min stale)."""
    STALE_TTL_MS = 300_000  # 5 minutes
    now_ms = int(time.time() * 1000)
    locks = read_swarm_locks()
    stale = []
    for lock in locks:
        hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
        if now_ms - hb > STALE_TTL_MS:
            lock["stale_ms"] = now_ms - hb
            stale.append(lock)
    return stale


@app.get("/locks/{file_path:path}")
async def get_lock(file_path: str) -> Response:
    """Return lock info for a specific file, or 404 if unlocked."""
    for lock in read_swarm_locks():
        if lock.get("filePath") == file_path:
            return Response(
                content=json.dumps(lock),
                media_type="application/json",
            )
    return Response(
        status_code=404,
        content=json.dumps({"error": "not locked"}),
        media_type="application/json",
    )


# ── Agent Run Records API ────────────────────────────────────────────


@app.get("/runs")
async def list_runs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent agent run records, optionally filtered by status."""
    if _run_store is None:
        return []
    records = _run_store.get_recent_runs(limit=limit)
    result = []
    for r in records:
        d = {
            "run_id": r.run_id,
            "issue_id": r.issue_id,
            "agent_name": r.agent_name,
            "status": r.status,
            "started_at": r.started_at,
            "completed_at": r.completed_at,
            "output_path": r.output_path,
            "error_message": r.error_message,
        }
        if status is None or r.status == status:
            result.append(d)
    return result


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> Response:
    """Return a single run record by ID, or 404."""
    if _run_store is None:
        return Response(
            status_code=500,
            content=json.dumps({"error": "store not initialized"}),
            media_type="application/json",
        )
    record = _run_store.get_run(run_id)
    if record is None:
        return Response(
            status_code=404,
            content=json.dumps({"error": "not found"}),
            media_type="application/json",
        )
    return Response(
        content=json.dumps({
            "run_id": record.run_id,
            "issue_id": record.issue_id,
            "agent_name": record.agent_name,
            "status": record.status,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "output_path": record.output_path,
            "error_message": record.error_message,
        }),
        media_type="application/json",
    )


@app.post("/runs/{run_id}/complete")
async def complete_run(run_id: str, payload: dict[str, Any] | None = None) -> Response:
    """Mark a run as completed or failed."""
    if _run_store is None:
        return Response(
            status_code=500,
            content=json.dumps({"error": "store not initialized"}),
            media_type="application/json",
        )
    record = _run_store.get_run(run_id)
    if record is None:
        return Response(
            status_code=404,
            content=json.dumps({"error": "not found"}),
            media_type="application/json",
        )
    status = (payload or {}).get("status", "completed")
    _run_store.update_run(run_id, status=status)
    return {"status": "ok"}


# ── Schedule Observatory API ──────────────────────────────────────────

@app.get("/schedules")
async def list_schedules() -> list[dict[str, Any]]:
    """Return all observed schedules across providers."""
    from prismatic.schedules import get_all_schedules
    return [s.to_dict() for s in get_all_schedules()]


@app.post("/schedules/{schedule_id:path}/mutate")
async def mutate_schedule(schedule_id: str, payload: dict[str, Any] = None) -> Response:
    """Mutate a schedule with owner-aware validation policy checks."""
    from prismatic.schedules import request_schedule_mutation, UnauthorizedMutationError
    
    payload = payload or {}
    enabled = payload.get("enabled")
    schedule_expr = payload.get("schedule_expr")
    
    try:
        result = request_schedule_mutation(
            schedule_id=schedule_id,
            enabled=enabled,
            schedule_expr=schedule_expr
        )
        # Emit schedule.updated event
        from prismatic.gateway.event_bus import get_event_bus
        bus = get_event_bus()
        event = {
            "event_id": f"event-{int(time.time())}",
            "event_type": "schedule.updated",
            "schedule_id": schedule_id,
            "owner": schedule_id.split(":")[0],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": {
                "updates": {"enabled": enabled, "schedule_expr": schedule_expr},
                "result": result
            }
        }
        # Push event to EventBus if running
        try:
            bus.publish(event)
        except Exception:
            pass
            
        return Response(
            content=json.dumps(result),
            media_type="application/json"
        )
    except UnauthorizedMutationError as e:
        return Response(
            status_code=403,
            content=json.dumps({"error": str(e)}),
            media_type="application/json"
        )
    except FileNotFoundError as e:
        return Response(
            status_code=404,
            content=json.dumps({"error": str(e)}),
            media_type="application/json"
        )
    except Exception as e:
        return Response(
            status_code=500,
            content=json.dumps({"error": str(e)}),
            media_type="application/json"
        )


@app.post("/schedules/chat-command")
async def handle_chat_schedule_command(payload: dict[str, Any]) -> Response:
    """Process a natural language instruction to change a schedule."""
    from prismatic.schedules import process_chat_schedule_request
    message = payload.get("message", "")
    res = process_chat_schedule_request(message)
    if res["success"]:
        # Emit schedule.updated event for simulation
        try:
            from prismatic.gateway.event_bus import get_event_bus
            bus = get_event_bus()
            event = {
                "event_id": f"event-{int(time.time())}",
                "event_type": "schedule.updated",
                "schedule_id": res["schedule_id"],
                "owner": res["schedule_id"].split(":")[0],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "payload": {
                    "updates": res["updates"],
                    "note": "Triggered via conversational chat command"
                }
            }
            bus.publish(event)
        except Exception:
            pass
        return Response(
            content=json.dumps(res),
            media_type="application/json"
        )
    else:
        return Response(
            status_code=400,
            content=json.dumps(res),
            media_type="application/json"
        )



# ── Webhook Endpoints (stubs — full implementation in dedicated modules) ──


@app.post("/api/gateway/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    """Receive GitHub webhook events (PR opened, synchronized, review submitted)."""
    body = await request.body()
    logger.info("GitHub webhook received (%d bytes)", len(body))
    return {"status": "ok", "message": "webhook received"}


@app.post("/api/gateway/linear")
async def linear_webhook(request: Request) -> dict[str, Any]:
    """Receive Linear webhook events (issue status changes, comments)."""
    body = await request.body()
    logger.info("Linear webhook received (%d bytes)", len(body))
    return {"status": "ok", "message": "webhook received"}


# ── CLI Entry Point ──────────────────────────────────────────────────


def _create_run_store() -> AgentRunRecordStore | None:
    """Initialize run store (used by gRPC server)."""
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    store_path = os.path.join(state_dir, "run_records.json")
    return AgentRunRecordStore(store_path)


def main() -> None:
    """CLI entry point — start the Prismatic Gateway server."""
    parser = argparse.ArgumentParser(description="Prismatic Engine Gateway Server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRISMATIC_PORT", 9000)),
        help="Port to bind HTTP",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable hot reload (development only)",
    )
    parser.add_argument(
        "--grpc",
        action="store_true",
        help="Enable gRPC server alongside HTTP",
    )
    parser.add_argument(
        "--grpc-port",
        type=int,
        default=9002,
        help="Port for gRPC server",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(
        "Starting Prismatic Gateway on %s:%d (reload=%s, grpc=%s)",
        args.host,
        args.port,
        args.reload,
        args.grpc,
    )

    # Start gRPC in background thread if enabled
    grpc_thread: threading.Thread | None = None
    if args.grpc:
        def _run_grpc_loop(port: int) -> None:
            """Run the gRPC server in a dedicated event loop."""
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from prismatic.gateway.grpc_server import serve_grpc
                loop.run_until_complete(serve_grpc(port=port))
            except KeyboardInterrupt:
                pass
            finally:
                loop.close()

        grpc_thread = threading.Thread(
            target=_run_grpc_loop,
            args=(args.grpc_port,),
            daemon=True,
        )
        grpc_thread.start()
        logger.info("gRPC server starting on port %d", args.grpc_port)

    # Start FastAPI/uvicorn
    uvicorn.run(
        "prismatic.gateway.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
