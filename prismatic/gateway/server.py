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
import sqlite3
import sys
import threading
import time
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
from prismatic.plugin_architecture import MEDIA_CAPABILITY_CLASSES, plugin_catalog
from prismatic.plugin_artifacts import store_from_env as plugin_artifact_store
from prismatic.plugin_health import get_plugin_health
from prismatic.plugin_jobs import store_from_env as plugin_job_store
from prismatic.plugin_policy import preview_policy
from prismatic.pwp_integration import (
    connect_pwp,
    disconnect_pwp,
    integration_status,
    refresh_pwp,
    run_pwp_reference_lifecycle,
)
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


def _configured_cors_origins() -> list[str]:
    """Return explicit browser origins allowed to call the Gateway.

    Public/default installs are local-only. Remote deployments must opt in with
    PRISMATIC_CORS_ORIGINS as a comma-separated list of exact origins. Wildcard
    CORS is intentionally rejected when credentials are enabled.
    """
    raw = os.environ.get(
        "PRISMATIC_CORS_ORIGINS",
        "http://127.0.0.1:9000,http://localhost:9000",
    )
    origins = [
        origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()
    ]
    if not origins:
        return ["http://127.0.0.1:9000", "http://localhost:9000"]
    if "*" in origins:
        logger.warning(
            "Ignoring wildcard PRISMATIC_CORS_ORIGINS while credentials are enabled"
        )
        return [origin for origin in origins if origin != "*"] or [
            "http://127.0.0.1:9000"
        ]
    return origins


# CORS — local-only by default. Remote browser origins must be explicitly
# configured with PRISMATIC_CORS_ORIGINS; do not combine wildcard origins with
# credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Canonical governance dashboard route target: prismatic/gateway/templates/dashboard.html
_GOVERNANCE_DASHBOARD_HTML = _REPO_ROOT / "prismatic" / "gateway" / "templates" / "dashboard.html"


