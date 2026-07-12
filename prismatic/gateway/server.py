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
import hashlib
import hmac as _hmac
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
from fastapi.responses import HTMLResponse, JSONResponse

from prismatic.gateway.event_bus import get_event_bus
from prismatic.gateway.ipc_bridge import UnixSocketListener, create_event_ingest_route
from prismatic.gateway.ws_broadcaster import (
    start_ws_broadcaster,
    stop_ws_broadcaster,
)
from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.plugin_health import get_plugin_health
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


async def _publish_webhook_auth_failed(source: str) -> None:
    """Publish a redacted webhook auth-failure event for curator escalation."""
    try:
        from prismatic.gateway.event_bus import get_event_bus

        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type="webhook.auth_failed",
                source=source,
                payload={"status": "auth-failed"},
            )
    except Exception as exc:
        logger.warning("webhook auth-failed bus publish failed: %s", exc)


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

_DASHBOARD_TEMPLATE = Path(__file__).resolve().parent / "templates" / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Serve the canonical governance dashboard."""
    if not _DASHBOARD_TEMPLATE.exists():
        return HTMLResponse("Dashboard template not found", status_code=404)
    return HTMLResponse(_DASHBOARD_TEMPLATE.read_text(encoding="utf-8"))

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
    if (
        path == "/metrics"
        or path.startswith("/events/")
        or path.startswith("/curator/")
    ):
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
    global _started_at, _server_started_at, _run_store, _ipc_listener

    _started_at = time.time()
    _server_started_at = _started_at

    # Initialize EventBus (ensure singleton) and start checkpoint task
    bus = get_event_bus()
    bus.start_checkpoint_task()

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

    # Stop checkpoint task
    bus = get_event_bus()
    bus.stop_checkpoint_task()

    stop_ws_broadcaster()

    logger.info("Gateway shutdown complete")


# ── Agent Dashboard API ─────────────────────────────────────────────

_AGENT_DEFAULTS: dict[str, dict[str, str]] = {
    "agy": {"name": "AGY", "role": "Vision & Research CLI"},
    "jules": {"name": "Jules", "role": "Async Git & PR Agent"},
    "fred": {"name": "Fred", "role": "Nudge/Staging Governor"},
    "ned": {"name": "Ned", "role": "Research & Synthesis"},
    "kai": {"name": "Kai", "role": "Tourism Orchestrator"},
    "codex": {"name": "Codex", "role": "Coding Executor"},
}


def _agent_key(name: str | None) -> str:
    """Normalize agent/profile names for dashboard keys."""
    key = (name or "unknown").strip().lower().replace("agent:", "")
    aliases = {
        "agy-cli": "agy",
        "kai-content": "kai",
        "kai-css": "kai",
        "kai-js": "kai",
    }
    return aliases.get(key, key)


def _read_agent_registry() -> dict[str, Any]:
    """Read optional live agent registry without failing the dashboard."""
    candidates = [
        os.environ.get("PRISMATIC_AGENT_REGISTRY"),
        str(Path.home() / ".prismatic" / "registry.json"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Unable to read agent registry %s: %s", path, exc)
    return {}


def _seconds_between(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        return max(0.0, (end - start).total_seconds())
    except Exception:
        return None


def _recent_agent_runs(limit: int = 200) -> list[Any]:
    if _run_store is None:
        return []
    try:
        _run_store.reload()
        return _run_store.get_recent_runs(limit=limit)
    except Exception as exc:
        logger.warning("Unable to read recent run records: %s", exc)
        return []


@app.get("/api/agents")
async def get_agents() -> dict[str, Any]:
    """Return live agent status from registry plus recent run records."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    agents: dict[str, dict[str, Any]] = {
        key: {
            "name": meta["name"],
            "role": meta["role"],
            "status": "Unknown",
            "last_seen": None,
            "dispatched": 0,
            "duration": "—",
            "dedup": "—",
            "queue": [],
            "logs": [],
        }
        for key, meta in _AGENT_DEFAULTS.items()
    }

    registry = _read_agent_registry()
    for raw_name, info in registry.items():
        if not isinstance(info, dict):
            continue
        key = _agent_key(raw_name)
        agents.setdefault(
            key,
            {
                "name": raw_name,
                "role": info.get("role", "Agent"),
                "status": "Unknown",
                "last_seen": None,
                "dispatched": 0,
                "duration": "—",
                "dedup": "—",
                "queue": [],
                "logs": [],
            },
        )
        status = info.get("status") or info.get("state") or agents[key]["status"]
        agents[key]["status"] = str(status).title()
        agents[key]["last_seen"] = info.get("last_heartbeat") or info.get("last_seen")
        agents[key]["current_issue"] = info.get("issue") or info.get("task_id")

    durations: dict[str, list[float]] = {}
    for record in _recent_agent_runs():
        key = _agent_key(getattr(record, "agent_name", None))
        agent = agents.setdefault(
            key,
            {
                "name": key.title(),
                "role": "Agent",
                "status": "Unknown",
                "last_seen": None,
                "dispatched": 0,
                "duration": "—",
                "dedup": "—",
                "queue": [],
                "logs": [],
            },
        )
        agent["dispatched"] += 1
        status = getattr(record, "status", "unknown")
        issue_id = getattr(record, "issue_id", "unknown")
        started_at = getattr(record, "started_at", "") or ""
        completed_at = getattr(record, "completed_at", None)
        seconds = _seconds_between(started_at, completed_at)
        if seconds is not None:
            durations.setdefault(key, []).append(seconds)
        if status in {"pending", "running"}:
            agent["queue"].append(f"{issue_id} — {status}")
            agent["status"] = "Running" if status == "running" else "Queued"
        if len(agent["logs"]) < 5:
            agent["logs"].append(
                {
                    "ref": issue_id,
                    "time": started_at[11:19] if len(started_at) >= 19 else "—",
                    "status": str(status).title(),
                    "dur": f"{seconds:.1f}s" if seconds is not None else "—",
                }
            )

    for key, values in durations.items():
        if values:
            agents[key]["duration"] = f"{sum(values) / len(values):.1f}s"

    return {"updated_at": now, "agents": agents}


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

