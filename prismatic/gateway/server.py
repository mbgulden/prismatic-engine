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
from fastapi.responses import JSONResponse

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

# ── D.5: In-process observability counters ──────────────────────────
_server_started_at: float | None = None
_webhook_counters: dict[str, int] = {
    "github_received": 0,
    "github_auth_failed": 0,
    "github_published": 0,
    "linear_received": 0,
    "linear_auth_failed": 0,
    "linear_published": 0,
}

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

# Auth check for observability endpoints (re-added 2026-06-30 after Phase D
# cherry-pick conflict dropped it). Reuses the IP allowlist from
# PRISMATIC_ALLOWED_IPS (already in systemd) plus an optional bearer token
# from PRISMATIC_METRICS_TOKEN. If neither is configured, endpoints are
# local-only (rejected unless from 127.0.0.1).
_METRICS_TOKEN = os.environ.get("PRISMATIC_METRICS_TOKEN", "")
_ALLOWED_IPS_RAW = os.environ.get("PRISMATIC_ALLOWED_IPS", "127.0.0.1,::1")
_ALLOWED_IPS = {ip.strip() for ip in _ALLOWED_IPS_RAW.split(",") if ip.strip()}


def _check_observability_auth(request: Request) -> bool:
    """Allow if bearer token matches OR client IP is allowlisted."""
    if _METRICS_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == _METRICS_TOKEN:
            return True
    client_ip = request.client.host if request.client else ""
    if client_ip in _ALLOWED_IPS:
        return True
    return False


@app.middleware("http")
async def _observability_auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/metrics" or path.startswith("/events/") or path.startswith("/curator/"):
        if not _check_observability_auth(request):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)

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
    _server_started_at = _started_at

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
    # NOTE: dispatch consumer is now managed by systemd unit
    # `prismatic-consumer.service` (see Phase D SPOF-2 fix). Do not spawn
    # the in-process consumer here — it's dead-on-arrival because the
    # EventBus singleton is per-process and the subprocess can't see
    # events published by this gateway process.


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


# ── D.5: Observability metrics ──────────────────────────────────