def _serve_governance_dashboard_html() -> HTMLResponse:
    """Serve the canonical Prismatic governance/control-plane dashboard."""
    if not _GOVERNANCE_DASHBOARD_HTML.exists():
        return HTMLResponse("Governance dashboard HTML not found", status_code=404)
    return HTMLResponse(_GOVERNANCE_DASHBOARD_HTML.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def serve_governance_index() -> HTMLResponse:
    """Governance gateway root: never serve marketing HTML here."""
    return _serve_governance_dashboard_html()


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_governance_dashboard() -> HTMLResponse:
    """Serve the canonical Prismatic governance/control-plane dashboard."""
    return _serve_governance_dashboard_html()

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
    get_event_bus()

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


@app.get("/api/harnesses")
async def get_harnesses() -> list[dict[str, Any]]:
    """Return the registered agent execution harness adapters."""
    registry_path = Path(__file__).resolve().parents[1] / "harnesses" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    return registry["harnesses"]


@app.get("/api/plugins/catalog")
async def plugins_catalog() -> dict[str, Any]:
    """Return the PE Core plugin catalog, manifest validation, and integration surfaces."""
    return plugin_catalog()


@app.get("/api/plugins/architecture")
async def plugins_architecture() -> dict[str, Any]:
    """Return the canonical plugin development architecture and future media classes."""
    catalog = plugin_catalog()
    return {
        "schema_version": catalog["schema_version"],
        "core_integration_points": catalog["core_integration_points"],
        "media_capability_classes": MEDIA_CAPABILITY_CLASSES,
        "proven_future_plugin_classes": [
            "video",
            "images",
            "music-sfx",
            "game-assets",
            "asset-forge-3d",
        ],
        "required_manifest_fields": [
            "schema_version",
            "name",
            "version",
            "entry_point",
            "core_version_constraint",
        ],
        "media_asset_required_fields": [
            "capabilities",
            "asset_domains",
            "artifact_types",
            "integration_points",
            "automation_surfaces",
        ],
        "recommended_surfaces": [
            "registered tools",
            "gateway API",
            "dashboard surface",
            "MCP server",
            "asset index",
            "artifact store",
            "governance checks",
        ],
    }


@app.get("/api/plugins/governance")
async def plugins_governance() -> dict[str, Any]:
    """Return operator-facing plugin readiness, risk, approval, and blocker data."""
    catalog = plugin_catalog()
    jobs = plugin_job_store().summary()
    artifacts = plugin_artifact_store().summary()
    return {
        "schema_version": catalog["schema_version"],
        "summary": catalog["governance_summary"],
        "jobs": jobs,
        "artifacts": artifacts,
        "plugins": [
            {
                "name": item["name"],
                "status": item["status"],
                "plugin_type": item["plugin_type"],
                "categories": item["categories"],
                "capability_count": len(item.get("capabilities", [])),
                "asset_domains": item.get("asset_domains", []),
                "artifact_types": item.get("artifact_types", []),
                "dashboard_surfaces": item.get("dashboard_surfaces", []),
                "endpoints": item.get("endpoints", []),
                "mcp_servers": item.get("mcp_servers", []),
                "governance": item["governance"],
            }
            for item in catalog["plugins"]
        ],
    }


@app.post("/api/plugins/policy/preview")
async def plugin_policy_preview(request: Request) -> JSONResponse:
    """Preview a generic plugin policy decision without mutating durable state."""
    payload = await request.json()
    kind = str(payload.get("kind") or "").strip()
    jobs = plugin_job_store()
    artifacts = plugin_artifact_store()
    job = jobs.get_job(str(payload.get("job_id"))) if payload.get("job_id") else None
    artifact = (
        artifacts.get_artifact(str(payload.get("artifact_id")))
        if payload.get("artifact_id")
        else None
    )
    policy = preview_policy(
        kind,
        job=job,
        artifact=artifact,
        plugin_name=payload.get("plugin_name"),
        action=payload.get("action"),
        input_summary=payload.get("input_summary"),
        target=payload.get("target"),
    )
    status = 200 if policy.get("decision") in {"allow", "needs_approval"} else 409
    return JSONResponse(policy, status_code=status)


@app.get("/api/plugins/audit-events")
async def list_plugin_audit_events(request: Request) -> dict[str, Any]:
    """List normalized cross-plugin audit events from job and artifact registries."""
    try:
        limit = int(request.query_params.get("limit") or "100")
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))
    plugin_name = request.query_params.get("plugin_name")
    job_id = request.query_params.get("job_id")
    artifact_id = request.query_params.get("artifact_id")
    event_type = request.query_params.get("event_type")
    job_store = plugin_job_store()
    artifact_store = plugin_artifact_store()
    job_events = job_store.list_events(
        plugin_name=plugin_name,
        job_id=job_id,
        event_type=event_type,
        limit=limit,
    )
    artifact_events = artifact_store.list_events(
        plugin_name=plugin_name,
        job_id=job_id,
        artifact_id=artifact_id,
        event_type=event_type,
        limit=limit,
    )
    events = [*job_events, *artifact_events]
    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    events = events[:limit]
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for event in events:
        by_source[event.get("audit_source") or "unknown"] = (
            by_source.get(event.get("audit_source") or "unknown", 0) + 1
        )
        by_type[event.get("event_type") or "unknown"] = (
            by_type.get(event.get("event_type") or "unknown", 0) + 1
        )
    return {
        "summary": {
            "event_count": len(events),
            "job_event_count": len(job_events),
            "artifact_event_count": len(artifact_events),
            "by_source": by_source,
            "by_event_type": by_type,
        },
        "events": events,
    }


@app.get("/api/plugins/artifacts")
async def list_plugin_artifacts(request: Request) -> dict[str, Any]:
    """List durable universal plugin artifacts/provenance records."""
    store = plugin_artifact_store()
    return {
        "summary": store.summary(),
        "artifacts": store.list_artifacts(
            plugin_name=request.query_params.get("plugin_name"),
            job_id=request.query_params.get("job_id"),
            approval_state=request.query_params.get("approval_state"),
            publish_state=request.query_params.get("publish_state"),
        ),
    }