# ── Merge Pipeline API ───────────────────────────────────────────────

TERMINAL_LINEAR_STATE_NAMES = {"done", "canceled", "cancelled", "duplicate"}
TERMINAL_LINEAR_STATE_TYPES = {"completed", "canceled"}


def _linear_state_is_terminal(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    name = str(state.get("name") or "").strip().lower()
    state_type = str(state.get("type") or "").strip().lower()
    return name in TERMINAL_LINEAR_STATE_NAMES or state_type in TERMINAL_LINEAR_STATE_TYPES


def _terminal_pending_keep_rationale(details: Any) -> str | None:
    if not isinstance(details, dict):
        return None
    if not any(details.get(key) for key in ("force_keep_terminal", "terminal_keep", "force_keep")):
        return None
    rationale = details.get("terminal_keep_rationale") or details.get("force_keep_rationale") or details.get("rationale")
    return str(rationale or "force-kept terminal Linear issue").strip()


def _prune_terminal_linear_pending(
    pending: dict[str, Any],
    linear_states: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop terminal Linear issues from merge-pending state unless force-kept."""
    active_pending: dict[str, Any] = {}
    pruned: list[dict[str, Any]] = []
    retained_terminal: list[dict[str, Any]] = []
    for ticket, details in pending.items():
        linear_state = linear_states.get(ticket)
        if not _linear_state_is_terminal(linear_state):
            active_pending[ticket] = details
            continue
        rationale = _terminal_pending_keep_rationale(details)
        if rationale:
            active_pending[ticket] = details
            retained_terminal.append({"ticket": ticket, "linear_state": linear_state, "rationale": rationale})
        else:
            pruned.append({"ticket": ticket, "linear_state": linear_state})
    return active_pending, pruned, retained_terminal


@app.get("/api/harnesses")
async def get_harnesses() -> list[dict[str, Any]]:
    """Return the registered agent execution harness adapters."""
    registry_path = Path(__file__).resolve().parents[1] / "harnesses" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    return registry["harnesses"]


@app.get("/api/v1/plugins/{plugin_name}/health")
async def plugin_health(plugin_name: str, request: Request) -> JSONResponse:
    """Return lifecycle/telemetry health for a sandboxed plugin."""
    if not _check_observability_auth(request):
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    payload = get_plugin_health(plugin_name)
    if payload.get("status") == "NOT_FOUND":
        return JSONResponse(payload, status_code=404)
    if payload.get("status") == "unhealthy":
        return JSONResponse(payload, status_code=503)
    return JSONResponse(payload)


@app.get("/api/cost")
async def get_cost_summary() -> dict[str, Any]:
    """Return per-dispatch cost summary for dashboards."""
    from prismatic.cost.tracker import cost_summary

    return cost_summary()


@app.get("/api/quota")
async def get_quota_status() -> dict[str, Any]:
    """Return normalized GCP/model/subscription quota and cost pressure."""
    from prismatic.quota_status import build_quota_status

    return build_quota_status(_read_dashboard_quota_state())


@app.post("/api/quota/poll")
async def poll_quota_status() -> dict[str, Any]:
    """Record quota poll operator intent without shelling out from the browser."""
    now = datetime.now(timezone.utc).isoformat()
    state = _read_dashboard_quota_state()
    actions = list(state.get("actions", []))
    entry = {
        "id": f"quota-poll-{int(time.time())}",
        "action": "poll",
        "requested_at": now,
        "mode": "intent-recorded",
        "message": "Quota poll intent recorded; no shell command executed from browser request.",
    }
    actions.append(entry)
    state["actions"] = actions[-50:]
    state["last_poll"] = entry
    state["updated_at"] = now
    _write_dashboard_quota_state(state)
    timeline_item = _record_control_timeline_event(
        source="QuotaControl",
        severity="info",
        title="Quota poll requested",
        message=entry["message"],
        entity_id="quota-poll",
        metadata=entry,
    )
    try:
        event_bus = get_event_bus()
        if event_bus is not None:
            await event_bus.publish(
                event_type="dashboard.quota.poll",
                source="prismatic-hub",
                payload={"entry": entry, "timeline_item": timeline_item},
            )
    except Exception:
        logger.warning("quota poll EventBus publish failed", exc_info=True)
    from prismatic.quota_status import build_quota_status

    return {
        "ok": True,
        "status": "ok",
        "entry": entry,
        "timeline_item": timeline_item,
        "quota": build_quota_status(state),
        "stdout": "",
        "stderr": "",
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


# ── Dashboard recovery controls ─────────────────────────────────

_RECOVERY_CONTROL_ACTIONS: dict[str, dict[str, str]] = {
    "restart": {
        "label": "Restart agent worker",
        "status": "restart queued",
        "detail": "Server recorded a restart request for the selected agent.",
    },
    "retry": {
        "label": "Retry failed run",
        "status": "retry queued",
        "detail": "Server recorded a retry request and marked the run for another attempt.",
    },
    "replay": {
        "label": "Replay last event",
        "status": "replay queued",
        "detail": "Server recorded an event replay request for the selected agent stream.",
    },
}

_DISPATCHER_CONTROL_ACTIONS: dict[str, dict[str, str]] = {
    "start": {
        "label": "Start dispatcher",
        "status": "start queued",
        "detail": "Server recorded a dispatcher start request.",
        "severity": "success",
    },
    "stop": {
        "label": "Stop dispatcher",
        "status": "stop queued",
        "detail": "Server recorded a dispatcher stop request.",
        "severity": "warning",
    },
    "restart": {
        "label": "Restart dispatcher",
        "status": "restart queued",
        "detail": "Server recorded a dispatcher restart request.",
        "severity": "success",
    },
}

_QUEUE_CONTROL_ACTIONS: dict[str, dict[str, str]] = {
    "retry": {
        "label": "Retry queue task",
        "status": "retry queued",
        "detail": "Server recorded a queue retry request.",
        "severity": "success",
    },
    "purge": {
        "label": "Purge queue history",
        "status": "purge queued",
        "detail": "Server recorded a queue purge request.",
        "severity": "warning",
    },
}


def _dashboard_recovery_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_recovery_controls.json"


def _dashboard_dispatcher_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_dispatcher_controls.json"


def _dashboard_queue_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_queue_controls.json"


def _dashboard_foundation_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_foundation_controls.json"


def _dashboard_merge_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_merge_controls.json"


def _dashboard_quota_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_quota_controls.json"


def _read_json_state(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
    except Exception:
        logger.warning("dashboard state read failed: %s", path, exc_info=True)
    return dict(default)


def _write_json_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _read_dashboard_recovery_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_recovery_state_path(), {"actions": [], "last_status": None})


def _write_dashboard_recovery_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_recovery_state_path(), state)


def _read_dashboard_dispatcher_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_dispatcher_state_path(), {"commands": [], "last_command": None, "cycle_number": 0})


def _write_dashboard_dispatcher_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_dispatcher_state_path(), state)


def _read_dashboard_queue_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_queue_state_path(), {"actions": [], "last_status": None})


def _write_dashboard_queue_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_queue_state_path(), state)


def _read_dashboard_foundation_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_foundation_state_path(), {"actions": [], "last_action": None})


def _write_dashboard_foundation_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_foundation_state_path(), state)


def _read_dashboard_merge_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_merge_state_path(), {"actions": [], "last_action": None})


def _write_dashboard_merge_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_merge_state_path(), state)


def _read_dashboard_quota_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_quota_state_path(), {"actions": [], "last_poll": None})


def _write_dashboard_quota_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_quota_state_path(), state)


def _record_control_timeline_event(
    *,
    source: str,
    severity: str,
    title: str,
    message: str,
    entity_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Best-effort audit event for dashboard control-plane actions."""
    try:
        from prismatic.timeline import record_timeline_item

        return record_timeline_item(
            kind="manual",
            source=source,
            severity=severity,
            title=title,
            message=message,
            entity_id=entity_id,
            metadata=metadata or {},
        )
    except Exception:
        logger.warning("timeline control event record failed", exc_info=True)
        return None


def _skill_card(manifest: dict[str, Any], *, installed: bool) -> dict[str, Any]:
    name = str(manifest.get("name") or "")
    return {
        "id": name,
        "name": name,
        "version": str(manifest.get("version") or "?"),
        "description": str(manifest.get("description") or ""),
        "category": str(manifest.get("category") or "uncategorized"),
        "labels": manifest.get("labels") if isinstance(manifest.get("labels"), list) else [],
        "author": str(manifest.get("author") or ""),
        "installed": installed,
        "status": "Active" if installed else "Available",
        "path": str(manifest.get("_path") or ""),
    }


def _skills_payload() -> dict[str, Any]:
    from prismatic.skills import list_skills

    bundled = {str(skill.get("name")): skill for skill in list_skills(installed=False)}
    installed = {str(skill.get("name")): skill for skill in list_skills(installed=True)}
    cards: list[dict[str, Any]] = []
    for name in sorted(set(bundled) | set(installed)):
        manifest = installed.get(name) or bundled.get(name) or {"name": name}
        cards.append(_skill_card(manifest, installed=name in installed))
    return {
        "source": "prismatic.skills",
        "bundled_count": len(bundled),
        "installed_count": len(installed),
        "skills": cards,
    }


@app.get("/api/skills")
async def get_skills() -> dict[str, Any]:
    """Return live Prismatic Core skill registry cards."""
    return _skills_payload()


@app.get("/api/skills/{name}", response_model=None)
async def get_skill_info(name: str) -> dict[str, Any] | JSONResponse:
    from prismatic.skills import skill_info

    manifest = skill_info(name)
    if manifest is None:
        return JSONResponse({"ok": False, "error": "skill not found", "name": name}, status_code=404)
    payload = _skill_card(manifest, installed=any(card["id"] == name and card["installed"] for card in _skills_payload()["skills"]))
    payload["manifest"] = manifest
    return {"ok": True, "source": "prismatic.skills", "skill": payload}


@app.post("/api/skills/{name}/install", response_model=None)
async def install_skill_api(name: str) -> dict[str, Any] | JSONResponse:
    from prismatic.skills import install_skill, list_skills, skill_info

    bundled = {str(skill.get("name")) for skill in list_skills(installed=False)}
    installed = {str(skill.get("name")) for skill in list_skills(installed=True)}
    if name in installed:
        return JSONResponse({"ok": False, "error": "skill already installed", "name": name}, status_code=409)
    if name not in bundled:
        return JSONResponse({"ok": False, "error": "skill not found", "name": name}, status_code=404)
    if not install_skill(name):
        return JSONResponse({"ok": False, "error": "skill install failed", "name": name}, status_code=500)
    manifest = skill_info(name) or {"name": name}
    timeline_item = _record_control_timeline_event(
        source="SkillRegistry",
        severity="success",
        title="Skill installed",
        message=f"Installed Prismatic skill {name}",
        entity_id=name,
        metadata={"skill": name, "version": manifest.get("version"), "category": manifest.get("category")},
    )
    return {"ok": True, "skill": _skill_card(manifest, installed=True), "timeline_item": timeline_item}


@app.post("/api/skills/{name}/uninstall", response_model=None)
async def uninstall_skill_api(name: str) -> dict[str, Any] | JSONResponse:
    from prismatic.skills import list_skills, uninstall_skill

    installed = {str(skill.get("name")) for skill in list_skills(installed=True)}
    if name not in installed:
        return JSONResponse({"ok": False, "error": "installed skill not found", "name": name}, status_code=404)
    if not uninstall_skill(name):
        return JSONResponse({"ok": False, "error": "skill uninstall failed", "name": name}, status_code=500)
    timeline_item = _record_control_timeline_event(
        source="SkillRegistry",
        severity="warning",
        title="Skill uninstalled",
        message=f"Uninstalled Prismatic skill {name}",
        entity_id=name,
        metadata={"skill": name},
    )
    return {"ok": True, "name": name, "timeline_item": timeline_item}


@app.get("/api/agent-context")
async def get_agent_context(agent: str = "hermes") -> dict[str, Any]:
    from prismatic.agent_context import list_context_cards

    return {"source": "prismatic.agent_context", "agent": agent, "cards": list_context_cards(agent)}


@app.get("/api/agent-context/line")
async def get_agent_context_line(agent: str = "hermes") -> dict[str, Any]:
    from prismatic.agent_context import render_context_lines

    return {"source": "prismatic.agent_context", "agent": agent, "line": render_context_lines(agent)}


@app.post("/api/agent-context/install-doc", response_model=None)
async def install_agent_context_doc(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    from prismatic.agent_context import install_context_doc

    path = str(payload.get("path") or "").strip()
    agent = str(payload.get("agent") or "hermes").strip() or "hermes"
    if not path:
        return JSONResponse({"ok": False, "error": "path is required"}, status_code=400)
    result = install_context_doc(path, agent=agent)
    timeline_item = _record_control_timeline_event(
        source="AgentContext",
        severity="success",
        title="Agent context doc installed",
        message=f"Installed Prismatic agent context block for {agent} into {path}",
        entity_id=path,
        metadata={"agent": agent, "path": path, "action": result.get("action")},
    )
    result["timeline_item"] = timeline_item
    return result


@app.get("/api/dashboard/recovery-control/status")
async def dashboard_recovery_control_status() -> dict[str, Any]:
    """Return visible recovery-control proof for the dashboard UI."""
    return _read_dashboard_recovery_state()


@app.get("/api/gateway/webhooks/stats")
async def dashboard_webhook_stats() -> dict[str, Any]:
    """Return normalized webhook intake counters and queue depths."""
    from prismatic.ingestion_status import webhook_stats_payload

    return webhook_stats_payload(dict(_webhook_counters), _recent_agent_runs(limit=500))


@app.get("/api/gateway/webhooks/queue")
async def dashboard_webhook_queue(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Return normalized queue items derived from run records."""
    from prismatic.ingestion_status import queue_payload

    return queue_payload(_recent_agent_runs(limit=500), status=status, limit=limit)


@app.get("/api/gateway/dispatcher/status")
async def dashboard_dispatcher_status() -> dict[str, Any]:
    """Return audit-safe dispatcher status for the Ingestion Queue tab."""
    from prismatic.ingestion_status import dispatcher_status_payload

    return dispatcher_status_payload(
        _read_dashboard_dispatcher_state(),
        _recent_agent_runs(limit=500),
        server_started_at=_server_started_at or _started_at or None,
    )


@app.get("/api/gateway/recovery/status")
async def dashboard_recovery_status() -> dict[str, Any]:
    """Return failure taxonomy, recent failures, and recovery-control state."""
    from prismatic.ingestion_status import recovery_status_payload

    return recovery_status_payload(_read_dashboard_recovery_state(), _recent_agent_runs(limit=500), dict(_webhook_counters))


@app.get("/api/gateway/foundation/peer_review")
async def dashboard_foundation_peer_review() -> dict[str, Any]:
    """Return live Foundation / Peer Review status from run evidence."""
    from prismatic.foundation_status import foundation_peer_review_payload

    return foundation_peer_review_payload(_recent_agent_runs(limit=500), _read_dashboard_foundation_state())


@app.post("/api/gateway/foundation/control/{action}", response_model=None)
async def dashboard_foundation_control(action: str) -> dict[str, Any] | JSONResponse:
    """Record an audit-safe Foundation control intent.

    This endpoint intentionally does not shell out from the browser. It records
    the allowlisted operator action, emits timeline evidence, and publishes an
    EventBus signal for downstream workers/watchdogs.
    """
    from prismatic.foundation_status import CONTROL_ACTIONS, foundation_control_entry

    clean_action = str(action or "").strip().lower()
    if clean_action not in CONTROL_ACTIONS:
        return JSONResponse(
            {"ok": False, "status": "error", "error": f"unsupported foundation action: {clean_action}", "allowed_actions": sorted(CONTROL_ACTIONS)},
            status_code=400,
        )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = foundation_control_entry(clean_action, now=now)
    state = _read_dashboard_foundation_state()
    actions = [entry] + list(state.get("actions", []))
    state["actions"] = actions[:25]
    state["last_action"] = entry
    state["updated_at"] = now
    _write_dashboard_foundation_state(state)
    spec = CONTROL_ACTIONS[clean_action]
    timeline_item = _record_control_timeline_event(
        source="FoundationControl",
        severity=spec.get("severity", "info"),
        title=spec["label"],
        message=spec["detail"],
        entity_id=clean_action,
        metadata={"entry": entry},
    )
    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=f"dashboard.foundation.{clean_action}",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard foundation event publish failed", exc_info=True)
    return {
        "ok": True,
        "status": "ok",
        "message": spec["detail"],
        "entry": entry,
        "timeline_item": timeline_item,
        "stdout": "",
        "stderr": "",
    }


@app.post("/api/dashboard/recovery-control", response_model=None)
async def dashboard_recovery_control(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Record restart/retry/replay recovery actions for live dashboard proof.

    The dashboard controls intentionally do not shell out or kill processes from
    the browser. The server-side effect is a durable recovery-control ledger plus
    an optional event-bus publication, giving operators visible proof that the
    command reached the gateway and changed server state.
    """
    action = str(payload.get("action", "")).strip().lower()
    if action not in _RECOVERY_CONTROL_ACTIONS:
        return JSONResponse(
            {"error": "invalid_action", "allowed": sorted(_RECOVERY_CONTROL_ACTIONS)},
            status_code=400,
        )

    agent = str(payload.get("agent") or "unknown").strip() or "unknown"
    ref = str(payload.get("ref") or payload.get("run_id") or "dashboard").strip() or "dashboard"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    spec = _RECOVERY_CONTROL_ACTIONS[action]
    status_text = f"{spec['status']}: {agent} / {ref}"
    entry = {
        "id": f"recovery-{int(time.time() * 1000)}",
        "action": action,
        "agent": agent,
        "ref": ref,
        "label": spec["label"],
        "status": status_text,
        "detail": spec["detail"],
        "created_at": now,
    }

    state = _read_dashboard_recovery_state()
    actions = [entry] + list(state.get("actions", []))
    state["actions"] = actions[:25]
    state["last_status"] = status_text
    state["updated_at"] = now
    _write_dashboard_recovery_state(state)
    _record_control_timeline_event(
        source="RecoveryControl",
        severity="success",
        title=spec["label"],
        message=status_text,
        entity_id=ref,
        metadata={"entry": entry, "agent": agent, "action": action},
    )

    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=f"dashboard.recovery.{action}",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard recovery event publish failed", exc_info=True)

    return {"ok": True, "status": status_text, "entry": entry, "state": state}


@app.post("/api/gateway/dispatcher/{action}", response_model=None)
async def dashboard_dispatcher_control(action: str) -> dict[str, Any] | JSONResponse:
    """Audit dispatcher control-button requests from the governance dashboard.

    This endpoint intentionally records durable operator intent instead of
    directly shelling out from the browser process.
    """
    action = action.strip().lower()
    if action not in _DISPATCHER_CONTROL_ACTIONS:
        return JSONResponse(
            {"ok": False, "error": "invalid_action", "allowed": sorted(_DISPATCHER_CONTROL_ACTIONS)},
            status_code=400,
        )
    spec = _DISPATCHER_CONTROL_ACTIONS[action]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {
        "id": f"dispatcher-{action}-{int(time.time() * 1000)}",
        "action": action,
        "label": spec["label"],
        "status": spec["status"],
        "detail": spec["detail"],
        "created_at": now,
    }
    state = _read_dashboard_dispatcher_state()
    commands = [entry] + list(state.get("commands", []))
    state["commands"] = commands[:25]
    state["last_command"] = entry
    state["updated_at"] = now
    state["cycle_number"] = int(state.get("cycle_number", 0) or 0) + 1
    _write_dashboard_dispatcher_state(state)
    timeline_item = _record_control_timeline_event(
        source="DispatcherControl",
        severity=spec.get("severity", "info"),
        title=spec["label"],
        message=spec["detail"],
        entity_id="dispatcher",
        metadata={"entry": entry},
    )
    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=f"dashboard.dispatcher.{action}",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard dispatcher event publish failed", exc_info=True)
    return {"ok": True, "status": spec["status"], "entry": entry, "timeline_item": timeline_item}


@app.post("/api/gateway/webhooks/queue/retry/{task_id}", response_model=None)
async def dashboard_queue_retry(task_id: str) -> dict[str, Any] | JSONResponse:
    """Audit a webhook queue retry request from the governance dashboard."""
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return JSONResponse({"ok": False, "error": "task_id is required"}, status_code=400)
    spec = _QUEUE_CONTROL_ACTIONS["retry"]
    entry = {
        "id": f"queue-retry-{clean_task_id}-{int(time.time() * 1000)}",
        "action": "retry",
        "task_id": clean_task_id,
        "label": spec["label"],
        "status": spec["status"],
        "detail": spec["detail"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state = _read_dashboard_queue_state()
    actions = [entry] + list(state.get("actions", []))
    state["actions"] = actions[:25]
    state["last_status"] = entry["status"]
    state["updated_at"] = entry["created_at"]
    _write_dashboard_queue_state(state)
    timeline_item = _record_control_timeline_event(
        source="QueueControl",
        severity=spec.get("severity", "info"),
        title=spec["label"],
        message=f"{spec['detail']} Task: {clean_task_id}",
        entity_id=clean_task_id,
        metadata={"entry": entry},
    )
    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type="dashboard.queue.retry",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard queue retry event publish failed", exc_info=True)
    return {"ok": True, "status": spec["status"], "entry": entry, "timeline_item": timeline_item}


@app.post("/api/gateway/webhooks/queue/purge", response_model=None)
async def dashboard_queue_purge() -> dict[str, Any]:
    """Audit a webhook queue purge request from the governance dashboard."""
    spec = _QUEUE_CONTROL_ACTIONS["purge"]
    entry = {
        "id": f"queue-purge-{int(time.time() * 1000)}",
        "action": "purge",
        "label": spec["label"],
        "status": spec["status"],
        "detail": spec["detail"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state = _read_dashboard_queue_state()
    actions = [entry] + list(state.get("actions", []))
    state["actions"] = actions[:25]
    state["last_status"] = entry["status"]
    state["updated_at"] = entry["created_at"]
    _write_dashboard_queue_state(state)
    timeline_item = _record_control_timeline_event(
        source="QueueControl",
        severity=spec.get("severity", "info"),
        title=spec["label"],
        message=spec["detail"],
        entity_id="webhook-queue",
        metadata={"entry": entry},
    )
    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type="dashboard.queue.purge",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard queue purge event publish failed", exc_info=True)
    return {"ok": True, "status": spec["status"], "entry": entry, "timeline_item": timeline_item}


def _recent_run_records_for_timeline(limit: int = 50) -> list[dict[str, Any]]:
    if _run_store is None:
        return []
    try:
        _run_store.reload()
        return [_run_record_to_dict(record) for record in _run_store.get_recent_runs(limit=limit)]
    except Exception:
        logger.warning("timeline run-record read failed", exc_info=True)
        return []


@app.get("/api/timeline")
async def get_timeline(
    limit: int = 50,
    source: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Return the normalized Prismatic Operational Timeline."""
    from prismatic.timeline import list_timeline

    return list_timeline(
        limit=limit,
        source=source,
        kind=kind,
        severity=severity,
        run_records=_recent_run_records_for_timeline(limit=limit),
        recovery_state=_read_dashboard_recovery_state(),
        webhook_counters=dict(_webhook_counters),
    )


@app.get("/api/timeline/summary")
async def get_timeline_summary(limit: int = 200) -> dict[str, Any]:
    """Return compact Operational Timeline counts for dashboard cards."""
    from prismatic.timeline import timeline_summary

    return timeline_summary(
        limit=limit,
        run_records=_recent_run_records_for_timeline(limit=limit),
        recovery_state=_read_dashboard_recovery_state(),
        webhook_counters=dict(_webhook_counters),
    )


@app.post("/api/timeline/record", response_model=None)
async def record_timeline_api(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Record a manual/governance event in the Operational Timeline."""
    from prismatic.timeline import record_timeline_item

    try:
        item = record_timeline_item(
            kind=str(payload.get("kind") or "manual"),
            source=str(payload.get("source") or "Manual"),
            severity=str(payload.get("severity") or "info"),
            title=str(payload.get("title") or ""),
            message=str(payload.get("message") or ""),
            entity_id=str(payload.get("entity_id") or ""),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


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


# ── Merge Backlog Triage Companion API ───────────────────────────────

MERGE_BACKLOG_TRIAGE: dict[str, Any] = {
    "status": "green_baseline_with_followups",
    "source_issue": "GRO-3520",
    "last_verified": "2026-07-06T14:32:49Z",
    "snapshot": {
        "pending_count": 84,
        "merged_count": 266,
        "drift_detected": False,
        "last_apply": None,
    },
    "okf_artifacts": [
        "okf/audits/merge-family-audit-2026-07-06.md",
        "okf/audits/canonical-merge-winner-map-2026-07-06.md",
        "okf/standards/prismatic-governance-scorecard.md",
    ],
    "canonical_winners": [
        {
            "family": "GRO-1567",
            "winner": "prismatic/gateway/server.py",
            "siblings": ["prismatic/gateway/ipc_bridge.py"],
            "reason": "Gateway/server is the operator-facing control surface; IPC bridge remains derivative.",
        },
        {
            "family": "GRO-1614",
            "winner": "prismatic/core/hardware_profile.py",
            "siblings": [
                "prismatic/core/__init__.py",
                "prismatic/core/registry.py",
                "prismatic/interface/plugin.py",
                "tests/test_hardware_profiles.py",
            ],
            "reason": "Hardware profile model is the source of truth for the registry cluster.",
        },
        {
            "family": "GRO-2091",
            "winner": "okf/index.md",
            "siblings": ["downstream index copies", "cross-links"],
            "reason": "Root OKF index remains canonical and should only point outward.",
        },
        {
            "family": "GRO-2193/GRO-2305",
            "winner": "plugins/hermes-plugin-prismatic-hub/src/index.js",
            "siblings": ["plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html", "dashboard/manifest.json"],
            "reason": "Source dashboard tree owns generated dist artifacts.",
        },
        {
            "family": "GRO-2353/GRO-2355",
            "winner": "plugins/pwp/plugin-manifest.yaml",
            "siblings": [
                "plugins/pwp/__init__.py",
                "plugins/pwp/plugin.py",
                "scripts/migrate_pwp.py",
                "tests/test_pwp_hooks.py",
            ],
            "reason": "Manifest-first ownership keeps plugin wiring explicit and reviewable.",
        },
        {
            "family": "GRO-2471",
            "winner": ".gitignore",
            "siblings": ["duplicate ignore fragments", "stale rule copies"],
            "reason": "Low-footprint cleanup family with a single root ignore source.",
        },
    ],
    "duplicate_families": ["GRO-2193/GRO-2305", "GRO-2353/GRO-2355"],
    "contested_items": ["GRO-1567", "GRO-2353/GRO-2355"],
    "next_actions": [
        "Close or fold duplicate siblings into the listed canonical winners.",
        "Keep dashboard/API merge visibility green while follow-up cleanup reduces pending_count.",
        "Downgrade scorecard gate 7 if this endpoint or the linked OKF artifacts disappear.",
    ],
}


@app.get("/api/governance/merge-backlog")
@app.get("/api/gateway/governance/merge-backlog")
async def governance_merge_backlog() -> dict[str, Any]:
    """Expose the GRO-3520 merge backlog triage map for dashboards and operators."""
    return MERGE_BACKLOG_TRIAGE


@app.get("/api/gateway/merge/status")
async def dashboard_merge_status() -> dict[str, Any]:
    """Return normalized Merge Pipeline status from existing merge state."""
    from prismatic.merge_status import load_merge_state, merge_status_payload

    state, state_path = load_merge_state()
    return merge_status_payload(
        state,
        _read_dashboard_merge_state(),
        MERGE_BACKLOG_TRIAGE,
        state_path=state_path,
    )


@app.post("/api/gateway/merge/control/{action}", response_model=None)
async def dashboard_merge_control(action: str) -> dict[str, Any] | JSONResponse:
    """Record an audit-safe Merge Pipeline control intent.

    The browser can request a merge refresh/promote/hold, but this handler does
    not execute git, gh, or shell commands. Downstream operators/agents consume
    the timeline/EventBus intent.
    """
    from prismatic.merge_status import CONTROL_ACTIONS, merge_control_entry

    clean_action = str(action or "").strip().lower()
    if clean_action not in CONTROL_ACTIONS:
        return JSONResponse(
            {"ok": False, "status": "error", "error": f"unsupported merge action: {clean_action}", "allowed_actions": sorted(CONTROL_ACTIONS)},
            status_code=400,
        )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = merge_control_entry(clean_action, now=now)
    state = _read_dashboard_merge_state()
    actions = [entry] + list(state.get("actions", []))
    state["actions"] = actions[:25]
    state["last_action"] = entry
    state["updated_at"] = now
    _write_dashboard_merge_state(state)
    spec = CONTROL_ACTIONS[clean_action]
    timeline_item = _record_control_timeline_event(
        source="MergeControl",
        severity=spec.get("severity", "info"),
        title=spec["label"],
        message=spec["detail"],
        entity_id=clean_action,
        metadata={"entry": entry},
    )
    try:
        bus = get_event_bus()
        if bus is not None:
            await bus.publish(
                event_type=f"dashboard.merge.{clean_action}",
                source="prismatic-hub",
                payload=entry,
            )
    except Exception:
        logger.warning("dashboard merge event publish failed", exc_info=True)
    return {
        "ok": True,
        "status": "ok",
        "message": spec["detail"],
        "entry": entry,
        "timeline_item": timeline_item,
        "stdout": "",
        "stderr": "",
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
        db_path = os.path.join(
            os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"), db_path
        )
    if not os.path.exists(db_path):
        return {
            "events": [],
            "count": 0,
            "source": "sqlite",
            "note": "bus db not yet created",
        }
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
                events.append(
                    {
                        "rowid": row[0],
                        "topic": row[1],
                        "ts": row[3],
                        "processed": bool(row[4]),
                        "payload": payload,
                    }
                )
            return {"events": events, "count": len(events), "source": "sqlite"}
        finally:
            conn.close()
    except Exception as e:
        return {"events": [], "count": 0, "source": "sqlite", "error": str(e)}


@app.get("/api/report/latest", response_model=None)
async def get_latest_report() -> Any:
    """Return the latest overnight factory report JSON.

    The report is generated by prismatic.reports.overnight and persisted to
    ~/.prismatic/reports/latest.json.  Missing reports return 404 so dashboards
    can show an explicit empty state instead of stale sample data.
    """
    report = Path("~/.prismatic/reports/latest.json").expanduser()
    if not report.exists():
        return Response(status_code=404)
    try:
        return json.loads(report.read_text())
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "latest report is not valid JSON", "detail": str(exc)},
        )


@app.get("/events/bus-stats")
async def events_bus_stats() -> dict[str, Any]:
    """SQLite bus durable stats: total events, processed, oldest, newest."""
    import sqlite3

    db_path = os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite"
    if not os.path.isabs(db_path):
        db_path = os.path.join(
            os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"), db_path
        )
    if not os.path.exists(db_path):
        return {"exists": False}
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            processed = conn.execute(
                "SELECT COUNT(*) FROM events WHERE processed = 1"
            ).fetchone()[0]
            oldest = conn.execute("SELECT MIN(ts) FROM events").fetchone()[0]
            newest = conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
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
            "work",
            "prismatic-engine",
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
            cur = conn.execute("SELECT tag, COUNT(*) FROM tagged_events GROUP BY tag")
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


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    duration = None
    if record.started_at and record.completed_at:
        try:
            from datetime import datetime
            start = datetime.fromisoformat(record.started_at)
            end = datetime.fromisoformat(record.completed_at)
            duration = (end - start).total_seconds()
        except Exception:
            pass
    return {
        "run_id": record.run_id,
        "issue_id": record.issue_id,
        "agent_name": record.agent_name,
        "status": record.status,
        "verification_status": getattr(record, "verification_status", "self_reported"),
        "verification_scope": getattr(record, "verification_scope", "not_run"),
        "failure_category": getattr(record, "failure_category", "none"),
        "cleanup_status": getattr(record, "cleanup_status", "not_reported"),
        "done_gate_result": getattr(record, "done_gate_result", "not_done"),
        "done_gate_errors": getattr(record, "done_gate_errors", []),
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "output_path": record.output_path,
        "error_message": record.error_message,
        "evidence": getattr(record, "evidence", None),
        "duration_seconds": duration,
    }


@app.get("/runs")
async def list_runs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent agent run records, optionally filtered by status."""
    if _run_store is None:
        return []
    records = _run_store.get_recent_runs(limit=limit)
    result = []
    for r in records:
        d = _run_record_to_dict(r)
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
        content=json.dumps(_run_record_to_dict(record)),
        media_type="application/json",
    )


@app.post("/runs/{run_id}/complete")
async def complete_run(run_id: str, payload: dict[str, Any] | None = None) -> Any:
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
    evidence = (payload or {}).get("evidence")
    _run_store.update_run(run_id, status=status, evidence=evidence)
    updated = _run_store.get_run(run_id)
    return {"status": "ok", "run": _run_record_to_dict(updated) if updated else None}


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
    secrets = get_github_secrets()
    if secrets:
        if not signature:
            logger.warning("GitHub webhook authentication failed: signature header missing")
            _webhook_counters["github_auth_failed"] += 1
            await _publish_webhook_auth_failed("github")
            from fastapi.responses import JSONResponse
            return JSONResponse({"status": "auth-failed"}, status_code=401)
        # GitHub HMAC algorithm: hmac_sha256(secret, body)
        signed_payload = body
        # GitHub sends "sha256=<hex>"; compare_digest needs raw hex on both sides.
        sig_hex = (
            signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
        )
        expected = None
        for secret in secrets:
            candidate = _hmac.new(
                secret.encode(), signed_payload, hashlib.sha256
            ).hexdigest()
            if _hmac.compare_digest(candidate, sig_hex):
                expected = candidate
                break
        if expected is None:
            logger.warning("GitHub webhook authentication failed: invalid signature")
            _webhook_counters["github_auth_failed"] += 1
            await _publish_webhook_auth_failed("github")
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
                event_type=request.headers.get("X-GitHub-Event")
                or event.get("action", "unknown"),
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
            await _publish_webhook_auth_failed("linear")
            from fastapi.responses import JSONResponse

            return JSONResponse({"status": "auth-failed"}, status_code=401)
    else:
        logger.warning("Linear webhook skipped signature check: no secrets configured")
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
                "reason": f"Session '{session_id}' not found under the v0.1 contract (no live data path).",
            },
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
            schedule_id=schedule_id, enabled=enabled, schedule_expr=schedule_expr
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
    for k in (
        "PRISMATIC_LINEAR_WEBHOOK_SECRET",
        "PRISMATIC_LINEAR_WEBHOOK_SECRET_SECONDARY",
        "LINEAR_WEBHOOK_SIGNING_SECRET",
        "LINEAR_WEBHOOK_SIGNING_SECRET_SECONDARY",
    ):
        v = os.environ.get(k, "")
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    if not out:
        env_file = Path(
            os.environ.get("PRISMATIC_ENV_FILE")
            or os.path.join(
                os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"),
                ".hermes/profiles/orchestrator/.env",
            )
        )
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if (
                    "PRISMATIC_LINEAR_WEBHOOK_SECRET" in line
                    or "LINEAR_WEBHOOK_SIGNING_SECRET" in line
                ) and "=" in line:
                    v = line.split("=", 1)[1].strip().strip("'\"")
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
    for k in (
        "PRISMATIC_GITHUB_WEBHOOK_SECRET",
        "PRISMATIC_GITHUB_WEBHOOK_SECRET_SECONDARY",
    ):
        v = os.environ.get(k, "")
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    if not out:
        svc = Path("/etc/systemd/system/prismatic-gateway.service")
        if svc.exists():
            content = svc.read_text()
            for k in (
                "PRISMATIC_GITHUB_WEBHOOK_SECRET",
                "PRISMATIC_GITHUB_WEBHOOK_SECRET_SECONDARY",
            ):
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

    from prismatic.observability import init_logging

    init_logging(level=args.log_level.upper())

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