@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Phase D.5 — observability metrics endpoint.

    Returns Prometheus-style plain-text when Accept contains 'text/plain';
    otherwise JSON. Includes event bus stats, webhook counters, and
    uptime. Counters reset on process restart (in-process).
    """
    from prismatic.gateway.event_bus import get_event_bus
    bus = get_event_bus()
    bus_stats = bus.stats
    uptime_s = time.time() - _server_started_at if _server_started_at else 0.0
    return {
        "uptime_seconds": round(uptime_s, 2),
        "event_bus": bus_stats,
        "webhooks": dict(_webhook_counters),
    }


@app.get("/events/recent")
async def events_recent(limit: int = 50) -> dict[str, Any]:
    """Phase D.5 — return recent events from both in-memory history and SQLite bus.

    Useful for debugging what got published, what's in the queue, and what
    the consumer should be draining. Reads from SQLite (durable) rather
    than in-memory ring buffer so the window is wider.
    """
    import sqlite3
    db_path = os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite"
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"), db_path)
    if not os.path.exists(db_path):
        return {"events": [], "count": 0, "source": "sqlite", "note": "bus db not yet created"}
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            cur = conn.execute(
                "SELECT rowid, topic, payload_json, ts, processed "
                "FROM events ORDER BY rowid DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            )
            rows = cur.fetchall()
            events = []
            for row in rows:
                try:
                    payload = json.loads(row[2])
                except Exception:
                    payload = {"_raw": row[2][:200]}
                events.append({
                    "rowid": row[0],
                    "topic": row[1],
                    "ts": row[3],
                    "processed": bool(row[4]),
                    "payload": payload,
                })
            return {"events": events, "count": len(events), "source": "sqlite"}
        finally:
            conn.close()
    except Exception as e:
        return {"events": [], "count": 0, "source": "sqlite", "error": str(e)}


@app.get("/events/bus-stats")
async def events_bus_stats() -> dict[str, Any]:
    """SQLite bus durable stats: total events, processed, oldest, newest."""
    import sqlite3
    db_path = os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite"
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"), db_path)
    if not os.path.exists(db_path):
        return {"exists": False}
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            processed = conn.execute(
                "SELECT COUNT(*) FROM events WHERE processed = 1"
            ).fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(ts) FROM events"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(ts) FROM events"
            ).fetchone()[0]
            return {
                "exists": True,
                "total": total,
                "processed": processed,
                "pending": total - processed,
                "oldest_ts": oldest,
                "newest_ts": newest,
            }
        finally:
            conn.close()
    except Exception as e:
        return {"exists": True, "error": str(e)}


@app.get("/curator/health")
async def curator_health() -> dict[str, Any]:
    """Story 1.7: Curator Lane observability dashboard endpoint.

    Returns curator state: tag distribution, lane stats, recent escalations,
    pool stats, budget usage, and last digest timestamp.

    Used by the morning digest generator + ad-hoc health checks.
    """
    import sqlite3
    curator_db = os.environ.get("PRISMATIC_CURATOR_DB")
    if not curator_db or not os.path.exists(curator_db):
        return {"exists": False, "error": "curator DB not found"}

    # Pool stats via import (graceful if not available)
    pool_stats = None
    try:
        engine_root = os.path.join(
            os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"),
            "work", "prismatic-engine",
        )
        if os.path.isdir(engine_root) and engine_root not in sys.path:
            sys.path.insert(0, engine_root)
        from prismatic.supervisor.recovery import get_pool
        pool_stats = get_pool().stats()
    except Exception as e:
        pool_stats = {"error": str(e)}

    # Budget stats
    budget_path = os.path.expanduser("~/.prismatic/curator/budget.json")
    budget = None
    if os.path.exists(budget_path):
        try:
            import json as _json
            with open(budget_path) as f:
                budget = _json.load(f)
        except Exception:
            pass

    try:
        conn = sqlite3.connect(curator_db, timeout=5)
        try:
            # Tag distribution
            cur = conn.execute(
                "SELECT tag, COUNT(*) FROM tagged_events GROUP BY tag"
            )
            tag_counts = {row[0]: row[1] for row in cur.fetchall()}

            # Last 10 escalations
            cur = conn.execute(
                "SELECT event_rowid, lane_hint, reason, tagged_at "
                "FROM tagged_events WHERE tag = 'escalate' "
                "ORDER BY tagged_at DESC LIMIT 10"
            )
            recent_escalations = [
                {
                    "event_rowid": row[0],
                    "lane_hint": row[1],
                    "reason": row[2],
                    "tagged_at": row[3],
                }
                for row in cur.fetchall()
            ]

            # Last digest
            cur = conn.execute(
                "SELECT date, ran_at, escalate_count, paged_michael, digest_path "
                "FROM digest_runs ORDER BY ran_at DESC LIMIT 1"
            )
            last_digest_row = cur.fetchone()
            last_digest = None
            if last_digest_row:
                last_digest = {
                    "date": last_digest_row[0],
                    "ran_at": last_digest_row[1],
                    "escalate_count": last_digest_row[2],
                    "paged_michael": bool(last_digest_row[3]),
                    "digest_path": last_digest_row[4],
                }
        finally:
            conn.close()
    except Exception as e:
        return {"exists": True, "error": str(e)}

    return {
        "exists": True,
        "tag_counts": tag_counts,
        "total_tagged": sum(tag_counts.values()),
        "recent_escalations": recent_escalations,
        "last_digest": last_digest,
        "pool_stats": pool_stats,
        "budget": budget,
    }


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


# ── Webhook Endpoints (stubs — full implementation in dedicated modules) ──


@app.post("/api/gateway/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    """Receive GitHub webhook events. Verifies HMAC-SHA256 via X-Hub-Signature-256
    and publishes to the in-process event bus.

    Per opus-event-driven-real-plan.md Phase 1.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    _webhook_counters["github_received"] += 1
    if signature:
        import hashlib, hmac as _hmac
        secrets = get_github_secrets()
        if not secrets:
            logger.warning("GitHub webhook skipped: secret not set")
            return {"status": "skipped", "reason": "no-secret"}
        # GitHub HMAC algorithm: hmac_sha256(secret, "x-hub-signature-256:" + body)
        signed_payload = b"x-hub-signature-256:" + body
        # GitHub sends "sha256=<hex>"; compare_digest needs raw hex on both sides.
        sig_hex = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
        expected = None
        for secret in secrets:
            candidate = _hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
            if _hmac.compare_digest(candidate, sig_hex):
                expected = candidate
                break
        if expected is None:
            _webhook_counters["github_auth_failed"] += 1
            from fastapi.responses import JSONResponse
            return JSONResponse({"status": "auth-failed"}, status_code=401)
    try:
        event = json.loads(body) if body else {}
    except Exception:
        event = {"raw": body.decode("utf-8", errors="replace")}
    try:
        from prismatic.gateway.event_bus import get_event_bus
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=event.get("action", "unknown"),
                source="github",
                payload=event,
            )
            logger.info("GitHub webhook published to bus")
            _webhook_counters["github_published"] += 1
    except Exception as e:
        logger.error("GitHub webhook bus publish failed: %s", e)
    return {"status": "ok", "message": "webhook received"}


