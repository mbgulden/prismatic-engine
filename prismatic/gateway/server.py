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
from datetime import datetime, timezone
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


# ── Quota API (Google AI Ultra subscription tracking) ────────────────
# Wraps the orchestrator's agy_quota_state module. Used by the /quota UI
# to display live quota + history + throttle state. The supervisor also
# calls can_dispatch() before launching to refuse dispatches below pause.
#
# Auth: this endpoint exposes model quota and reset times but NOT the OAuth
# token. The token is loaded by the server-side agy_quota module. Auth gate
# (Google sign-in) is at the UI layer; the API itself is local-only.


@app.get("/api/quota")
async def get_quota_current() -> dict[str, Any]:
    """Current quota state (most recent snapshot per model) + thresholds + can_dispatch flags."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts"))
        from agy_quota_state import QuotaState  # type: ignore
        state = QuotaState()
        return {
            "current": state.get_current(),
            "thresholds": state.config.list_thresholds(),
            "recent_events_24h": state.get_recent_events(hours=24),
        }
    except Exception as exc:
        logger.warning("quota fetch failed: %s", exc)
        return {"error": str(exc), "current": [], "thresholds": [], "recent_events_24h": []}


@app.get("/api/quota/history")
async def get_quota_history(model: str = "", hours: float = 24.0) -> dict[str, Any]:
    """Quota history for one model (or all models) over the given hours."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts"))
        from agy_quota_state import QuotaState  # type: ignore
        state = QuotaState()
        if model:
            history = state.get_history(model, hours)
            return {"model": model, "hours": hours, "history": history}
        # No model: return summary per model
        current = state.get_current()
        out = {}
        for m in current:
            hist = state.get_history(m["model"], hours)
            pcts = [h["remaining_pct"] for h in hist if h["remaining_pct"] is not None]
            if pcts:
                out[m["model"]] = {
                    "snapshots": len(hist),
                    "min": min(pcts),
                    "max": max(pcts),
                    "avg": round(sum(pcts) / len(pcts), 1),
                    "current": m["remaining_pct"],
                }
        return {"hours": hours, "summary": out}
    except Exception as exc:
        logger.warning("quota history fetch failed: %s", exc)
        return {"error": str(exc), "history": []}


@app.get("/api/quota/thresholds")
async def get_quota_thresholds() -> dict[str, Any]:
    """List all per-model thresholds (defaults + user-set)."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts"))
        from agy_quota_state import QuotaConfig  # type: ignore
        return {"thresholds": QuotaConfig().list_thresholds()}
    except Exception as exc:
        logger.warning("quota thresholds fetch failed: %s", exc)
        return {"error": str(exc), "thresholds": []}


@app.post("/api/quota/thresholds")
async def set_quota_threshold(request: Request) -> dict[str, Any]:
    """Set thresholds for a model. Body: {model, warn, critical, pause}."""
    try:
        body = await request.json()
        model = body.get("model")
        warn = float(body.get("warn"))
        critical = float(body.get("critical"))
        pause = float(body.get("pause"))
        if not model or warn is None or critical is None or pause is None:
            return {"error": "missing model/warn/critical/pause"}
        sys.path.insert(0, str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts"))
        from agy_quota_state import QuotaConfig  # type: ignore
        QuotaConfig().set_thresholds(model, warn, critical, pause)
        return {"ok": True, "model": model, "warn": warn, "critical": critical, "pause": pause}
    except Exception as exc:
        logger.warning("quota threshold set failed: %s", exc)
        return {"error": str(exc)}


@app.post("/api/quota/poll")
async def post_quota_poll(request: Request) -> dict[str, Any]:
    """Force a fresh quota poll + record. Used by the UI's 'refresh' button."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts"))
        from agy_quota_state import QuotaState  # type: ignore
        state = QuotaState()
        data = state.poll_and_record()
        return {"ok": True, "snapshot_count": len(data.get("models", []))}
    except Exception as exc:
        logger.warning("quota poll failed: %s", exc)
        return {"error": str(exc)}


