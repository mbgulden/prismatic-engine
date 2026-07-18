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
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from prismatic.gateway.event_bus import get_event_bus
from prismatic.gateway.ipc_bridge import UnixSocketListener, create_event_ingest_route
from prismatic.gateway.ws_broadcaster import (
    start_ws_broadcaster,
    stop_ws_broadcaster,
)
from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    get_completed_work,
    ingest_completed_work,
    list_completed_work,
)
from prismatic.agy_merge_backlog import (
    AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER,
    AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
    build_operator_pr_creation_dry_run,
    execute_approved_real_pr_creation,
    build_real_pr_creation_approval_gate,
    build_real_pr_creation_approved_action,
    build_pr_candidate_lifecycle,
    get_merge_backlog_item,
    list_merge_backlog,
    verify_merge_backlog_item,
)
from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    AgyOvernightGuardStore,
    evaluate_overnight_readiness,
    list_overnight_run_attempts,
    record_guard_decision,
    set_operator_pause,
)
from prismatic.agy_limited_overnight_runner import (
    AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
    LimitedOvernightRunStore,
    RunnerRequest,
    run_limited_overnight_dry_run,
    status_payload as limited_overnight_status_payload,
    stop_latest_run as stop_limited_overnight_run,
)
from prismatic.agent_raw_output_queue import (
    get_raw_output,
    list_raw_outputs,
    mark_rerun_requested,
    queue_counts,
    repair_preview as raw_output_repair_preview,
)
from prismatic.agent_packet_normalizer import RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
from prismatic.agy_unattended_window import (
    UnattendedWindowRequest,
    UnattendedWindowStore,
    approve_window,
    evaluate_unattended_window,
    request_approval as request_unattended_window_approval,
    set_pause as set_unattended_window_pause,
    status_payload as unattended_window_status_payload,
)
from prismatic.budget_caps import read_budget_caps, write_budget_caps
from prismatic.completed_work_gate import (
    completed_work_gate_schema,
    demo_completed_work_gate_state,
)
from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.linear_rate_limit import (
    LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
    get_linear_rate_limit_snapshot,
)
from prismatic.dispatcher import get_dispatcher_polling_budget_snapshot
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


@app.get("/api/quota/caps")
@app.get("/api/gateway/quota/caps")
async def get_budget_caps() -> dict[str, Any]:
    """Return Resources panel budget caps."""

    return read_budget_caps()


@app.post("/api/quota/caps")
@app.post("/api/gateway/quota/caps")
async def set_budget_caps(body: dict[str, Any]) -> dict[str, Any]:
    """Persist Resources panel budget caps."""

    return write_budget_caps(body)


@app.get("/api/completed-work/gate/schema")
@app.get("/api/gateway/completed-work/gate/schema")
async def completed_work_gate_contract_schema() -> dict[str, Any]:
    """Return the AGY completed-work integration gate contract."""

    return completed_work_gate_schema()


@app.get("/api/completed-work/gate/demo")
@app.get("/api/gateway/completed-work/gate/demo")
async def completed_work_gate_demo() -> dict[str, Any]:
    """Return fixture-only gate status for dashboard/API proof.

    This endpoint never merges, dispatches, or mutates external AGY branches.
    """

    return demo_completed_work_gate_state()