@app.post("/api/gateway/linear")
async def linear_webhook(request: Request) -> dict[str, Any]:
    """Receive Linear webhook events. Validates HMAC and publishes to bus.

    Per opus-event-driven-real-plan.md Phase 1.
    """
    body = await request.body()
    signature = request.headers.get("linear-signature", "")
    _webhook_counters["linear_received"] += 1
    if signature:
        import hashlib, hmac as _hmac
        secrets = get_linear_secrets()
        if secrets:
            expected = None
            for secret in secrets:
                candidate = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if _hmac.compare_digest(candidate, signature):
                    expected = candidate
                    break
            if expected is None:
                _webhook_counters["linear_auth_failed"] += 1
                from fastapi.responses import JSONResponse
                return JSONResponse({"status": "auth-failed"}, status_code=401)
    try:
        event = json.loads(body) if body else {}
    except Exception:
        event = {"raw": body.decode("utf-8", errors="replace")}
    try:
        from prismatic.gateway.event_bus import get_event_bus
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=event.get("action", "unknown"),
                source="linear",
                payload=event,
            )
            logger.info("Linear webhook published to bus")
            _webhook_counters["linear_published"] += 1
    except Exception as e:
        logger.error("Linear webhook bus publish failed: %s", e)
    return {"status": "ok", "message": "webhook received"}


@app.post("/webhooks/linear")
async def linear_webhook_alias(request: Request) -> dict[str, Any]:
    """Alias for /api/gateway/linear — Linear's OAuth apps store the literal
    webhook URL https://webhooks.growthwebdev.com/webhooks/linear. Without
    this alias, every Linear webhook hits 404 (Jun 30 2026 incident).
    Forward to the same handler.
    """
    return await linear_webhook(request)


# ── Chat AGY Endpoints (v0.1) ──────────────────────────────────────

@app.get("/chat/sessions")
async def list_chat_sessions() -> list[dict[str, Any]]:
    """Get the list of active/known AGY chat sessions."""
    from prismatic.capabilities.chat_agy import ChatAGYCapability
    cap = ChatAGYCapability()
    return cap.list_sessions()


