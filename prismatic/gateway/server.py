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

# ── FastAPI Application ──────────────────────────────────────────────

# Body size limit (DoS protection). Linear webhook events are typically <10KB.
# Cap at 1MB to allow for batched events but reject truly abusive payloads.
MAX_BODY_BYTES = int(os.environ.get("PRISMATIC_MAX_BODY_BYTES", str(1024 * 1024)))

# IP allowlist for non-webhook endpoints. The webhook endpoints (Linear, GitHub)
# authenticate via HMAC; everything else should only accept connections from
# localhost or explicitly allowed IPs. Comma-separated CIDR list, default
# localhost-only. Also includes 'testclient' for in-process TestClient runs.
PRISMATIC_ALLOWED_IPS = frozenset(
    ip.strip() for ip in os.environ.get(
        "PRISMATIC_ALLOWED_IPS", "127.0.0.1,::1,testclient"
    ).split(",")
    if ip.strip()
)

# Replay protection window (seconds). Linear webhook signatures don't include
# a timestamp by default, so we accept a window relative to a server-side
# "epoch" — fresh enough to allow clock skew between Linear and us.
WEBHOOK_REPLAY_WINDOW_SECONDS = int(os.environ.get("PRISMATIC_WEBHOOK_REPLAY_WINDOW", "300"))

app = FastAPI(
    title="Prismatic Engine Gateway",
    description="HTTP/gRPC gateway for the Prismatic Engine orchestration hub",
    version="0.1.0",
    openapi_url=None,  # Disable OpenAPI schema generation — internal gateway
)


# ── Body-size middleware (DoS protection) ────────────────────────────
# Request-ID propagation. Generates a UUID4 if X-Request-ID is absent,
# echoes it back in the response. Used by audit logs to correlate entries
# from a single inbound request.
import uuid as _uuid


@app.middleware("http")
async def request_id_propagation(request: Request, call_next):
    """Read X-Request-ID from headers or generate one; echo back in response.

    Allows callers to pass their own request ID for cross-system tracing.
    Default fallback: UUID4 (16 bytes of randomness).
    """
    request_id = request.headers.get("X-Request-ID", "")
    if not request_id:
        request_id = str(_uuid.uuid4())
    # Stash on request.state so handlers can access via request.state.request_id
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Per-IP rate limit (sliding window). In-memory only — sufficient for a single
# instance. Multi-instance deployments would need Redis or similar.
# Default: 60 requests / 60s / IP. Configurable.
import time as _time
_RATE_LIMIT_WINDOW = int(os.environ.get("PRISMATIC_RATE_LIMIT_WINDOW", "60"))
_RATE_LIMIT_MAX = int(os.environ.get("PRISMATIC_RATE_LIMIT_MAX", "60"))
_rate_limit_buckets: dict[str, list[float]] = {}


@app.middleware("http")
async def rate_limit_per_ip(request: Request, call_next):
    """Sliding-window per-IP rate limit.

    Returns 429 with Retry-After header when an IP exceeds RATE_LIMIT_MAX
    requests in the last RATE_LIMIT_WINDOW seconds.

    State is in-memory; multi-instance gateways would need a shared store.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    bucket = _rate_limit_buckets.setdefault(client_ip, [])
    # Drop entries older than the window
    while bucket and bucket[0] < now - _RATE_LIMIT_WINDOW:
        bucket.pop(0)
    if len(bucket) >= _RATE_LIMIT_MAX:
        retry_after = int(bucket[0] + _RATE_LIMIT_WINDOW - now) + 1
        return JSONResponse(
            {"status": "rejected", "reason": "rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    # Periodically clean up empty buckets to prevent unbounded growth.
    if len(_rate_limit_buckets) > 1000 and now % 100 < 1:
        empty = [k for k, v in _rate_limit_buckets.items() if not v]
        for k in empty:
            _rate_limit_buckets.pop(k, None)
    return await call_next(request)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject requests with bodies larger than MAX_BODY_BYTES.

    Linear webhook events are typically <10KB. We cap at 1MB to allow batched
    events but reject abusive payloads.
    """
    if request.method in {"POST", "PUT", "PATCH"}:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return JSONResponse(
                {"status": "rejected", "reason": "payload too large"},
                status_code=413,
            )
    return await call_next(request)


# ── IP allowlist middleware (defense-in-depth for non-webhook endpoints) ──
@app.middleware("http")
async def ip_allowlist(request: Request, call_next):
    """Restrict non-webhook endpoints to localhost (or PRISMATIC_ALLOWED_IPS).

    Webhook endpoints (paths starting with /api/gateway/) authenticate via HMAC
    and are exempt from IP filtering. Everything else (locks, runs, schedules,
    chat, websocket) requires the connection to come from a trusted IP.
    """
    path = request.url.path
    if path.startswith("/api/gateway/") or path == "/health":
        # Webhook endpoints use HMAC; health is intentionally public.
        return await call_next(request)
    client_ip = request.client.host if request.client else ""
    if client_ip not in PRISMATIC_ALLOWED_IPS:
        return JSONResponse(
            {"status": "rejected", "reason": "ip not allowed"},
            status_code=403,
        )
    return await call_next(request)