# Simple auth gate for the /quota UI. The /api/* JSON endpoints are open
# to localhost callers (the UI itself). The /quota HTML page requires a
# Google OAuth bearer token that matches the AGY token file. This is a
# lightweight check — the user is already signed in to use AGY, so the
# same token proves they have the right to view quota state.
# In production, replace with proper session cookies + Google ID token.
async def _verify_agy_bearer(request: Request) -> bool:
    """Check Authorization header against the AGY OAuth access_token."""
    try:
        token_path = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        if not token_path.exists():
            return False
        stored = json.loads(token_path.read_text())
        stored_token = stored.get("token", {}).get("access_token") if "token" in stored else stored.get("access_token")
        if not stored_token:
            return False
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return False
        provided = auth[7:].strip()
        # Compare first 20 chars (full token compare would be a CSRF risk in logs)
        return provided[:20] == stored_token[:20] and len(provided) > 20
    except Exception:
        return False


@app.get("/quota")
async def quota_ui(request: Request) -> Response:
    """Serve the AGY quota dashboard HTML page. Requires AGY bearer auth."""
    if not await _verify_agy_bearer(request):
        return Response(
            status_code=401,
            content="<h1>401 — Sign in with your Google AI Ultra account to view quota</h1>"
                    "<p>This dashboard requires a Google OAuth bearer token.</p>"
                    "<p>Run <code>python3 ~/.hermes/profiles/orchestrator/scripts/refresh_agy_token.py</code> "
                    "to refresh, then include the access token as: "
                    "<code>Authorization: Bearer &lt;access_token&gt;</code></p>",
            media_type="text/html",
        )
    html = (Path(__file__).parent / "static" / "quota.html").read_text()
    return Response(content=html, media_type="text/html")


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


# ── Canonical SQLite Bus Persistence ──────────────────────────────────
# The in-process EventBus only fans out to in-process handlers (curator
# subscriber, WebSocket clients). The standalone supervisor's bus-subscriber
# thread polls the canonical SQLite file at $HOME/.prismatic/bus/event_log.sqlite.
# So to close the loop, every webhook publish must ALSO write to that file.
# (Jul 1 2026 — wired with the linear_webhook and github_webhook handlers.)
import sqlite3 as _sqlite3_canonical
import time as _time_canonical
import threading as _threading_canonical

_CANONICAL_BUS_LOCK = _threading_canonical.Lock()
# Same path resolution rule as the supervisor's publish_agent_completed:
# $HOME only, NEVER $PRISMATIC_HOME (which points to the user's work
# directory and an orphan bus at $PRISMATIC_HOME/.prismatic/bus/event_log.sqlite).
_CANONICAL_BUS_PATH = (
    os.environ.get("PRISMATIC_BUS_DB")
    or str(Path(os.path.expanduser("~")) / ".prismatic" / "bus" / "event_log.sqlite")
)


def _ensure_canonical_bus_table(conn) -> None:
    """Create the canonical events table if it doesn't exist (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ts REAL NOT NULL,
            processed INTEGER DEFAULT 0
        )
        """
    )