@app.get("/api/agents/raw-output")
@app.get("/api/gateway/agents/raw-output")
async def list_agent_raw_output(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Expose real persisted raw output queue state. No fixtures."""

    rows = [row.as_dict() for row in list_raw_outputs(limit=limit)]
    return {
        "status": "ok",
        "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
        "count": len(rows),
        "counts": queue_counts(),
        "raw_outputs": rows,
        "non_claims": {
            "demo_fixture_rows": False,
            "auto_repair_success": False,
            "auto_rerun_enabled": False,
            "auto_merge_enabled": False,
            "production_deploy": False,
        },
    }


@app.get("/api/agents/raw-output/{raw_output_id}")
@app.get("/api/gateway/agents/raw-output/{raw_output_id}")
async def get_agent_raw_output(raw_output_id: str) -> dict[str, Any]:
    try:
        row = get_raw_output(raw_output_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="raw output row not found") from exc
    return {
        "status": "ok",
        "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
        "raw_output": row.as_dict(),
    }


@app.post("/api/agents/raw-output/{raw_output_id}/repair-preview")
@app.post("/api/gateway/agents/raw-output/{raw_output_id}/repair-preview")
async def preview_agent_raw_output_repair(raw_output_id: str) -> dict[str, Any]:
    try:
        return raw_output_repair_preview(raw_output_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="raw output row not found") from exc


@app.post("/api/agents/raw-output/{raw_output_id}/mark-rerun-requested")
@app.post("/api/gateway/agents/raw-output/{raw_output_id}/mark-rerun-requested")
async def request_agent_raw_output_rerun(raw_output_id: str) -> dict[str, Any]:
    try:
        row = mark_rerun_requested(raw_output_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="raw output row not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "rerun_requested",
        "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
        "auto_rerun_enabled": False,
        "raw_output": row.as_dict(),
    }


@app.post("/api/agy/completed-work/ingest")
@app.post("/api/gateway/agy/completed-work/ingest")
async def ingest_agy_completed_work(body: dict[str, Any]) -> dict[str, Any]:
    """Persist a completed AGY result packet and gate it for review."""

    packet = body.get("packet") if "packet" in body else body
    if not isinstance(packet, dict):
        raise HTTPException(status_code=422, detail="packet must be a JSON object")
    try:
        row = ingest_completed_work(
            packet,
            dirty_source=bool(body.get("dirty_source", False)),
            source_is_stale=bool(body.get("source_is_stale", False)),
            conflicts=body.get("conflicts")
            if isinstance(body.get("conflicts"), list)
            else None,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "accepted",
        "marker": AGY_COMPLETED_WORK_INGESTION_MARKER,
        "completed_work": row.as_dict(),
    }


@app.get("/api/agy/completed-work")
@app.get("/api/gateway/agy/completed-work")
async def list_agy_completed_work(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List persisted completed AGY rows, newest first."""

    rows = [row.as_dict() for row in list_completed_work(limit=limit)]
    return {
        "status": "ok",
        "marker": AGY_COMPLETED_WORK_INGESTION_MARKER,
        "count": len(rows),
        "completed_work": rows,
    }


@app.get("/api/agy/completed-work/{completed_work_id}")
@app.get("/api/gateway/agy/completed-work/{completed_work_id}")
async def get_agy_completed_work(completed_work_id: str) -> dict[str, Any]:
    """Return one persisted AGY completed-work row."""

    try:
        row = get_completed_work(completed_work_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc
    return {
        "status": "ok",
        "marker": AGY_COMPLETED_WORK_INGESTION_MARKER,
        "completed_work": row.as_dict(),
    }


@app.get("/api/agy/merge-backlog")
@app.get("/api/gateway/agy/merge-backlog")
async def list_agy_merge_backlog(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List dry-run AGY merge backlog decisions from persisted completed-work rows."""

    rows = [item.as_dict() for item in list_merge_backlog(limit=limit)]
    return {
        "status": "ok",
        "marker": AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER,
        "count": len(rows),
        "merge_backlog": rows,
        "non_claims": {
            "auto_merge": False,
            "production_deploy": False,
            "github_pr_created": False,
            "agy_dispatch": False,
        },
    }


@app.post("/api/agy/merge-backlog/{completed_work_id}/pr-dry-run")
def agy_merge_backlog_operator_pr_dry_run(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = payload or {}
    try:
        return build_operator_pr_creation_dry_run(
            completed_work_id,
            requested_by=str(body.get("requested_by") or "dashboard-operator"),
            action=str(body.get("action") or "operator_pr_creation_dry_run"),
            linear_writeback=bool(body.get("linear_writeback", True)),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-dry-run")
def gateway_agy_merge_backlog_operator_pr_dry_run(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return agy_merge_backlog_operator_pr_dry_run(completed_work_id, payload)


@app.post("/api/agy/merge-backlog/{completed_work_id}/pr-approval")
def agy_merge_backlog_real_pr_approval_gate(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = payload or {}
    try:
        return build_real_pr_creation_approval_gate(
            completed_work_id,
            requested_by=str(body.get("requested_by") or "operator"),
            approved_by=str(body.get("approved_by") or "") or None,
            approval_token=str(body.get("approval_token") or "") or None,
            approval_note=str(body.get("approval_note") or "") or None,
            action=str(body.get("action") or "record_real_pr_creation_approval"),
            expose_real_pr_action=bool(body.get("expose_real_pr_action", True)),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-approval")
def gateway_agy_merge_backlog_real_pr_approval_gate(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return agy_merge_backlog_real_pr_approval_gate(completed_work_id, payload)


@app.post("/api/agy/merge-backlog/{completed_work_id}/pr-create-approved")
def agy_merge_backlog_real_pr_create_approved_action(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = payload or {}
    try:
        return build_real_pr_creation_approved_action(
            completed_work_id,
            approval_id=str(body.get("approval_id") or "") or None,
            approved_by=str(body.get("approved_by") or "") or None,
            approval_token=str(body.get("approval_token") or "") or None,
            requested_by=str(body.get("requested_by") or "operator"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-create-approved")
def gateway_agy_merge_backlog_real_pr_create_approved_action(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return agy_merge_backlog_real_pr_create_approved_action(completed_work_id, payload)


@app.post("/api/agy/merge-backlog/{completed_work_id}/pr-executor")
def agy_merge_backlog_approved_real_pr_executor(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = payload or {}
    try:
        return execute_approved_real_pr_creation(
            completed_work_id,
            approval_id=str(body.get("approval_id") or "") or None,
            approved_by=str(body.get("approved_by") or "") or None,
            approval_token=str(body.get("approval_token") or "") or None,
            requested_by=str(body.get("requested_by") or "operator"),
            final_operator_trigger=bool(body.get("final_operator_trigger", False)),
            execute=bool(body.get("execute", False)),
            executor_mode=str(body.get("executor_mode") or "dry_run"),
            allow_real_side_effects=bool(body.get("allow_real_side_effects", False)),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-executor")
def gateway_agy_merge_backlog_approved_real_pr_executor(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return agy_merge_backlog_approved_real_pr_executor(completed_work_id, payload)


@app.get("/api/agy/merge-backlog/{completed_work_id}")
@app.get("/api/gateway/agy/merge-backlog/{completed_work_id}")
async def get_agy_merge_backlog(completed_work_id: str) -> dict[str, Any]:
    """Return one dry-run AGY merge backlog decision."""

    try:
        item = get_merge_backlog_item(completed_work_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc
    return {
        "status": "ok",
        "marker": AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
        "merge_backlog": item.as_dict(),
        "non_claims": {
            "auto_merge": False,
            "production_deploy": False,
            "github_pr_created": False,
            "agy_dispatch": False,
        },
    }


@app.post("/api/agy/merge-backlog/{completed_work_id}/verify")
@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/verify")
async def verify_agy_merge_backlog(completed_work_id: str) -> dict[str, Any]:
    """Evaluate the lane verification gate for one completed-work row."""

    try:
        return verify_merge_backlog_item(completed_work_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.get("/api/agy/overnight-guard")
@app.get("/api/gateway/agy/overnight-guard")
async def get_agy_overnight_guard() -> dict[str, Any]:
    """Return current limited overnight readiness guard status. No task launch."""

    decision = evaluate_overnight_readiness()
    persisted = record_guard_decision(decision)
    return {
        "status": "ok",
        "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER,
        "guard": decision.as_dict(),
        "persisted_decision": persisted.as_dict(),
        "recent_runs": [run.as_dict() for run in list_overnight_run_attempts(limit=10)],
        "operator_pause": AgyOvernightGuardStore().operator_pause(),
        "tasks_launched": 0,
    }


@app.post("/api/agy/merge-backlog/{completed_work_id}/pr-candidate")
@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-candidate")
async def stage_agy_pr_candidate(
    completed_work_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage deterministic clean-PR candidate metadata after explicit operator action.

    This endpoint is intentionally metadata-only: no git mutation, no GitHub PR
    creation, no auto-merge, no production deploy, and no AGY dispatch.
    """

    payload = body or {}
    try:
        result = build_pr_candidate_lifecycle(
            completed_work_id,
            requested_by=str(payload.get("requested_by") or "operator"),
            action=str(payload.get("action") or "stage_pr_candidate"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc
    if result.get("status") == "blocked":
        return {**result, "http_status": 200}
    return result


@app.post("/api/agy/overnight-guard/evaluate")
@app.post("/api/gateway/agy/overnight-guard/evaluate")
async def evaluate_agy_overnight_guard(
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate/persist a dry-run overnight readiness decision. No task launch."""

    payload = body or {}
    agents = payload.get("allowed_agents") or payload.get("agents") or ["agy"]
    if not isinstance(agents, list):
        raise HTTPException(status_code=422, detail="allowed_agents must be a list")
    decision = evaluate_overnight_readiness(
        requested_by=str(payload.get("requested_by") or "fred"),
        allowed_agents=[str(agent) for agent in agents],
        max_tasks=int(payload.get("max_tasks", payload.get("max_tasks_per_run", 1))),
        auto_merge=bool(payload.get("auto_merge", False)),
        production_deploy=bool(payload.get("production_deploy", False)),
        real_github_pr_create=bool(payload.get("real_github_pr_create", False)),
        bulk_dispatch=bool(payload.get("bulk_dispatch", False)),
        gateway_healthy=bool(payload.get("gateway_healthy", True)),
        operator_summary_required=bool(payload.get("operator_summary_required", True)),
        required_preflight_ok=bool(payload.get("required_preflight_ok", True)),
    )
    persisted = record_guard_decision(decision)
    return {
        "status": "ok",
        "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER,
        "guard": decision.as_dict(),
        "persisted_decision": persisted.as_dict(),
        "dry_run": True,
        "tasks_launched": 0,
        "non_claims": {
            "overnight_autopilot_active": False,
            "auto_merge_enabled": False,
            "bulk_agy_dispatch": False,
            "production_deploy": False,
            "real_github_pr_created": False,
        },
    }


@app.post("/api/agy/overnight-guard/pause")
@app.post("/api/gateway/agy/overnight-guard/pause")
async def pause_agy_overnight_guard() -> dict[str, Any]:
    state = set_operator_pause(True)
    return {"status": "paused", **state}


@app.post("/api/agy/overnight-guard/resume")
@app.post("/api/gateway/agy/overnight-guard/resume")
async def resume_agy_overnight_guard() -> dict[str, Any]:
    state = set_operator_pause(False)
    return {"status": "resumed", **state}


@app.get("/api/agy/overnight-guard/runs")
@app.get("/api/gateway/agy/overnight-guard/runs")
async def list_agy_overnight_guard_runs(
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return {
        "status": "ok",
        "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER,
        "count": len(list_overnight_run_attempts(limit=limit)),
        "runs": [run.as_dict() for run in list_overnight_run_attempts(limit=limit)],
        "tasks_launched": 0,
    }


@app.post("/api/agy/limited-overnight/dry-run")
@app.post("/api/gateway/agy/limited-overnight/dry-run")
async def run_agy_limited_overnight_dry_run(
    body: dict[str, Any] | None = None,
) -> JSONResponse:
    request = RunnerRequest.from_mapping(body or {})
    result = run_limited_overnight_dry_run(request)
    status_code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=status_code)


@app.get("/api/agy/limited-overnight/runs")
@app.get("/api/gateway/agy/limited-overnight/runs")
async def list_agy_limited_overnight_runs(
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return limited_overnight_status_payload(limit=limit)


@app.get("/api/agy/limited-overnight/runs/{run_id}")
@app.get("/api/gateway/agy/limited-overnight/runs/{run_id}")
async def get_agy_limited_overnight_run(run_id: str) -> dict[str, Any]:
    try:
        run = LimitedOvernightRunStore().get(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="limited overnight run not found"
        ) from exc
    return {
        "status": "ok",
        "marker": run.get("marker") or AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
        "run": run,
        "non_claims": run.get("non_claims", {}),
    }


@app.post("/api/agy/limited-overnight/stop")
@app.post("/api/gateway/agy/limited-overnight/stop")
async def stop_agy_limited_overnight() -> dict[str, Any]:
    return stop_limited_overnight_run()


@app.get("/api/agy/unattended-window/status")
@app.get("/api/gateway/agy/unattended-window/status")
async def get_agy_unattended_window_status(
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return unattended_window_status_payload(limit=limit)


@app.post("/api/agy/unattended-window/evaluate")
@app.post("/api/gateway/agy/unattended-window/evaluate")
async def evaluate_agy_unattended_window(
    body: dict[str, Any] | None = None,
) -> JSONResponse:
    request = UnattendedWindowRequest.from_mapping(body or {})
    result = evaluate_unattended_window(request)
    status_code = 200 if result.get("allowed") else 409
    return JSONResponse(result, status_code=status_code)


@app.post("/api/agy/unattended-window/request-approval")
@app.post("/api/gateway/agy/unattended-window/request-approval")
async def request_agy_unattended_window_approval(
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = UnattendedWindowRequest.from_mapping(body or {})
    return request_unattended_window_approval(request)


@app.post("/api/agy/unattended-window/approve")
@app.post("/api/gateway/agy/unattended-window/approve")
async def approve_agy_unattended_window(
    body: dict[str, Any] | None = None,
) -> JSONResponse:
    request = UnattendedWindowRequest.from_mapping(body or {})
    result = approve_window(request)
    status_code = 200 if result.get("allowed") else 409
    return JSONResponse(result, status_code=status_code)


@app.post("/api/agy/unattended-window/pause")
@app.post("/api/gateway/agy/unattended-window/pause")
async def pause_agy_unattended_window() -> dict[str, Any]:
    return set_unattended_window_pause(True)


@app.post("/api/agy/unattended-window/resume")
@app.post("/api/gateway/agy/unattended-window/resume")
async def resume_agy_unattended_window() -> dict[str, Any]:
    return set_unattended_window_pause(False)


@app.get("/api/agy/unattended-window/evaluations/{evaluation_id}")
@app.get("/api/gateway/agy/unattended-window/evaluations/{evaluation_id}")
async def get_agy_unattended_window_evaluation(evaluation_id: str) -> dict[str, Any]:
    try:
        item = UnattendedWindowStore().get(evaluation_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="unattended window evaluation not found"
        ) from exc
    return {"status": "ok", "evaluation": item}


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


# ── Dashboard live adapter APIs (restored from Fred dashboard branches) ───────


def _run_records_for_dashboard(limit: int = 500) -> list[Any]:
    return _recent_agent_runs(limit=limit)


def _run_dicts_for_dashboard(limit: int = 500) -> list[dict[str, Any]]:
    return [
        _run_record_to_dict(record)
        for record in _run_records_for_dashboard(limit=limit)
    ]


def _dashboard_agent_inputs(limit: int = 500) -> dict[str, Any]:
    from prismatic.ingestion_status import queue_payload, recovery_status_payload
    from prismatic.timeline import list_timeline

    records = _run_records_for_dashboard(limit=limit)
    record_dicts = [_run_record_to_dict(record) for record in records]
    recovery_state = _read_dashboard_recovery_state()
    queue = queue_payload(records, limit=limit)
    recovery = recovery_status_payload(
        recovery_state, records, counters=_webhook_counters
    )
    timeline = list_timeline(
        limit=min(limit, 100),
        run_records=record_dicts,
        recovery_state=recovery_state,
        webhook_counters=_webhook_counters,
    )
    return {
        "run_records": records,
        "registry": _read_agent_registry(),
        "queue_payload": queue,
        "timeline_payload": timeline,
        "health_context": {
            "recovery": recovery,
            "server_started_at": _server_started_at,
        },
    }


@app.get("/api/gateway/agents/status")
async def gateway_agents_status() -> dict[str, Any]:
    """Return normalized live agent status for the dashboard main tab."""
    from prismatic.agent_status import build_agent_status

    return build_agent_status(**_dashboard_agent_inputs())


@app.get("/api/gateway/agents/{agent_id}")
async def gateway_agent_detail(agent_id: str) -> dict[str, Any]:
    """Return normalized live status detail for one dashboard agent."""
    from prismatic.agent_status import build_agent_detail

    return build_agent_detail(agent_id, **_dashboard_agent_inputs())


@app.get("/api/gateway/signals")
async def gateway_agent_signals(
    limit: int = Query(200, ge=1, le=1000),
    agent: str | None = None,
) -> dict[str, Any]:
    """Return durable assigned-agent signal/chat/transcript stream for dashboard Signals."""
    from prismatic.agent_signal_stream import list_agent_signals

    return list_agent_signals(limit=limit, agent=agent, include_log_tails=True)


@app.get("/api/gateway/timeline")
async def gateway_timeline(
    limit: int = Query(80, ge=1, le=500),
    source: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Return real operational timeline events for dashboard activity/signals."""
    from prismatic.timeline import list_timeline

    return list_timeline(
        limit=limit,
        source=source,
        kind=kind,
        severity=severity,
        run_records=_run_dicts_for_dashboard(limit=limit),
        recovery_state=_read_dashboard_recovery_state(),
        webhook_counters=_webhook_counters,
    )


@app.get("/api/webhooks/stats")
@app.get("/api/gateway/webhooks/stats")
async def dashboard_webhook_stats() -> dict[str, Any]:
    from prismatic.ingestion_queue import queue_stats_payload

    return queue_stats_payload(extra_counters=_webhook_counters)


@app.get("/api/webhooks/queue")
@app.get("/api/gateway/webhooks/queue")
async def dashboard_webhook_queue(
    status: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    from prismatic.ingestion_queue import queue_payload

    return queue_payload(status=status, limit=limit)


@app.get("/api/webhooks/queue/status")
@app.get("/api/gateway/webhooks/queue/status")
async def dashboard_webhook_queue_status() -> dict[str, Any]:
    from prismatic.ingestion_queue import queue_status_payload

    return queue_status_payload(extra_counters=_webhook_counters)


@app.post("/api/webhooks/queue/retry/{task_id}")
@app.post("/api/gateway/webhooks/queue/retry/{task_id}")
async def dashboard_webhook_queue_retry(task_id: str) -> dict[str, Any]:
    from prismatic.ingestion_queue import retry_task

    return retry_task(task_id)


@app.post("/api/webhooks/queue/purge")
@app.post("/api/gateway/webhooks/queue/purge")
async def dashboard_webhook_queue_purge() -> dict[str, Any]:
    from prismatic.ingestion_queue import purge_queue

    return purge_queue()


@app.get("/api/dispatcher/status")
@app.get("/api/gateway/dispatcher/status")
async def dashboard_dispatcher_status() -> dict[str, Any]:
    from prismatic.ingestion_status import dispatcher_status_payload

    payload = dispatcher_status_payload(
        {}, _run_records_for_dashboard(), server_started_at=_server_started_at
    )
    payload["linear_rate_limit"] = get_linear_rate_limit_snapshot()
    payload["polling_budget"] = get_dispatcher_polling_budget_snapshot()
    return payload


@app.get("/api/linear/rate-limit")
@app.get("/api/gateway/linear/rate-limit")
async def dashboard_linear_rate_limit() -> dict[str, Any]:
    return {
        "ok": True,
        "marker": LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
        "linear_rate_limit": get_linear_rate_limit_snapshot(),
        "polling_budget": get_dispatcher_polling_budget_snapshot(),
    }


@app.post("/api/dispatcher/{action}")
@app.post("/api/gateway/dispatcher/{action}")
async def dashboard_dispatcher_action(action: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "accepted_noop",
        "action": action,
        "message": f"Dispatcher {action} intent recorded; browser route did not launch or stop workers.",
    }


@app.get("/api/recovery/status")
@app.get("/api/gateway/recovery/status")
async def dashboard_recovery_status() -> dict[str, Any]:
    from prismatic.ingestion_status import recovery_status_payload

    return recovery_status_payload(
        _read_dashboard_recovery_state(),
        _run_records_for_dashboard(),
        counters=_webhook_counters,
    )


@app.get("/api/foundation/peer_review")
@app.get("/api/gateway/foundation/peer_review")
async def dashboard_foundation_peer_review() -> dict[str, Any]:
    from prismatic.foundation_status import foundation_peer_review_payload

    return foundation_peer_review_payload(_run_records_for_dashboard())


@app.post("/api/foundation/control/{action}")
@app.post("/api/gateway/foundation/control/{action}")
async def dashboard_foundation_control(action: str) -> dict[str, Any]:
    from prismatic.foundation_status import foundation_control_entry
    from prismatic.timeline import utc_now

    entry = foundation_control_entry(action, now=utc_now(), actor="dashboard")
    return {
        "ok": True,
        "status": entry.get("status"),
        "entry": entry,
        "source": "foundation_control_state",
    }


@app.get("/api/skills")
@app.get("/api/gateway/skills")
async def dashboard_skills() -> dict[str, Any]:
    try:
        from prismatic.skills import list_skills

        skills = list_skills()
    except Exception as exc:
        logger.warning("dashboard skills adapter failed: %s", exc)
        skills = []
    return {
        "ok": True,
        "source": "prismatic.skills",
        "skills": skills,
        "count": len(skills),
    }


@app.get("/api/skills/{name}", response_model=None)
@app.get("/api/gateway/skills/{name}", response_model=None)
async def dashboard_skill_detail(name: str) -> Any:
    try:
        from prismatic.skills import read_skill

        return read_skill(name)
    except Exception as exc:
        return JSONResponse(
            {"error": "skill_not_found", "detail": str(exc)}, status_code=404
        )


@app.post("/api/skills/{name}/install", response_model=None)
@app.post("/api/gateway/skills/{name}/install", response_model=None)
async def dashboard_skill_install(name: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "accepted_noop",
        "skill": name,
        "message": "Install intent recorded; no browser shell execution.",
    }


@app.post("/api/skills/{name}/uninstall", response_model=None)
@app.post("/api/gateway/skills/{name}/uninstall", response_model=None)
async def dashboard_skill_uninstall(name: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "accepted_noop",
        "skill": name,
        "message": "Uninstall intent recorded; no browser shell execution.",
    }


@app.get("/api/quota")
@app.get("/api/gateway/quota")
@app.get("/api/quotas")
@app.get("/api/gcp/quotas")
@app.get("/api/vertex/quota")
@app.get("/api/vertex/quotas")
async def dashboard_quota_summary() -> dict[str, Any]:
    try:
        from prismatic.vertex_telemetry import read_quota_summary  # type: ignore

        data = read_quota_summary()
        records = data.get("quota_records") or data.get("current") or []
        return {
            **data,
            "ok": True,
            "source": data.get("source") or "vertex-ledger",
            "current": data.get("current") or records,
            "recent_events": data.get("recent_events")
            or data.get("latest_errors")
            or [],
            "snapshot_at": data.get("snapshot_at")
            or (data.get("quota_freshness") or {}).get("last_recorded_at"),
            "snapshot_age_sec": data.get("snapshot_age_sec")
            or (data.get("quota_freshness") or {}).get("age_seconds"),
        }
    except Exception as exc:
        return {
            "ok": True,
            "source": "quota_state.db",
            "current": [],
            "recent_events": [],
            "snapshot_at": None,
            "snapshot_age_sec": None,
            "errors": [{"source": "quota-adapter", "error_message": str(exc)}],
        }


@app.post("/api/quota/poll")
@app.post("/api/gateway/quota/poll")
async def dashboard_quota_poll() -> dict[str, Any]:
    payload = await dashboard_quota_summary()
    return {
        **payload,
        "poll": {
            "attempted": False,
            "reason": "browser-safe route returns persisted quota state only",
        },
    }


@app.get("/api/gateway/merge/status")
async def dashboard_merge_status() -> dict[str, Any]:
    from prismatic.merge_status import load_merge_state, merge_status_payload

    state, path = load_merge_state()
    return merge_status_payload(state, {}, {}, state_path=path)


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
    queue_result: dict[str, Any] = {"queued": False, "reason": "not-attempted"}
    try:
        from prismatic.ingestion_queue import enqueue_linear_event, increment_counter

        queue_result = enqueue_linear_event(event, raw_body=body)
        increment_counter("linear_received", 1)
        if queue_result.get("inserted"):
            increment_counter("queued", 1)
    except Exception as exc:
        queue_result = {"queued": False, "status": "failed", "reason": str(exc)[:160]}
        logger.error("Linear webhook durable queue insert failed: %s", exc)
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
    return {"status": "ok", "message": "webhook received", "queue": queue_result}


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


# ── Production workspace-tree compatibility surface ─────────────────────
# These read-only routes are intentionally small and dependency-light. They
# keep the operator page visible without CDN JavaScript and provide the local
# proof targets required by the Production Durability Standard.
WORKSPACE_TREE_MAX_PREVIEW_BYTES = int(
    os.environ.get("PRISMATIC_WORKSPACE_TREE_MAX_PREVIEW_BYTES", "524288")
)
WORKSPACE_TREE_PREVIEW_EXTENSIONS = {
    ".cfg",
    ".css",
    ".env",
    ".gitignore",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
WORKSPACE_TREE_IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
}


def _workspace_tree_roots() -> dict[str, Path]:
    """Return production-safe workspace roots for read-only tree/preview APIs."""
    roots: dict[str, Path] = {}
    raw = os.environ.get("PRISMATIC_WORKSPACE_ROOTS", "")
    for entry in raw.split(os.pathsep):
        if not entry.strip():
            continue
        if "=" in entry:
            label, value = entry.split("=", 1)
        else:
            value = entry
            label = Path(value).name or "workspace"
        path = Path(value).expanduser().resolve()
        if path.exists() and path.is_dir():
            roots[label.strip() or path.name] = path

    repo_root = Path(__file__).resolve().parents[2]
    roots.setdefault("Prismatic Engine", repo_root)
    work_dir = Path(
        os.environ.get("PRISMATIC_WORKSPACE_ROOT", str(Path.home() / "work"))
    )
    if work_dir.exists():
        for child in sorted(work_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                label = " ".join(
                    part.capitalize()
                    for part in child.name.replace("_", "-").split("-")
                )
                roots.setdefault(label, child.resolve())
    return roots


def _workspace_tree_resolve(file: str) -> Path:
    """Resolve a requested file under an allowed workspace root, blocking traversal."""
    requested = (file or "").strip()
    if not requested:
        requested = "README.md"
    raw_path = Path(requested).expanduser()
    roots = _workspace_tree_roots()

    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        for root in roots.values():
            candidates.append(root / raw_path)

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        for root in roots.values():
            try:
                if resolved.is_relative_to(root.resolve()):
                    return resolved
            except (OSError, ValueError):
                continue
    raise HTTPException(status_code=403, detail="workspace-tree path blocked")


def _workspace_tree_node(
    path: Path, root: Path, depth: int = 0, max_depth: int = 2
) -> dict[str, Any]:
    rel = str(path.relative_to(root)) if path != root else ""
    if path.is_file():
        stat = path.stat()
        return {
            "name": path.name,
            "type": "file",
            "path": str(path),
            "relative_path": rel,
            "size": stat.st_size,
            "previewable": path.suffix.lower() in WORKSPACE_TREE_PREVIEW_EXTENSIONS,
        }
    children: list[dict[str, Any]] = []
    if depth < max_depth:
        try:
            for child in sorted(
                path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
            )[:200]:
                if (
                    child.name.startswith(".")
                    or child.name in WORKSPACE_TREE_IGNORED_DIRS
                ):
                    continue
                children.append(_workspace_tree_node(child, root, depth + 1, max_depth))
        except OSError:
            children = []
    return {
        "name": path.name or str(path),
        "type": "directory",
        "path": str(path),
        "relative_path": rel,
        "children": children,
    }


def _workspace_tree_preview_payload(file: str) -> dict[str, Any]:
    target = _workspace_tree_resolve(file)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="workspace-tree file not found")
    if target.suffix.lower() not in WORKSPACE_TREE_PREVIEW_EXTENSIONS:
        raise HTTPException(
            status_code=415, detail="workspace-tree file type is not previewable"
        )
    stat = target.stat()
    if stat.st_size > WORKSPACE_TREE_MAX_PREVIEW_BYTES:
        raise HTTPException(
            status_code=413, detail="workspace-tree file too large for preview"
        )
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415, detail="workspace-tree file is not UTF-8 text"
        ) from exc
    roots = _workspace_tree_roots()
    relative = None
    root_label = None
    for label, root in roots.items():
        try:
            if target.resolve().is_relative_to(root.resolve()):
                relative = str(target.resolve().relative_to(root.resolve()))
                root_label = label
                break
        except (OSError, ValueError):
            continue
    return {
        "ok": True,
        "name": target.name,
        "path": str(target),
        "relative_path": relative or target.name,
        "workspace": root_label,
        "size": stat.st_size,
        "content": content,
        "lines": content.count("\n") + 1,
    }


def _workspace_tree_html(file: str) -> str:
    roots = _workspace_tree_roots()
    try:
        preview = _workspace_tree_preview_payload(file)
        preview_status = "Loaded preview"
        preview_content = preview["content"][:20000]
        preview_name = preview["relative_path"]
    except HTTPException as exc:
        preview = {"ok": False, "detail": exc.detail, "status_code": exc.status_code}
        preview_status = f"Preview unavailable ({exc.status_code}): {exc.detail}"
        preview_content = ""
        preview_name = file or "README.md"

    root_items = "".join(
        f"<li><strong>{label}</strong><br><code>{root}</code></li>"
        for label, root in roots.items()
    )
    safe_content = (
        preview_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    safe_name = (
        str(preview_name)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Prismatic Workspace Tree</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; background:#07111f; color:#e5edf7; }}
    body {{ margin:0; padding:32px; background:linear-gradient(135deg,#07111f,#101a2e); }}
    main {{ max-width:1120px; margin:0 auto; }}
    .card {{ border:1px solid #2f4668; border-radius:18px; padding:20px; margin:18px 0; background:rgba(15,23,42,.88); box-shadow:0 18px 45px rgba(0,0,0,.28); }}
    h1 {{ margin:0 0 8px; font-size:clamp(2rem,5vw,3.4rem); }}
    h2 {{ margin-top:0; color:#93c5fd; }}
    code, pre {{ background:#020617; color:#dbeafe; border-radius:10px; }}
    code {{ padding:2px 6px; }}
    pre {{ padding:16px; overflow:auto; max-height:520px; white-space:pre-wrap; }}
    .status {{ color:#86efac; font-weight:700; }}
    a {{ color:#7dd3fc; }}
  </style>
</head>
<body>
<main data-route=\"workspace-tree\">
  <section class=\"card\">
    <h1>Prismatic Workspace Tree</h1>
    <p class=\"status\">Visible fallback content loaded without CDN JavaScript.</p>
    <p>This production-safe read-only route previews files under configured workspace roots and blocks traversal.</p>
  </section>
  <section class=\"card\">
    <h2>{safe_name}</h2>
    <p>{preview_status}</p>
    <p>API: <a href=\"/api/workspaces\">/api/workspaces</a> · <a href=\"/api/workspace-tree/preview?file={safe_name}\">preview JSON</a></p>
    <pre>{safe_content or json.dumps(preview, indent=2)}</pre>
  </section>
  <section class=\"card\">
    <h2>Workspace roots</h2>
    <ul>{root_items}</ul>
  </section>
</main>
<script src=\"/workspace-tree/index.js?v=20260716\" defer></script>
</body>
</html>"""


_GOVERNANCE_DASHBOARD_HTML = (
    Path(__file__).resolve().parent / "templates" / "dashboard.html"
)


def _serve_governance_dashboard_html() -> HTMLResponse:
    """Serve the canonical Prismatic governance/control-plane dashboard."""
    if not _GOVERNANCE_DASHBOARD_HTML.exists():
        return HTMLResponse(
            "Prismatic governance dashboard HTML not found",
            status_code=404,
        )
    return HTMLResponse(_GOVERNANCE_DASHBOARD_HTML.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def serve_governance_index() -> HTMLResponse:
    """Governance gateway root: serve the canonical dashboard, not fallback shell."""
    return _serve_governance_dashboard_html()


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_governance_dashboard() -> HTMLResponse:
    """Serve the canonical Prismatic governance/control-plane dashboard."""
    return _serve_governance_dashboard_html()


@app.get("/api/workspaces")
async def workspace_tree_workspaces() -> dict[str, Any]:
    roots = _workspace_tree_roots()
    return {
        "ok": True,
        "workspaces": [
            {
                "name": label,
                "path": str(root),
                "exists": root.exists(),
                "tree": _workspace_tree_node(root, root, max_depth=1)
                if root.exists()
                else None,
            }
            for label, root in roots.items()
        ],
        "workspace_count": len(roots),
        "max_preview_bytes": WORKSPACE_TREE_MAX_PREVIEW_BYTES,
    }


@app.get("/api/workspace-tree/preview")
async def workspace_tree_preview(file: str = Query(...)) -> dict[str, Any]:
    return _workspace_tree_preview_payload(file)


@app.get("/api/workspace-tree/node")
async def workspace_tree_node(
    file: str = Query(...), depth: int = Query(1, ge=0, le=3)
) -> dict[str, Any]:
    """Return a safe directory subtree under an allowed workspace root."""
    target = _workspace_tree_resolve(file)
    if not target.exists() or not target.is_dir():
        raise HTTPException(
            status_code=404, detail="workspace-tree directory not found"
        )
    roots = _workspace_tree_roots()
    for label, root in roots.items():
        try:
            target.relative_to(root)
        except ValueError:
            continue
        return {
            "ok": True,
            "root_label": label,
            "root": str(root),
            "tree": _workspace_tree_node(target, root, max_depth=depth),
        }
    raise HTTPException(status_code=403, detail="workspace-tree path blocked")


@app.get("/workspace-tree/index.js")
async def workspace_tree_index_js() -> PlainTextResponse:
    script = """
(() => {
  document.documentElement.dataset.workspaceTreeJs = 'loaded';
  const marker = document.createElement('p');
  marker.textContent = 'Workspace tree enhancement loaded.';
  marker.style.color = '#bae6fd';
  const main = document.querySelector('main[data-route="workspace-tree"]');
  if (main) main.appendChild(marker);
})();
""".strip()
    return PlainTextResponse(script, media_type="application/javascript")


@app.get("/workspace-tree")
async def workspace_tree_page(
    file: str = Query("docs/prismatic-production-durability-standard.md"),
) -> HTMLResponse:
    return HTMLResponse(_workspace_tree_html(file))


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