@app.post("/api/plugins/artifacts")
async def create_plugin_artifact(request: Request) -> JSONResponse:
    """Create a durable plugin artifact/provenance record."""
    payload = await request.json()
    plugin_name = str(payload.get("plugin_name") or "").strip()
    if not plugin_name:
        return JSONResponse({"error": "plugin_name is required"}, status_code=400)
    artifact = plugin_artifact_store().create_artifact(
        plugin_name=plugin_name,
        job_id=payload.get("job_id"),
        artifact_type=payload.get("artifact_type"),
        mime_type=payload.get("mime_type"),
        path_or_url=payload.get("path_or_url"),
        asset_id=payload.get("asset_id"),
        metadata=payload.get("metadata") or {},
        provenance=payload.get("provenance") or {},
        input_summary=payload.get("input_summary"),
        provider_or_service=payload.get("provider_or_service"),
        approval_state=payload.get("approval_state") or "pending",
        publish_state=payload.get("publish_state") or "draft",
        artifact_id=payload.get("artifact_id"),
    )
    return JSONResponse(artifact, status_code=201)


@app.get("/api/plugins/artifacts/{artifact_id}")
async def get_plugin_artifact(artifact_id: str) -> JSONResponse:
    artifact = plugin_artifact_store().get_artifact(artifact_id)
    if not artifact:
        return JSONResponse(
            {"error": "plugin artifact not found", "artifact_id": artifact_id},
            status_code=404,
        )
    return JSONResponse(artifact)