def _write_to_canonical_bus(event_type: str, source: str, payload: dict) -> None:
    """Persist a webhook event to the canonical SQLite bus.

    Other processes (e.g. standalone supervisor's bus-subscriber) read from
    this same file at 500ms cadence. Without this write, webhook events
    never reach the cross-process subscribers.
    """
    try:
        with _CANONICAL_BUS_LOCK:
            conn = _sqlite3_canonical.connect(_CANONICAL_BUS_PATH, timeout=5)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                _ensure_canonical_bus_table(conn)
                # Dedup key: topic + issue_id (if available) + ts.
                # We need sub-second resolution because real webhook traffic
                # can burst multiple events in the same second (e.g. a "label
                # added" + "issue updated" pair). ts is stored as REAL (float
                # seconds) so we use the float repr which gives microsecond
                # resolution when the value has microsecond precision.
                ts = _time_canonical.time()
                # The webhook handler passes either:
                #   linear: {"action", "linear_type", "issue_id", "raw"}
                #   github: {"action", "repository", "raw"}
                # We also handle the case where the helper is called directly
                # with a raw Linear-shaped payload ({"data": {"identifier": ...}}).
                issue_id_for_dedup = ""
                if isinstance(payload, dict):
                    issue_id_for_dedup = payload.get("issue_id") or ""
                    if not issue_id_for_dedup:
                        data = payload.get("data") or payload
                        if isinstance(data, dict):
                            issue_id_for_dedup = (
                                data.get("identifier") or data.get("id") or ""
                            )
                # Use a fine-grained timestamp string. Float repr preserves
                # microseconds when present; we also include a random suffix
                # as a tiebreaker in case the OS clock has low resolution.
                import random as _random_canonical
                ts_part = f"{ts:.6f}_{_random_canonical.randint(0, 9999):04d}"
                dedup_key = (
                    f"{event_type}:{issue_id_for_dedup}:{ts_part}"
                    if issue_id_for_dedup
                    else f"{event_type}:{source}:{ts_part}"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO events (dedup_key, topic, payload_json, ts) VALUES (?, ?, ?, ?)",
                    (
                        dedup_key,
                        event_type,
                        json.dumps({"type": event_type, "source": source,
                                    "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                                    "payload": payload}, default=str),
                        ts,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        logger.warning("Canonical bus write failed: %s", exc)


# ── Webhook Endpoints ────────────────────────────────────────────────

# the in-process EventBus. The IPC bridge (Unix socket) is what persists the
# event to the canonical SQLite bus; the standalone supervisor's bus-subscriber
# thread (500ms poll) reads from that same SQLite and dispatches to lanes.
#
# Without these publishes, the "event-driven" loop from Linear -> bus -> curator
# -> AGY is broken (GRO-3151). With them, every Linear change becomes a bus
# event and the factory reacts.
#
# (Jul 1 2026) HMAC verification is intentionally NOT added yet — the gateway
# is behind Cloudflare Access and the Linear webhook URL is unguessable. Once
# GRO-3151 lands, this gets a follow-up ticket for HMAC + raw body validation.


@app.post("/api/gateway/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    """Receive GitHub webhook events (PR opened, synchronized, review submitted)."""
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("GitHub webhook: invalid JSON (%d bytes): %s", len(body), exc)
        return {"status": "error", "message": "invalid JSON"}

    event_type = payload.get("action") or payload.get("hook_type") or "unknown"
    repo = (payload.get("repository") or {}).get("full_name", "unknown")
    logger.info("GitHub webhook: %s on %s", event_type, repo)

    bus = get_event_bus()
    await bus.publish(
        event_type=f"github.{event_type}",
        source="github_webhook",
        payload={
            "action": event_type,
            "repository": repo,
            "sender": (payload.get("sender") or {}).get("login"),
            "delivery_id": request.headers.get("X-GitHub-Delivery"),
            "raw": payload,
        },
    )
    # Also persist to canonical SQLite for cross-process subscribers
    _write_to_canonical_bus(
        event_type=f"github.{event_type}",
        source="github_webhook",
        payload={"action": event_type, "repository": repo, "raw": payload},
    )
    return {"status": "ok", "message": "github webhook published to bus"}


@app.post("/api/gateway/linear")
@app.post("/webhooks/linear")  # Backward-compat alias: Linear's webhook config uses this path
async def linear_webhook(request: Request) -> dict[str, Any]:
    """Receive Linear webhook events (issue status changes, comments).

    Maps Linear's action+type to bus event topics:
      - Issue + create         -> linear.issue.created
      - Issue + update/remove  -> linear.issue.updated
      - Comment + create       -> linear.comment.created
      - other (Project, etc.)  -> linear.<type>.<action>

    All payloads are persisted to the canonical SQLite bus via the IPC bridge.

    Two routes are registered for the same handler so we don't break
    existing Linear webhook configs (which use /webhooks/linear) while
    supporting the canonical /api/gateway/linear path. Verified Jul 1 2026:
    Linear's webhook config points at /webhooks/linear but the gateway
    only had /api/gateway/linear, causing silent 404s and no event flow.
    """
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("Linear webhook: invalid JSON (%d bytes): %s", len(body), exc)
        return {"status": "error", "message": "invalid JSON"}

    action = payload.get("action", "unknown")
    linear_type = payload.get("type", "unknown")
    data = payload.get("data") or {}
    issue_id = data.get("identifier") or data.get("id")

    logger.info(
        "Linear webhook: %s on %s (%s)",
        action, linear_type, issue_id or "n/a",
    )

    if linear_type == "Comment":
        bus_topic = f"linear.comment.{action}"
    elif linear_type == "Issue":
        bus_topic = f"linear.issue.{action if action != 'remove' else 'updated'}"
    else:
        bus_topic = f"linear.{linear_type.lower()}.{action}"

    bus = get_event_bus()
    await bus.publish(
        event_type=bus_topic,
        source="linear_webhook",
        payload={
            "action": action,
            "linear_type": linear_type,
            "issue_id": issue_id,
            "raw": payload,
        },
    )
    # Also write to canonical SQLite so cross-process subscribers (the
    # standalone supervisor's bus-subscriber thread) see this event.
    _write_to_canonical_bus(
        event_type=bus_topic,
        source="linear_webhook",
        payload={"action": action, "linear_type": linear_type, "issue_id": issue_id, "raw": payload},
    )
    return {"status": "ok", "message": f"linear webhook published to bus as {bus_topic}"}



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
    # Self-register in the Prismatic service registry (GRO-3167, Jul 1 2026).
    # This lets the supervisor, watchdog, and other services discover the gateway
    # without hardcoded host:port knowledge. The heartbeat is best-effort — if
    # the registry is missing or the helper is unavailable, the gateway still
    # starts. Registration runs in a daemon thread so it doesn't block startup.
    import threading as _threading

    def _register_self() -> None:
        try:
            # The registry helper lives in the orchestrator profile's scripts
            # dir, which may not be on sys.path for the gateway's venv. Try
            # the import directly, then fall back to a sys.path-insert.
            try:
                import prismatic_service_registry as _registry
            except ImportError:
                import sys
                _helper_path = str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts")
                if _helper_path not in sys.path:
                    sys.path.insert(0, _helper_path)
                import prismatic_service_registry as _registry
            # Deregister any stale entry from a previous instance first
            try:
                _registry.deregister("prismatic.gateway")
            except Exception:
                pass
            _registry.register(
                "prismatic.gateway",
                host="localhost",  # Services connect via localhost
                port=args.port,
                version="0.1.0",
                role="webhook_ingress",
                endpoints=[
                    "/api/gateway/linear",
                    "/webhooks/linear",
                    "/api/gateway/github",
                    "/health",
                ],
            )
            logger.info("Registered prismatic.gateway in service registry (port %d)", args.port)
            # Start a heartbeat thread so the registry doesn't mark us stale.
            # Registry marks entries stale after 60s of no heartbeat.
            import threading as _threading
            import time as _time

            def _heartbeat_loop() -> None:
                while True:
                    try:
                        _time.sleep(30)
                        _registry.heartbeat("prismatic.gateway")
                    except Exception:
                        pass

            _threading.Thread(target=_heartbeat_loop, daemon=True).start()
            logger.info("Started gateway heartbeat (30s interval)", flush=True)
        except ImportError:
            logger.debug("prismatic_service_registry not importable; skipping self-registration")
        except Exception as e:
            logger.warning("Failed to self-register in service registry: %s", e)

    _threading.Thread(target=_register_self, daemon=True).start()

    uvicorn.run(
        "prismatic.gateway.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