# CORS — explicit origins only. Default is none (no browser clients). Set
# PRISMATIC_CORS_ORIGINS to a comma-separated list when browser dashboards
# need to access the gateway (e.g. "https://app.growthwebdev.com").
_prismatic_origins = [
    o.strip() for o in os.environ.get("PRISMATIC_CORS_ORIGINS", "").split(",")
    if o.strip()
]
if _prismatic_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_prismatic_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Linear-Signature", "X-Hub-Signature-256"],
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
# SECURITY NOTE: /ws accepts any client that passes the IP allowlist
# middleware (default localhost). There is no per-client authentication —
# any connected client receives ALL broadcast events (lock/unlock, agent
# lifecycle, etc.). For multi-instance or externally-reachable gateways,
# this is an information disclosure risk. Mitigation options:
#   - require a bearer token in the WebSocket upgrade headers
#   - require a per-session HMAC challenge
#   - rate-limit connections per IP
# Tracked as Tier 7 follow-up (GRO-2058).


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





def _audit_webhook(
    source: str,
    outcome: str,
    *,
    reason: str | None = None,
    identifier: str | None = None,
    event_type: str | None = None,
    repo: str | None = None,
    request_id: str | None = None,
) -> None:
    """Append a row to the webhook audit log.

    Schema (one JSON record per line):
      {ts, source, outcome, reason?, identifier?, event_type?, repo?,
       request_id?}

    Lives at $PRISMATIC_STATE_DIR/webhook_audit.log. Append-only.
    Used for forensics and rate-of-anomaly detection.

    request_id correlates all audit entries from a single inbound request.
    """
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "source": source,
        "outcome": outcome,
    }
    if reason is not None:
        record["reason"] = reason
    if identifier is not None:
        record["identifier"] = identifier
    if event_type is not None:
        record["event_type"] = event_type
    if repo is not None:
        record["repo"] = repo
    if request_id is not None:
        record["request_id"] = request_id
    try:
        with (state_dir / "webhook_audit.log").open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Audit must never break the webhook path. Log and continue.
        logger.warning("audit log write failed: %s", exc)