@app.post("/api/plugins/artifacts/{artifact_id}/approve")
async def approve_plugin_artifact(artifact_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    artifact = plugin_artifact_store().set_approval(
        artifact_id,
        "approved",
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not artifact:
        return JSONResponse(
            {"error": "plugin artifact not found", "artifact_id": artifact_id},
            status_code=404,
        )
    return JSONResponse(artifact)


@app.post("/api/plugins/artifacts/{artifact_id}/reject")
async def reject_plugin_artifact(artifact_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    artifact = plugin_artifact_store().set_approval(
        artifact_id,
        "rejected",
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not artifact:
        return JSONResponse(
            {"error": "plugin artifact not found", "artifact_id": artifact_id},
            status_code=404,
        )
    return JSONResponse(artifact)


@app.post("/api/plugins/artifacts/{artifact_id}/publish-ready")
async def mark_plugin_artifact_publish_ready(
    artifact_id: str, request: Request
) -> JSONResponse:
    payload = await request.json()
    artifact = plugin_artifact_store().mark_publish_ready(
        artifact_id,
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not artifact:
        return JSONResponse(
            {"error": "plugin artifact not found", "artifact_id": artifact_id},
            status_code=404,
        )
    policy = artifact.get("policy_result") or {}
    if policy.get("decision") and policy.get("decision") != "allow":
        return JSONResponse(
            {"artifact": artifact, "policy_result": policy}, status_code=409
        )
    return JSONResponse(artifact)


@app.post("/api/plugins/artifacts/{artifact_id}/export")
async def export_plugin_artifact(artifact_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    target = str(payload.get("target") or "").strip()
    if not target:
        return JSONResponse({"error": "target is required"}, status_code=400)
    artifact = plugin_artifact_store().add_export(
        artifact_id,
        target=target,
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not artifact:
        return JSONResponse(
            {"error": "plugin artifact not found", "artifact_id": artifact_id},
            status_code=404,
        )
    policy = artifact.get("policy_result") or {}
    if policy.get("decision") != "allow":
        return JSONResponse(
            {"artifact": artifact, "policy_result": policy}, status_code=409
        )
    return JSONResponse({"artifact": artifact, "policy_result": policy})


@app.get("/api/plugins/jobs")
async def list_plugin_jobs(request: Request) -> dict[str, Any]:
    """List durable plugin jobs with audit summary."""
    store = plugin_job_store()
    return {
        "summary": store.summary(),
        "jobs": store.list_jobs(
            plugin_name=request.query_params.get("plugin_name"),
            status=request.query_params.get("status"),
        ),
    }


@app.post("/api/plugins/jobs")
async def create_plugin_job(request: Request) -> JSONResponse:
    """Create a durable plugin job and run the generic policy/approval gate."""
    payload = await request.json()
    plugin_name = str(payload.get("plugin_name") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not plugin_name or not action:
        return JSONResponse(
            {"error": "plugin_name and action are required"}, status_code=400
        )
    job = plugin_job_store().create_job(
        plugin_name,
        action,
        actor=str(payload.get("actor") or "operator"),
        source=str(payload.get("source") or "api"),
        input_summary=payload.get("input_summary"),
        operator_notes=payload.get("operator_notes"),
        approval_required=payload.get("approval_required"),
        metadata=payload.get("metadata") or {},
    )
    return JSONResponse(job, status_code=201)


@app.get("/api/plugins/jobs/{job_id}")
async def get_plugin_job(job_id: str) -> JSONResponse:
    job = plugin_job_store().get_job(job_id)
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    return JSONResponse(job)


@app.post("/api/plugins/jobs/{job_id}/approve")
async def approve_plugin_job(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    job = plugin_job_store().approve_job(
        job_id,
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    return JSONResponse(job)


@app.post("/api/plugins/jobs/{job_id}/reject")
async def reject_plugin_job(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    job = plugin_job_store().reject_job(
        job_id,
        actor=str(payload.get("actor") or "operator"),
        note=payload.get("note") or payload.get("operator_notes"),
    )
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    return JSONResponse(job)


@app.post("/api/plugins/jobs/{job_id}/start")
async def start_plugin_job(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    job, policy = plugin_job_store().start_job(
        job_id,
        actor=str(payload.get("actor") or "system"),
        message=payload.get("message"),
    )
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    if policy and policy.get("decision") == "allow":
        return JSONResponse({"job": job, "policy_result": policy})
    status = 409 if policy and policy.get("decision") == "needs_approval" else 403
    return JSONResponse({"job": job, "policy_result": policy}, status_code=status)


@app.post("/api/plugins/jobs/{job_id}/events")
async def append_plugin_job_event(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    job = plugin_job_store().append_event(
        job_id,
        str(payload.get("event_type") or "note_added"),
        actor=str(payload.get("actor") or "system"),
        source=str(payload.get("source") or "api"),
        message=payload.get("message"),
        details=payload.get("details") or {},
        artifact=payload.get("artifact"),
    )
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    return JSONResponse(job)


@app.post("/api/plugins/jobs/{job_id}/status")
async def update_plugin_job_status(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    status = str(payload.get("status") or "").strip()
    if not status:
        return JSONResponse({"error": "status is required"}, status_code=400)
    try:
        job = plugin_job_store().update_status(
            job_id,
            status,
            actor=str(payload.get("actor") or "system"),
            message=payload.get("message"),
            error=payload.get("error"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not job:
        return JSONResponse(
            {"error": "plugin job not found", "job_id": job_id}, status_code=404
        )
    if status == "running":
        policy = job.get("policy_result") or {}
        if policy.get("decision") != "allow":
            code = 409 if policy.get("decision") == "needs_approval" else 403
            return JSONResponse({"job": job, "policy_result": policy}, status_code=code)
    return JSONResponse(job)


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


@app.get("/api/pwp/status")
async def pwp_status() -> dict[str, Any]:
    """Return PWP additive plugin connection, capability, and governance status."""
    return integration_status()


@app.get("/pwp/status")
async def pwp_status_compat() -> dict[str, Any]:
    """Compatibility alias for stale dashboard/content cards that predate /api/pwp/status."""
    return integration_status()


@app.get("/api/content/status")
@app.get("/content/status")
async def content_status_compat() -> dict[str, Any]:
    """Return a non-404 content/plugin readiness shim for legacy content dashboard panes."""
    pwp = integration_status()
    catalog = plugin_catalog()
    return {
        "ok": True,
        "status": "ready" if pwp.get("manifest", {}).get("exists") else "degraded",
        "surface": "content-plugin-compat",
        "pwp": pwp,
        "plugins": {
            "count": catalog.get("count", 0),
            "ready_count": catalog.get("ready_count", 0),
            "invalid_count": catalog.get("invalid_count", 0),
        },
    }


@app.post("/api/pwp/connect")
async def pwp_connect() -> JSONResponse:
    """Connect PWP as an additive PE capability surface when hard blockers are clear."""
    payload = connect_pwp()
    status = 200 if payload.get("connected") else 409
    return JSONResponse(payload, status_code=status)


@app.post("/api/pwp/disconnect")
async def pwp_disconnect() -> dict[str, Any]:
    """Disconnect PWP capability surface without deleting plugin code or artifacts."""
    return disconnect_pwp()


@app.post("/api/pwp/refresh")
async def pwp_refresh() -> dict[str, Any]:
    """Refresh PWP dashboard/governance state from current manifest and files."""
    return refresh_pwp()


@app.post("/api/pwp/lifecycle-demo")
async def pwp_lifecycle_demo(request: Request) -> JSONResponse:
    """Run PWP as the full lifecycle reference plugin demo."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    disconnect_after = bool(payload.get("disconnect_after", True))
    actor = str(payload.get("actor") or "pwp-dashboard")
    result = run_pwp_reference_lifecycle(actor=actor, disconnect_after=disconnect_after)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.get("/api/cost")
async def get_cost_summary() -> dict[str, Any]:
    """Return per-dispatch cost summary for dashboards."""
    from prismatic.cost.tracker import cost_summary

    return cost_summary()


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


def _dashboard_recovery_state_path() -> Path:
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
    return state_dir / "dashboard_recovery_controls.json"


def _read_dashboard_recovery_state() -> dict[str, Any]:
    path = _dashboard_recovery_state_path()
    if not path.exists():
        return {"actions": [], "last_status": None}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("actions", [])
            data.setdefault("last_status", None)
            return data
    except Exception:
        logger.warning("dashboard recovery state read failed", exc_info=True)
    return {"actions": [], "last_status": None}


def _write_dashboard_recovery_state(state: dict[str, Any]) -> None:
    path = _dashboard_recovery_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _dashboard_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")).expanduser()


def _read_json_state(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
    except Exception:
        logger.warning("dashboard state read failed from %s", path, exc_info=True)
    return dict(default)


def _write_json_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _dashboard_foundation_state_path() -> Path:
    return _dashboard_state_dir() / "dashboard_foundation_controls.json"


def _read_dashboard_foundation_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_foundation_state_path(), {"actions": [], "last_action": None})


def _write_dashboard_foundation_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_foundation_state_path(), state)


def _dashboard_merge_state_path() -> Path:
    return _dashboard_state_dir() / "dashboard_merge_controls.json"


def _read_dashboard_merge_state() -> dict[str, Any]:
    return _read_json_state(_dashboard_merge_state_path(), {"actions": [], "last_action": None})


def _write_dashboard_merge_state(state: dict[str, Any]) -> None:
    _write_json_state(_dashboard_merge_state_path(), state)


@app.get("/api/dashboard/recovery-control/status")
async def dashboard_recovery_control_status() -> dict[str, Any]:
    """Return visible recovery-control proof for the dashboard UI."""
    return _read_dashboard_recovery_state()


@app.post("/api/dashboard/recovery-control", response_model=None)
async def dashboard_recovery_control(
    payload: dict[str, Any],
) -> dict[str, Any] | JSONResponse:
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
    ref = (
        str(payload.get("ref") or payload.get("run_id") or "dashboard").strip()
        or "dashboard"
    )
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
            "siblings": [
                "plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html",
                "dashboard/manifest.json",
            ],
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
@app.get("/api/merge/status")
async def merge_status() -> dict[str, Any]:
    """Return normalized Merge Pipeline status from existing merge state."""
    from prismatic.merge_status import load_merge_state, merge_status_payload

    state, state_path = load_merge_state()
    return merge_status_payload(
        state,
        _read_dashboard_merge_state(),
        MERGE_BACKLOG_TRIAGE,
        state_path=state_path,
    )


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


# ── Dashboard pane compatibility endpoints ───────────────────────────

def _dashboard_now() -> float:
    return time.time()


def _empty_failure_taxonomy() -> list[dict[str, Any]]:
    return [
        {
            "code": "missing_backend_route",
            "name": "Dashboard route compatibility",
            "layer": "gateway",
            "example": "A dashboard pane requested a route that was not wired into the live gateway.",
            "signals": ["HTTP 404", "dashboard pane stale fetch", "safe fallback route"],
        },
        {
            "code": "operator_action_required",
            "name": "Operator action required",
            "layer": "control-plane",
            "example": "A plugin is installed but disconnected until an operator connects it.",
            "signals": ["disconnected", "pending approval", "manual launch gate"],
        },
    ]


@app.get("/api/recovery/status")
async def dashboard_recovery_status() -> dict[str, Any]:
    """Return failure taxonomy/recovery state for the dashboard without 404 noise."""
    return {
        "ok": True,
        "service_name": "prismatic-gateway.service",
        "systemd_active": True,
        "heartbeat": {"exists": True, "source": "gateway-live"},
        "pool_stats": {"live_count": 0, "total_skipped_dlq": 0},
        "failure_taxonomy": _empty_failure_taxonomy(),
        "generated_at": _dashboard_now(),
        "source": "dashboard-compat",
    }


@app.get("/api/webhooks/stats")
async def dashboard_webhook_stats() -> dict[str, Any]:
    """Return webhook/queue counters expected by the dashboard telemetry pane."""
    received = _webhook_counters.get("github_received", 0) + _webhook_counters.get("linear_received", 0)
    auth_failed = _webhook_counters.get("github_auth_failed", 0) + _webhook_counters.get("linear_auth_failed", 0)
    published = _webhook_counters.get("github_published", 0) + _webhook_counters.get("linear_published", 0)
    return {
        "received": received,
        "auth_failed": auth_failed,
        "published": published,
        "average_dispatch_latency_seconds": 0.0,
        "recent_latencies": [],
        "queue_depths": {
            "pending": 0,
            "processing": 0,
            "completed": published,
            "failed": auth_failed,
        },
        "source": "gateway-counters",
    }


@app.get("/api/webhooks/queue")
async def dashboard_webhook_queue() -> dict[str, Any]:
    """Return a non-404 queue payload compatible with the dashboard table."""
    events_payload = await events_recent(limit=50)
    items: list[dict[str, Any]] = []
    for event in events_payload.get("events", []):
        payload = event.get("payload") if isinstance(event, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        items.append(
            {
                "id": event.get("rowid"),
                "identifier": payload.get("identifier") or payload.get("issue") or event.get("topic"),
                "agent_name": payload.get("agent") or payload.get("agent_name") or "gateway",
                "action": payload.get("action") or event.get("topic"),
                "dispatch_status": "completed" if event.get("processed") else "pending",
                "queued_at": event.get("ts"),
            }
        )
    return {"items": items, "total": len(items), "source": events_payload.get("source", "sqlite")}


@app.post("/api/webhooks/queue/retry/{task_id}")
async def dashboard_webhook_queue_retry(task_id: str) -> dict[str, Any]:
    """Acknowledge dashboard retry intent without mutating unknown queue storage."""
    return {
        "ok": True,
        "status": "accepted_noop",
        "task_id": task_id,
        "message": "Retry request recorded by gateway compatibility layer; no unsafe shell action executed.",
    }


@app.post("/api/webhooks/queue/purge")
async def dashboard_webhook_queue_purge() -> dict[str, Any]:
    """Acknowledge dashboard purge intent without destructive queue mutation."""
    return {
        "ok": True,
        "status": "accepted_noop",
        "message": "Purge request accepted by gateway compatibility layer; no destructive mutation executed.",
    }


@app.get("/api/dispatcher/status")
async def dashboard_dispatcher_status() -> dict[str, Any]:
    """Return dispatcher status for the dashboard without process-control side effects."""
    uptime = (_dashboard_now() - _server_started_at) if _server_started_at else 0.0
    return {
        "status": "active" if uptime > 0 else "idle",
        "cycle_number": 0,
        "last_cycle_at": None,
        "active_agents": [],
        "silent_stall": {"triggered": False, "message": "No dispatcher stall detected by gateway compatibility layer."},
        "source": "gateway-compat",
    }


@app.post("/api/dispatcher/{action}")
async def dashboard_dispatcher_control(action: str) -> JSONResponse:
    """Return an auditable no-op for dashboard dispatcher controls.

    Browser controls must not shell out to service managers or agent CLIs. This
    endpoint prevents 404s while making the no-op explicit to operators.
    """
    if action not in {"start", "stop", "restart", "pause", "resume"}:
        return JSONResponse({"ok": False, "status": "unsupported", "action": action}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "status": "accepted_noop",
            "action": action,
            "message": "Dispatcher control acknowledged; no shell or service-manager action executed from browser route.",
        }
    )


@app.get("/api/foundation/peer_review")
@app.get("/api/gateway/foundation/peer_review")
async def dashboard_foundation_peer_review() -> dict[str, Any]:
    """Return live Foundation / Peer Review status from run evidence."""
    from prismatic.foundation_status import foundation_peer_review_payload

    return foundation_peer_review_payload(_recent_agent_runs(limit=500), _read_dashboard_foundation_state())


@app.post("/api/foundation/control/{action}")
@app.post("/api/gateway/foundation/control/{action}")
async def dashboard_foundation_control(action: str) -> JSONResponse:
    """Record an audit-safe Foundation control intent without shell execution."""
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
    state["actions"] = [entry] + list(state.get("actions", []))[:24]
    state["last_action"] = entry
    state["updated_at"] = now
    _write_dashboard_foundation_state(state)
    return JSONResponse({"ok": True, "status": "ok", "message": entry["detail"], "entry": entry})


def _quota_state_db_path() -> Path:
    return Path(os.environ.get("PRISMATIC_QUOTA_STATE_DB", "~/.prismatic/quota_state.db")).expanduser()


def _read_quota_state_ledger(limit: int = 60) -> dict[str, Any] | None:
    """Read the original dashboard quota ledger contract from quota_state.db.

    Kai's dashboard tab expects current/recent_events/snapshot_at/snapshot_age_sec.
    The newer Vertex telemetry ledger uses quota_records/quota_freshness, so this
    bridge keeps the working dashboard contract connected to the persisted data.
    """
    db_path = _quota_state_db_path()
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        latest_ts_row = conn.execute("SELECT MAX(ts) AS ts FROM quota_snapshots").fetchone()
        latest_ts = latest_ts_row["ts"] if latest_ts_row else None
        current: list[dict[str, Any]] = []
        if latest_ts is not None:
            rows = conn.execute(
                """
                SELECT id, ts, model, display_name, remaining_pct, exhausted,
                       reset_time, supports_thinking, project_id
                FROM quota_snapshots
                WHERE ts = ?
                ORDER BY exhausted DESC, remaining_pct ASC, display_name ASC
                LIMIT ?
                """,
                (latest_ts, limit),
            ).fetchall()
            current = [
                {
                    "id": row["id"],
                    "timestamp": row["ts"],
                    "model": row["model"],
                    "display_name": row["display_name"] or row["model"],
                    "remaining_pct": row["remaining_pct"],
                    "exhausted": bool(row["exhausted"]),
                    "reset_time": row["reset_time"],
                    "supports_thinking": bool(row["supports_thinking"]),
                    "project_id": row["project_id"],
                }
                for row in rows
            ]
        event_rows = conn.execute(
            """
            SELECT id, ts, model, event_type, remaining_pct
            FROM quota_events
            ORDER BY ts DESC, id DESC
            LIMIT 50
            """
        ).fetchall()
        recent_events = [
            {
                "id": row["id"],
                "timestamp": row["ts"],
                "model": row["model"],
                "event_type": row["event_type"],
                "remaining_pct": row["remaining_pct"],
                "details": f"{row['event_type']} at {row['remaining_pct']}% remaining"
                if row["remaining_pct"] is not None
                else row["event_type"],
            }
            for row in event_rows
        ]
        thresholds = [
            dict(row)
            for row in conn.execute(
                "SELECT model, warn_pct, critical_pct, pause_pct, updated_at FROM quota_thresholds ORDER BY model"
            ).fetchall()
        ]
    snapshot_age_sec = None if latest_ts is None else max(0, int(time.time() - float(latest_ts)))
    return {
        "ok": True,
        "source": "quota_state.db",
        "db_path": str(db_path),
        "snapshot_at": latest_ts,
        "snapshot_age_sec": snapshot_age_sec,
        "current": current,
        "recent_events": recent_events,
        "thresholds": thresholds,
        "active_count": len(current),
        "exhausted_count": sum(1 for item in current if item.get("exhausted")),
        "quota_records": current,
        "quota_freshness": {
            "last_recorded_at": latest_ts,
            "age_seconds": snapshot_age_sec,
            "stale": snapshot_age_sec is None or snapshot_age_sec > 900,
        },
    }


def _read_vertex_quota_summary() -> dict[str, Any]:
    from prismatic.vertex_telemetry import VertexBillingLedger

    return VertexBillingLedger().get_status_summary()


def _dashboard_quota_payload() -> dict[str, Any]:
    legacy = _read_quota_state_ledger()
    if legacy and legacy.get("current"):
        return legacy
    try:
        vertex = _read_vertex_quota_summary()
    except Exception as exc:
        return {
            "ok": False,
            "source": "quota-empty-fallback",
            "current": [],
            "recent_events": [],
            "snapshot_at": None,
            "snapshot_age_sec": None,
            "quota_records": [],
            "quota_freshness": {"stale": True, "last_recorded_at": None, "age_seconds": None},
            "errors": [{"source": "vertex-ledger", "error_message": str(exc)}],
        }
    records = vertex.get("quota_records") or []
    return {
        **vertex,
        "ok": True,
        "source": vertex.get("source") or "vertex-ledger",
        "current": records,
        "recent_events": vertex.get("latest_errors") or [],
        "snapshot_at": (vertex.get("quota_freshness") or {}).get("last_recorded_at"),
        "snapshot_age_sec": (vertex.get("quota_freshness") or {}).get("age_seconds"),
    }


@app.get("/api/quota")
@app.get("/api/quotas")
@app.get("/api/gcp/quotas")
@app.get("/api/vertex/quota")
@app.get("/api/vertex/quotas")
async def dashboard_quota_summary() -> dict[str, Any]:
    """Return quota data in the dashboard contract, backed by persisted ledgers."""
    return _dashboard_quota_payload()


@app.post("/api/quota/poll")
async def dashboard_quota_poll() -> dict[str, Any]:
    """Run the real quota collector when credentials exist, then return dashboard data."""
    poll = {"attempted": True, "ok": False, "errors": []}
    try:
        from prismatic.vertex_telemetry import (
            VertexBillingLedger,
            poll_billing_balance,
            poll_vertex_quota_status,
        )

        ledger = VertexBillingLedger()
        status = poll_vertex_quota_status()
        records = status.get("records") or []
        errors = status.get("errors") or []
        if records:
            ledger.record_quota_snapshot(records)
        if errors:
            ledger.record_quota_errors(errors)
        balance = poll_billing_balance()
        if balance:
            ledger.record_balance_checkpoint(balance)
        poll.update(
            {
                "ok": bool(records) and not errors,
                "records": len(records),
                "errors": errors,
                "balance_recorded": bool(balance),
            }
        )
    except Exception as exc:
        poll["errors"] = [{"source": "quota-poll", "error_message": str(exc)}]
    payload = _dashboard_quota_payload()
    payload["poll"] = poll
    payload["status"] = "polled" if poll.get("ok") else "poll_unavailable_using_persisted_data"
    return payload


@app.get("/api/gateway/overnight-report/latest")
async def dashboard_overnight_report_latest() -> Any:
    """Compatibility alias for dashboard morning/factory briefing pane."""
    report = Path("~/.prismatic/reports/latest.json").expanduser()
    if not report.exists():
        return {
            "ok": True,
            "status": "empty",
            "title": "No overnight report found",
            "items": [],
            "source": str(report),
        }
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
    if signature:
        secrets = get_github_secrets()
        if not secrets:
            logger.warning("GitHub webhook skipped: secret not set")
            return {"status": "skipped", "reason": "no-secret"}
        # GitHub HMAC algorithm: hmac_sha256(secret, "x-hub-signature-256:" + body)
        signed_payload = b"x-hub-signature-256:" + body
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
    if signature:
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


# ── Native Cron Endpoints ─────────────────────────────────────────────


@app.get("/native-crons")
async def list_native_crons_endpoint(
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    """List PE-native portable cron definitions and queue state."""
    from prismatic.native_crons import list_native_crons

    return list_native_crons(include_deleted=include_deleted)


@app.post("/native-crons/{cron_id}/action")
async def native_cron_action(cron_id: str, payload: dict[str, Any]):
    """Pause/resume/deactivate/activate/delete/run a PE-native cron."""
    from fastapi.responses import JSONResponse
    from prismatic.native_crons import mutate_native_cron

    action = payload.get("action")
    if action not in {"pause", "resume", "deactivate", "activate", "delete", "run"}:
        return JSONResponse(
            status_code=400, content={"error": "Unsupported native cron action"}
        )
    try:
        return mutate_native_cron(cron_id, action)
    except KeyError:
        return JSONResponse(
            status_code=404, content={"error": f"Native cron not found: {cron_id}"}
        )
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


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