@app.get("/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    """Get a single chat session by ID, or return 404 per v0.1 contract."""
    from prismatic.capabilities.chat_agy import ChatAGYCapability
    from fastapi import HTTPException
    cap = ChatAGYCapability()
    session = cap.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "session_not_found",
                "reason": f"Session '{session_id}' not found under the v0.1 contract (no live data path)."
            }
        )
    return session


# ── Schedule Observatory Endpoints ─────────────────────────────────

@app.get("/schedules")
async def list_schedules() -> list[dict[str, Any]]:
    """List all configured schedules across providers."""
    from prismatic.schedules import get_all_schedules
    return [s.to_dict() for s in get_all_schedules()]


@app.post("/schedules/chat-command")
async def schedules_chat_command(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a chat command to update a schedule."""
    from prismatic.schedules import process_chat_schedule_request
    message = payload.get("message", "")
    return process_chat_schedule_request(message)


@app.post("/schedules/{schedule_id}/mutate")
async def mutate_schedule(schedule_id: str, payload: dict[str, Any]):
    """Mutate a schedule with owner-aware policy check."""
    from prismatic.schedules import request_schedule_mutation, UnauthorizedMutationError
    from fastapi.responses import JSONResponse
    enabled = payload.get("enabled")
    schedule_expr = payload.get("schedule_expr")
    try:
        res = request_schedule_mutation(
            schedule_id=schedule_id,
            enabled=enabled,
            schedule_expr=schedule_expr
        )
        return res
    except UnauthorizedMutationError as e:
        return JSONResponse(status_code=403, content={"error": str(e)})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})




def get_linear_secrets():
    """Read PRIMARY + SECONDARY Linear webhook signing secrets.

    Supports 2-slot rotation: PRIMARY is current, SECONDARY is the previous
    or next secret during rotation. Both are accepted for HMAC verification.
    """
    import os
    seen = set()
    out = []
    for k in ("PRISMATIC_LINEAR_WEBHOOK_SECRET", "PRISMATIC_LINEAR_WEBHOOK_SECRET_SECONDARY",
              "LINEAR_WEBHOOK_SIGNING_SECRET", "LINEAR_WEBHOOK_SIGNING_SECRET_SECONDARY"):
        v = os.environ.get(k, "")
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    if not out:
        env_file = Path(os.environ.get("PRISMATIC_ENV_FILE") or
                        os.path.join(os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"),
                                     ".hermes/profiles/orchestrator/.env"))
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if ("PRISMATIC_LINEAR_WEBHOOK_SECRET" in line or "LINEAR_WEBHOOK_SIGNING_SECRET" in line) and "=" in line:
                    v = line.split("=", 1)[1].strip().strip('\'\"')
                    if v and v not in seen:
                        out.append(v)
                        seen.add(v)
    return out


def get_linear_secret():
    """Legacy single-secret accessor (returns first slot)."""
    secrets = get_linear_secrets()
    return secrets[0] if secrets else ""
def get_github_secrets():
    """Read PRIMARY + SECONDARY GitHub webhook signing secrets.

    Supports 2-slot rotation: PRIMARY is current, SECONDARY is the previous
    or next secret during rotation. Both are accepted for HMAC verification.
    """
    import os
    import re as _re
    seen = set()
    out = []
    for k in ("PRISMATIC_GITHUB_WEBHOOK_SECRET", "PRISMATIC_GITHUB_WEBHOOK_SECRET_SECONDARY"):
        v = os.environ.get(k, "")
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    if not out:
        svc = Path("/etc/systemd/system/prismatic-gateway.service")
        if svc.exists():
            content = svc.read_text()
            for k in ("PRISMATIC_GITHUB_WEBHOOK_SECRET", "PRISMATIC_GITHUB_WEBHOOK_SECRET_SECONDARY"):
                m = _re.search(k + "=(.*)", content)
                if m:
                    v = m.group(1).strip()
                    if v and v not in seen:
                        out.append(v)
                        seen.add(v)
    return out


def get_github_secret():
    """Legacy single-secret accessor (returns first slot)."""
    secrets = get_github_secrets()
    return secrets[0] if secrets else ""
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