@app.post("/api/gateway/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    """Receive GitHub webhook events (PR opened, synchronized, review submitted).

    HMAC validation against PRISMATIC_GITHUB_WEBHOOK_SECRET. Header is
    X-Hub-Signature-256 (sha256=<hex>) per GitHub's spec.

    Dual-secret rotation supported (PRISMATIC_GITHUB_WEBHOOK_SECRET_NEXT).

    If the secret is unset, HMAC is skipped (dev only — never deploy this way).
    """
    import hmac
    import hashlib

    body = await request.body()
    request_id = getattr(request.state, "request_id", None)
    primary_secret = os.environ.get("PRISMATIC_GITHUB_WEBHOOK_SECRET", "")
    next_secret = os.environ.get("PRISMATIC_GITHUB_WEBHOOK_SECRET_NEXT", "")
    if primary_secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            _audit_webhook(
                "github", "rejected", reason="missing signature header",
                request_id=request_id,
            )
            return JSONResponse(
                {"status": "rejected", "reason": "missing X-Hub-Signature-256"},
                status_code=401,
            )
        sig = sig_header[len("sha256="):]
        # Compute expected for primary and (if set) next secret.
        expected_primary = hmac.new(
            primary_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        matches_primary = hmac.compare_digest(sig, expected_primary)
        matches_next = False
        if next_secret:
            expected_next = hmac.new(
                next_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            matches_next = hmac.compare_digest(sig, expected_next)
        if not (matches_primary or matches_next):
            _audit_webhook(
                "github", "rejected", reason="bad signature",
                request_id=request_id,
            )
            return JSONResponse(
                {"status": "rejected", "reason": "bad signature"},
                status_code=401,
            )

    # Parse + log + queue (mirror of Linear handler).
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        _audit_webhook(
            "github", "rejected", reason="bad json", request_id=request_id,
        )
        return JSONResponse({"status": "rejected", "reason": "bad json"}, status_code=400)

    event_type = event.get("action", "") or event.get("ref", "")
    repo = (event.get("repository") or {}).get("full_name", "unknown")
    _audit_webhook(
        "github", "received", repo=repo, event_type=str(event_type)[:64],
        request_id=request_id,
    )
    return {"status": "ok", "message": "webhook received", "event_type": event_type}


@app.post("/api/gateway/linear")
async def linear_webhook(request: Request) -> dict[str, Any]:
    """Receive Linear webhook events (issue status changes, comments).

    Production-grade handler (Tier 6 + Tier 7 hardening):

    1. HMAC validation against PRISMATIC_LINEAR_WEBHOOK_SECRET (constant-time compare).
    2. Body size capped at MAX_BODY_BYTES (DoS protection, enforced upstream).
    3. JSON parse with explicit error.
    4. Event-driven dispatch for Issue events with agent:* labels (GRO-2048).
       Uses single-issue dispatch helper to avoid full-cycle amplification
       (each webhook event was triggering ~20 Linear API calls).
    5. SQLite catch-up queue for non-matching events (GRO-2050).
    6. Audit log appended for every outcome (success, rejection, queued, error).
    7. No stack traces leak in responses (all errors return sanitized JSON).

    Configuration:
    - PRISMATIC_LINEAR_WEBHOOK_SECRET: HMAC secret. If unset, HMAC is
      skipped (dev only — never deploy this way).
    """
    import hmac
    import hashlib
    import sqlite3

    body = await request.body()
    body_size = len(body)

    # ── 1. HMAC validation (with dual-secret rotation support) ─────
    # Supports zero-downtime rotation: PRISMATIC_LINEAR_WEBHOOK_SECRET
    # is the primary, _NEXT is a candidate during rotation. Both are
    # accepted during the rotation window. After Linear is confirmed to
    # use the new secret, swap roles (rename _NEXT → primary).
    request_id = getattr(request.state, "request_id", None)
    primary_secret = os.environ.get("PRISMATIC_LINEAR_WEBHOOK_SECRET", "")
    next_secret = os.environ.get("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", "")
    if primary_secret:
        sig = request.headers.get("Linear-Signature", "")
        if not sig:
            _audit_webhook(
                "linear", "rejected", reason="missing signature header",
                request_id=request_id,
            )
            return JSONResponse(
                {"status": "rejected", "reason": "missing Linear-Signature header"},
                status_code=401,
            )
        # Compute expected signatures for primary and (if set) next secret.
        # Compare against each. Accept if either matches.
        expected_primary = hmac.new(
            primary_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        matches_primary = hmac.compare_digest(sig, expected_primary)
        matches_next = False
        if next_secret:
            expected_next = hmac.new(
                next_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            matches_next = hmac.compare_digest(sig, expected_next)
        if not (matches_primary or matches_next):
            _audit_webhook("linear", "rejected", reason="bad signature", request_id=request_id)
            return JSONResponse(
                {"status": "rejected", "reason": "bad signature"},
                status_code=401,
            )

    # ── 2. JSON parse ────────────────────────────────────────────────
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        _audit_webhook("linear", "rejected", reason="bad json", request_id=request_id)
        return JSONResponse(
            {"status": "rejected", "reason": "bad json"},
            status_code=400,
        )

    # ── 2b. Replay protection (Tier 7 hardening, GRO-2062) ─────────
    # Linear's HMAC signature doesn't include a timestamp, so a captured
    # payload+signature could be replayed indefinitely. We use the
    # event's `createdAt` field (set by Linear at event-generation time)
    # to enforce a freshness window. This catches:
    #   - replay attacks where someone captures a valid payload and POSTs it later
    #   - stale events from replayed infrastructure
    # Configurable via PRISMATIC_WEBHOOK_REPLAY_WINDOW (default 300s = 5min).
    created_at_str = event.get("createdAt", "")
    if created_at_str:
        from datetime import datetime, timezone
        try:
            # Linear uses ISO 8601 with 'Z' suffix
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_seconds = (now - created_at).total_seconds()
            if age_seconds > WEBHOOK_REPLAY_WINDOW_SECONDS:
                _audit_webhook(
                    "linear", "rejected",
                    reason=f"stale: {int(age_seconds)}s old (max {WEBHOOK_REPLAY_WINDOW_SECONDS}s)",
                    request_id=request_id,
                )
                return JSONResponse(
                    {
                        "status": "rejected",
                        "reason": f"event too old: {int(age_seconds)}s (max {WEBHOOK_REPLAY_WINDOW_SECONDS}s)",
                    },
                    status_code=401,
                )
            # Also reject events from the future (clock skew attacks).
            if age_seconds < -60:  # allow 60s skew
                _audit_webhook(
                    "linear", "rejected",
                    reason=f"future event: {-int(age_seconds)}s in future",
                    request_id=request_id,
                )
                return JSONResponse(
                    {"status": "rejected", "reason": "event timestamp in the future"},
                    status_code=401,
                )
        except ValueError:
            # If createdAt is unparseable, log and continue. Don't reject
            # since older Linear events might not have createdAt.
            logger.warning("could not parse createdAt: %r", created_at_str)
    else:
        # No createdAt in payload — likely an older Linear event. Log warning.
        logger.warning("webhook payload missing createdAt — replay protection not enforced")

    # ── 3. Extract event metadata ───────────────────────────────────
    event_type = event.get("type", "")
    action = event.get("action", "")
    data = event.get("data", {})
    identifier = data.get("identifier", "")
    labels = [l.get("name", "") for l in data.get("labels", []) if isinstance(l, dict)]

    logger.info(
        "Linear webhook: type=%s action=%s issue=%s labels=%s body_size=%d",
        event_type, action, identifier, labels, body_size,
    )

    # ── 4. Event-driven dispatch (single-issue) ─────────────────────
    has_agent_label = any(l.startswith("agent:") for l in labels)
    if event_type == "Issue" and action in ("update", "create") and identifier and has_agent_label:
        # Single-issue dispatch (replaces dispatch_once full cycle).
        # The full cycle was wasteful: a single webhook event was triggering
        # ~20 Linear API calls. Single-issue dispatch uses ~1-2 calls.
        try:
            from ..dispatcher import dispatch_issue_by_identifier
            result = dispatch_issue_by_identifier(identifier=identifier)
            outcome = "dispatched" if result else "dispatch_no_op"
            _audit_webhook(
                "linear", outcome, identifier=identifier, event_type=event_type,
                request_id=request_id,
            )
            return {
                "status": outcome,
                "identifier": identifier,
                "result": result,
            }
        except Exception as exc:  # noqa: BLE001 — never let webhook 500
            # Sanitized: no stack trace in response.
            logger.exception("dispatch failed for %s: %s", identifier, exc)
            _audit_webhook(
                "linear", "dispatch_failed",
                identifier=identifier, event_type=event_type,
                reason=str(exc)[:200], request_id=request_id,
            )
            # Fall through to queue-on-failure below.

    # ── 5. Queue to SQLite catch-up table ────────────────────────────
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "linear_webhook_queue.db"
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS linear_webhook_queue (
                    event_id TEXT PRIMARY KEY,
                    identifier TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    dispatch_status TEXT NOT NULL DEFAULT 'pending'
                )"""
            )
            event_id = hashlib.sha256(body).hexdigest()
            # INSERT OR IGNORE — same body twice still only one queue row.
            conn.execute(
                """INSERT OR IGNORE INTO linear_webhook_queue
                   (event_id, identifier, event_type, action, received_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    identifier or "",
                    event_type,
                    action,
                    time.time(),
                    body.decode("utf-8", errors="replace"),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        # Sanitized: don't leak filesystem paths in response.
        logger.exception("webhook queue write failed: %s", exc)
        _audit_webhook(
            "linear", "queue_failed",
            identifier=identifier, event_type=event_type,
            reason="queue write failed",
        )
        return JSONResponse(
            {"status": "error", "reason": "queue write failed"},
            status_code=500,
        )

    # ── 6. Audit log + response ─────────────────────────────────────
    _audit_webhook(
        "linear", "queued", identifier=identifier, event_type=event_type,
        request_id=request_id,
    )
    return {"status": "queued", "identifier": identifier or "(unknown)"}


# ── CLI Entry Point ──────────────────────────────────────────────────


# ── Chat Capability API (GRO-1955) ────────────────────────────────────
# Read-only gateway endpoints for the chat.agy capability. The contract
# is intentionally narrow: a list endpoint and a get-by-id endpoint.
# Mutation paths (send, follow-up, transcript retrieval) are a
# separate GRO-1955 follow-up issue. Adding a mutation endpoint here
# would scope-creep; the additive half of GRO-1955 is read-only.

@app.get("/chat/sessions")
async def list_chat_sessions() -> list[dict[str, Any]]:
    """Return the list of known AGY chat sessions.

    v0.1 contract: returns an empty list until the AGY live data path
    is wired. The wrapper uses the ``ChatAGYCapability`` so swapping
    in the real adapter later requires no gateway change.
    """
    from prismatic.capabilities import ChatAGYCapability

    cap = ChatAGYCapability()
    return cap.list_sessions()


@app.get("/chat/sessions/{session_id:path}")
async def get_chat_session(session_id: str) -> dict[str, Any]:
    """Return a single AGY chat session by id, or 404 if not found.

    v0.1 contract: returns 404 for any id (no live data yet). When the
    live path is wired, this endpoint will return the typed session
    shape or a 404 with an honest "no live data" reason.
    """
    from fastapi import HTTPException
    from prismatic.capabilities import ChatAGYCapability

    cap = ChatAGYCapability()
    session = cap.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "session_not_found",
                "session_id": session_id,
                "reason": "chat.agy live data path not yet wired (v0.1 returns empty list).",
            },
        )
    return session


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
