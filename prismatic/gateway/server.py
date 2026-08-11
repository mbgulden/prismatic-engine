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
from contextlib import asynccontextmanager
import hashlib
import hmac as _hmac
import html
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from prismatic.agent_packet_normalizer import RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
from prismatic.agent_raw_output_queue import (
    get_raw_output,
    list_raw_outputs,
    mark_rerun_requested,
    queue_counts,
)
from prismatic.agent_raw_output_queue import (
    repair_preview as raw_output_repair_preview,
)
from prismatic.agy_activity import list_agy_activity_runs
from prismatic.agy_approved_action_executor import (
    ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
    build_approved_action_executor,
    get_approved_action_executor,
    latest_or_record_approved_action_executor,
    list_approved_action_executors,
    record_approved_action_executor,
)
from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    get_completed_work,
    ingest_completed_work,
    ingest_completed_work_text,
    list_completed_work,
)
from prismatic.agy_executor_runs import (
    PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_MARKER,
    build_prompt6_executor_canary_dry_run,
    get_executor_run,
    list_executor_runs,
    record_executor_run,
)
from prismatic.agy_final_action_authorization import (
    ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
    build_final_action_authorization,
    get_final_action_authorization,
    latest_or_record_final_action_authorization,
    list_final_action_authorizations,
    record_final_action_authorization,
)
from prismatic.agy_limited_overnight_runner import (
    AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
    LimitedOvernightRunStore,
    RunnerRequest,
    run_limited_overnight_dry_run,
)
from prismatic.agy_limited_overnight_runner import (
    status_payload as limited_overnight_status_payload,
)
from prismatic.agy_limited_overnight_runner import (
    stop_latest_run as stop_limited_overnight_run,
)
from prismatic.agy_merge_backlog import (
    AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER,
    AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
    build_operator_pr_creation_dry_run,
    build_pr_candidate_lifecycle,
    build_real_pr_creation_approval_gate,
    build_real_pr_creation_approved_action,
    execute_approved_real_pr_creation,
    get_merge_backlog_item,
    list_merge_backlog,
    verify_merge_backlog_item,
)
from prismatic.agy_operator_action_approval import (
    ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
    build_operator_action_approval,
    get_operator_action_approval,
    latest_or_record_operator_action_approval,
    list_operator_action_approvals,
    record_operator_action_approval,
)
from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    AgyOvernightGuardStore,
    evaluate_overnight_readiness,
    list_overnight_run_attempts,
    record_guard_decision,
    set_operator_pause,
)
from prismatic.agy_promotion_ledger import (
    ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
    build_promotion_decision,
    get_promotion_decision,
    latest_or_record_decision,
    list_promotion_decisions,
    record_promotion_decision,
)
from prismatic.agy_quarantined_execution_adapter import (
    ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
    build_quarantined_execution_adapter,
    get_quarantined_execution_adapter,
    latest_or_record_quarantined_execution_adapter,
    list_quarantined_execution_adapters,
    record_quarantined_execution_adapter,
)
from prismatic.agy_real_executor_arming_gate import (
    ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
    build_real_executor_arming_gate,
    get_real_executor_arming_gate,
    latest_or_record_real_executor_arming_gate,
    list_real_executor_arming_gates,
    record_real_executor_arming_gate,
)
from prismatic.agy_sandboxed_execution_canary import (
    ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
    build_sandboxed_execution_canary,
    get_sandboxed_execution_canary,
    latest_or_record_sandboxed_execution_canary,
    list_sandboxed_execution_canaries,
    record_sandboxed_execution_canary,
)
from prismatic.agy_unattended_window import (
    UnattendedWindowRequest,
    UnattendedWindowStore,
    approve_window,
    evaluate_unattended_window,
)
from prismatic.agy_unattended_window import (
    request_approval as request_unattended_window_approval,
)
from prismatic.agy_unattended_window import (
    set_pause as set_unattended_window_pause,
)
from prismatic.agy_unattended_window import (
    status_payload as unattended_window_status_payload,
)
from prismatic.api.routers.merge_factory import router as merge_factory_router
from prismatic.budget_caps import read_budget_caps, write_budget_caps
from prismatic.completed_work_gate import (
    completed_work_gate_schema,
    demo_completed_work_gate_state,
)
from prismatic.dispatcher import get_dispatcher_polling_budget_snapshot
from prismatic.gateway.control_auth import control_authorization_middleware
from prismatic.gateway.event_bus import get_event_bus
from prismatic.gateway.ipc_bridge import UnixSocketListener, create_event_ingest_route
from prismatic.gateway.workspace_tree import (
    RegistryError,
    WorkspaceTreeError,
    get_node,
    get_preview,
    list_workspaces,
    load_registry,
    resolve_legacy_file,
)
from prismatic.gateway.ws_broadcaster import (
    start_ws_broadcaster,
    stop_ws_broadcaster,
)
from prismatic.linear_rate_limit import (
    LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
    get_linear_rate_limit_snapshot,
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
from prismatic.verification.receipt_store import (
    PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
    get_verification_receipt,
    list_verification_receipts,
    persist_verification_receipt,
    revoke_verification_receipt,
    verification_receipt_counts,
    verification_receipt_schema,
)

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


def _linear_state_is_terminal(state: dict | None) -> bool:
    if not state:
        return False
    name = (state.get("name") or "").lower()
    stype = (state.get("type") or "").lower()
    return stype in ("completed", "canceled") or name in (
        "done",
        "duplicate",
        "canceled",
    )


def _prune_terminal_linear_pending(
    pending: dict, linear_states: dict
) -> tuple[dict, list, list]:
    active = {}
    pruned = []
    retained = []
    for ticket, info in pending.items():
        st = linear_states.get(ticket)
        if _linear_state_is_terminal(st):
            if info.get("force_keep_terminal"):
                active[ticket] = info
                retained.append(
                    {
                        "ticket": ticket,
                        "linear_state": st,
                        "rationale": info.get("terminal_keep_rationale", ""),
                    }
                )
            else:
                pruned.append({"ticket": ticket, "linear_state": st})
        else:
            active[ticket] = info
    return active, pruned, retained


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for EventBus, IPC bridge, WebSocket broadcaster, and store."""
    global _started_at, _run_store, _ipc_listener

    _started_at = time.time()

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
    yield
    if _ipc_listener:
        await _ipc_listener.stop()
        _ipc_listener = None

    stop_ws_broadcaster()


# ── FastAPI Application ──────────────────────────────────────────────


app = FastAPI(
    title="Prismatic Engine Gateway",
    description="HTTP/gRPC gateway for the Prismatic Engine orchestration hub",
    version="0.1.0",
    openapi_url=None,  # Disable OpenAPI schema generation — internal gateway
    lifespan=lifespan,
)

# One fail-closed boundary covers all current and future HTTP mutation routes.
# The middleware itself owns the narrow read-only and signed-webhook bypasses.
app.middleware("http")(control_authorization_middleware)
app.state.prismatic_control_authorization_installed = True


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
app.include_router(merge_factory_router, prefix="/api")

# ── Startup timestamp ──────────────────────────────────────────────
_started_at: float = 0.0

# ── Global state ───────────────────────────────────────────────────
_run_store: AgentRunRecordStore | None = None
_slack_bot: Any = None  # placeholder for future Slack integration
_ipc_listener: UnixSocketListener | None = None
_ws_clients: set[WebSocket] = set()


# ── Lifecycle Events managed via FastAPI lifespan context manager ──


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


@app.get("/api/agy/activity")
@app.get("/api/gateway/agy/activity")
def agy_activity(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Project durable exact-run AGY activity receipts for the dashboard."""
    return list_agy_activity_runs(limit=limit)


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
    if os.environ.get("PRISMATIC_WS_AUTH_REQUIRED", "1") in ("1", "true", "TRUE"):
        auth_hdr = websocket.headers.get("Authorization", "").strip()
        if not auth_hdr.startswith("Bearer "):
            await websocket.accept()
            await websocket.close(code=1008, reason="Unauthorized")
            return
        token = auth_hdr[7:].strip()
        allowed = [
            t.strip()
            for t in os.environ.get("PRISMATIC_WS_TOKENS", "").split(",")
            if t.strip()
        ]
        import secrets

        if not token or not any(secrets.compare_digest(token, t) for t in allowed):
            await websocket.accept()
            await websocket.close(code=1008, reason="Unauthorized")
            return

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


@app.get("/api/verification/receipts/schema")
@app.get("/api/gateway/verification/receipts/schema")
async def provider_neutral_verification_receipt_contract() -> dict[str, Any]:
    """Return the native provider-neutral verification receipt contract."""

    return verification_receipt_schema()


@app.get("/api/verification/receipts")
@app.get("/api/gateway/verification/receipts")
async def provider_neutral_verification_receipts(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return native receipts; hosted CI is optional metadata only."""

    rows = [row.as_dict() for row in list_verification_receipts(limit=limit)]
    return {
        "status": "ok",
        "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        "count": len(rows),
        "counts": verification_receipt_counts(),
        "receipts": rows,
        "acceptance_authority": "native_provider_neutral_receipt",
        "hosted_signals_required": False,
        "non_claims": {
            "github_required": False,
            "github_actions_required": False,
            "auto_merge": False,
            "auto_deploy": False,
        },
    }


@app.get("/api/verification/receipts/{receipt_id}")
@app.get("/api/gateway/verification/receipts/{receipt_id}")
async def provider_neutral_verification_receipt(receipt_id: str) -> dict[str, Any]:
    try:
        row = get_verification_receipt(receipt_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="verification receipt not found"
        ) from exc
    return {
        "status": "ok",
        "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        "receipt": row.as_dict(),
        "acceptance_authority": "native_provider_neutral_receipt",
    }


@app.post("/api/verification/receipts")
@app.post("/api/gateway/verification/receipts")
async def record_provider_neutral_verification_receipt(
    body: dict[str, Any],
) -> dict[str, Any]:
    """Persist one immutable native receipt without provider side effects."""

    receipt = body.get("receipt")
    policy = body.get("policy")
    if not isinstance(receipt, dict) or not isinstance(policy, dict):
        raise HTTPException(
            status_code=422, detail="receipt and policy must be objects"
        )
    try:
        row = persist_verification_receipt(
            receipt,
            policy,
            hosted_signals=body.get("hosted_signals"),
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "conflicting immutable" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return {
        "status": "accepted" if row.merge_eligible else "recorded_blocked",
        "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        "receipt": row.as_dict(),
        "side_effects": {
            "github": False,
            "github_actions": False,
            "linear": False,
            "merge": False,
            "deploy": False,
        },
    }


@app.post("/api/verification/receipts/{receipt_id}/revoke")
@app.post("/api/gateway/verification/receipts/{receipt_id}/revoke")
async def revoke_provider_neutral_verification_receipt(
    receipt_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Append an authenticated immutable native revocation event."""

    try:
        row = revoke_verification_receipt(
            receipt_id,
            reason=str(body.get("reason") or ""),
            revoked_by=str(body.get("revoked_by") or ""),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="verification receipt not found"
        ) from exc
    except ValueError as exc:
        status_code = 409 if "conflicting immutable" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "status": "revoked",
        "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        "receipt": row.as_dict(),
        "side_effects": {
            "github": False,
            "linear": False,
            "merge": False,
            "deploy": False,
        },
    }


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

    packet_text = body.get("completed_work_text") or body.get("log_text")
    if isinstance(packet_text, str):
        try:
            row = ingest_completed_work_text(packet_text)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
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


ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER = (
    "ONE_AGENT_COMPLETED_WORK_TO_DASHBOARD_LINEAR_DRY_RUN_OK"
)
ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER = (
    "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK"
)


def _one_agent_dashboard_linear_payload(
    completed_work_id: str, requested_by: str = "dashboard"
) -> dict[str, Any]:
    row = get_completed_work(completed_work_id)
    completed = row.as_dict()
    pr_dry_run = build_operator_pr_creation_dry_run(
        completed_work_id,
        requested_by=requested_by,
        action="one_agent_dashboard_linear_dry_run",
        linear_writeback=True,
    )
    linear_writeback = completed.get("linear_writeback") or {}
    pr_linear_writeback = pr_dry_run.get("linear_writeback") or {}
    dashboard = {
        "status": "ready"
        if completed.get("integration_classification") == "pass_ready_for_review"
        else "needs_attention",
        "agent": completed.get("agent"),
        "completed_work_id": completed_work_id,
        "issue_identifier": completed.get("packet", {}).get("issue_identifier"),
        "proof_marker": completed.get("proof_marker"),
        "integration_classification": completed.get("integration_classification"),
        "merge_backlog_action": pr_dry_run.get("pr_candidate", {}).get("action")
        or pr_dry_run.get("status"),
        "linear_writeback_status": linear_writeback.get("status"),
        "linear_writeback_posted": False,
        "linear_writeback_dry_run": True,
        "marker": ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER,
    }
    return {
        "status": "ok",
        "marker": ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER,
        "completed_work": completed,
        "dashboard": dashboard,
        "linear_writeback": {
            "posted": False,
            "dry_run": True,
            "source": "completed_work_bridge",
            "completed_work_payload": linear_writeback,
            "pr_dry_run_payload": pr_linear_writeback,
            "body": pr_linear_writeback.get("body") or linear_writeback.get("body"),
            "marker": ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER,
        },
        "pr_dry_run": pr_dry_run,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "git_branch_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
        },
        "non_claims": {
            "real_Linear_writeback_posted": False,
            "real_github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "bulk_agent_dispatch": False,
            "overnight_autopilot": False,
        },
    }


@app.get("/api/agy/completed-work/{completed_work_id}/dashboard-linear-dry-run")
@app.get("/api/gateway/agy/completed-work/{completed_work_id}/dashboard-linear-dry-run")
async def get_one_agent_completed_work_dashboard_linear_dry_run(
    completed_work_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return a one-agent dashboard + Linear writeback dry-run bridge payload."""

    try:
        return _one_agent_dashboard_linear_payload(
            completed_work_id, requested_by=requested_by
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.get("/api/agy/completed-work/dashboard-linear-dry-run/latest")
@app.get("/api/gateway/agy/completed-work/dashboard-linear-dry-run/latest")
async def get_latest_one_agent_completed_work_dashboard_linear_dry_run(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return the newest one-agent dashboard + Linear dry-run bridge payload."""

    rows = list_completed_work(limit=1)
    if not rows:
        return {
            "status": "empty",
            "marker": ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER,
            "completed_work": None,
            "dashboard": {
                "status": "empty",
                "marker": ONE_AGENT_DASHBOARD_LINEAR_DRY_RUN_MARKER,
            },
            "linear_writeback": {"posted": False, "dry_run": True},
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "git_branch_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
            },
        }
    return _one_agent_dashboard_linear_payload(rows[0].id, requested_by=requested_by)


def _one_agent_verified_pr_dry_run_payload(
    completed_work_id: str, requested_by: str = "dashboard"
) -> dict[str, Any]:
    dashboard_bridge = _one_agent_dashboard_linear_payload(
        completed_work_id, requested_by=requested_by
    )
    pr_dry_run = build_operator_pr_creation_dry_run(
        completed_work_id,
        requested_by=requested_by,
        action="one_agent_verified_pr_dry_run",
        linear_writeback=True,
    )
    verification = verify_merge_backlog_item(completed_work_id)
    verification_selection = pr_dry_run.get("verification_gate_selection") or {}
    commands = list(verification_selection.get("commands") or [])
    completed = dashboard_bridge.get("completed_work") or {}
    dashboard = dashboard_bridge.get("dashboard") or {}
    verification_passed = verification.get("verification_gate") == "pass"
    bridge_ready = dashboard.get("status") == "ready"
    status = "ready" if verification_passed and bridge_ready else "blocked"
    verification_artifact = {
        "status": "dry_run_verified" if status == "ready" else "blocked",
        "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
        "completed_work_id": completed_work_id,
        "verification_lane": verification.get("verification_lane"),
        "verification_gate": verification.get("verification_gate"),
        "selected_commands": commands,
        "proof_source": "completed_work_packet_and_merge_backlog_verify_gate",
        "proof_marker": completed.get("proof_marker"),
        "proof_result": completed.get("proof_result"),
        "executed_against_real_branch": False,
        "real_git_branch_created": False,
        "real_github_pr_created": False,
        "ad_hoc_or_canonical": "ad-hoc targeted",
    }
    linear_body = (
        "## One-agent verified PR dry-run\n\n"
        "```text\n"
        f"RESULT={'PASS' if status == 'ready' else 'BLOCKED'}\n"
        f"MARKER={ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER}\n"
        f"completed_work_id={completed_work_id}\n"
        f"verification_gate={verification.get('verification_gate')}\n"
        f"verification_lane={verification.get('verification_lane')}\n"
        "real_git_branch_created=false\n"
        "real_github_pr_created=false\n"
        "real_Linear_writeback_posted=false\n"
        "auto_merge_enabled=false\n"
        "production_deployed=false\n"
        "```"
    )
    side_effects = {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "bulk_agent_dispatch": False,
        "overnight_autopilot": False,
    }
    return {
        "status": "ok" if status == "ready" else "blocked",
        "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
        "completed_work": completed,
        "dashboard": {
            **dashboard,
            "status": status,
            "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
            "verified_pr_dry_run": status == "ready",
            "verification_gate": verification.get("verification_gate"),
            "verification_lane": verification.get("verification_lane"),
        },
        "dashboard_linear_dry_run": dashboard_bridge,
        "pr_dry_run": pr_dry_run,
        "verification": verification,
        "verification_artifact": verification_artifact,
        "linear_writeback": {
            "posted": False,
            "dry_run": True,
            "source": "verified_pr_dry_run_bridge",
            "target_issue": completed.get("packet", {}).get("issue_identifier"),
            "body": linear_body,
            "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
        },
        "side_effects": side_effects,
        "non_claims": {
            "real_github_pr_created": False,
            "real_git_branch_created": False,
            "real_Linear_writeback_posted": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "bulk_agent_dispatch": False,
            "overnight_autopilot": False,
            "canonical_full_suite_green": False,
        },
    }


@app.get("/api/agy/completed-work/{completed_work_id}/verified-pr-dry-run")
@app.get("/api/gateway/agy/completed-work/{completed_work_id}/verified-pr-dry-run")
async def get_one_agent_completed_work_verified_pr_dry_run(
    completed_work_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return a one-agent verified PR dry-run payload without side effects."""

    try:
        return _one_agent_verified_pr_dry_run_payload(
            completed_work_id, requested_by=requested_by
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.get("/api/agy/completed-work/verified-pr-dry-run/latest")
@app.get("/api/gateway/agy/completed-work/verified-pr-dry-run/latest")
async def get_latest_one_agent_completed_work_verified_pr_dry_run(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return the newest one-agent verified PR dry-run payload."""

    rows = list_completed_work(limit=1)
    if not rows:
        return {
            "status": "empty",
            "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
            "completed_work": None,
            "dashboard": {
                "status": "empty",
                "marker": ONE_AGENT_VERIFIED_PR_DRY_RUN_MARKER,
            },
            "linear_writeback": {"posted": False, "dry_run": True},
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "git_branch_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
                "bulk_agent_dispatch": False,
                "overnight_autopilot": False,
            },
        }
    return _one_agent_verified_pr_dry_run_payload(rows[0].id, requested_by=requested_by)


@app.get("/api/agy/completed-work/{completed_work_id}/promotion-decision/preview")
@app.get(
    "/api/gateway/agy/completed-work/{completed_work_id}/promotion-decision/preview"
)
async def preview_one_agent_promotion_decision(
    completed_work_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Preview the promotion decision ledger payload without persisting."""

    try:
        decision = build_promotion_decision(
            completed_work_id, requested_by=requested_by
        ).as_dict()
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
        "promotion_decision": decision,
        "persisted": False,
        "side_effects": decision["side_effects"],
    }


@app.post("/api/agy/completed-work/{completed_work_id}/promotion-decision")
@app.post("/api/gateway/agy/completed-work/{completed_work_id}/promotion-decision")
def record_one_agent_promotion_decision(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable promotion decision for one completed-work row."""

    body = payload or {}
    try:
        decision = record_promotion_decision(
            completed_work_id,
            requested_by=str(body.get("requested_by") or "dashboard"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
        "promotion_decision": decision,
        "persisted": True,
        "side_effects": decision["side_effects"],
    }


@app.get("/api/agy/promotion-decisions")
@app.get("/api/gateway/agy/promotion-decisions")
async def list_one_agent_promotion_decisions(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable promotion decision records."""

    records = list_promotion_decisions(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
        "count": len(records),
        "promotion_decisions": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "bulk_agent_dispatch": False,
        },
    }


@app.get("/api/agy/promotion-decisions/latest")
@app.get("/api/gateway/agy/promotion-decisions/latest")
async def latest_one_agent_promotion_decision(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return the latest ledger record, materializing one for the latest row if needed."""

    decision = latest_or_record_decision(requested_by=requested_by)
    if decision is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
            "promotion_decision": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "bulk_agent_dispatch": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
        "promotion_decision": decision,
        "side_effects": decision["side_effects"],
    }


@app.get("/api/agy/promotion-decisions/{promotion_decision_id}")
@app.get("/api/gateway/agy/promotion-decisions/{promotion_decision_id}")
async def get_one_agent_promotion_decision(
    promotion_decision_id: str,
) -> dict[str, Any]:
    """Return one durable promotion decision record."""

    decision = get_promotion_decision(promotion_decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="promotion decision not found")
    return {
        "status": "ok",
        "marker": ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
        "promotion_decision": decision,
        "side_effects": decision["side_effects"],
    }


@app.get("/api/agy/promotion-decisions/{promotion_decision_id}/operator-action/preview")
@app.get(
    "/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action/preview"
)
async def preview_one_agent_operator_action_approval(
    promotion_decision_id: str,
    operator_decision: str = Query(default="approve"),
    requested_by: str = Query(default="dashboard"),
    requested_action: str | None = Query(default=None),
) -> dict[str, Any]:
    """Preview an operator approve/reject/defer record without persisting."""

    try:
        approval = build_operator_action_approval(
            promotion_decision_id,
            operator_decision=operator_decision,
            requested_by=requested_by,
            requested_action=requested_action,
        ).as_dict()
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="promotion decision not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
        "operator_action_approval": approval,
        "persisted": False,
        "side_effects": approval["side_effects"],
    }


@app.post("/api/agy/promotion-decisions/{promotion_decision_id}/operator-action")
@app.post(
    "/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action"
)
def record_one_agent_operator_action_approval(
    promotion_decision_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable operator approve/reject/defer record."""

    body = payload or {}
    try:
        approval = record_operator_action_approval(
            promotion_decision_id,
            operator_decision=str(body.get("operator_decision") or "approve"),
            requested_by=str(body.get("requested_by") or "dashboard"),
            requested_action=body.get("requested_action"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="promotion decision not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
        "operator_action_approval": approval,
        "persisted": True,
        "side_effects": approval["side_effects"],
    }


@app.get("/api/agy/operator-action-approvals")
@app.get("/api/gateway/agy/operator-action-approvals")
async def list_one_agent_operator_action_approvals(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable operator action approval records."""

    records = list_operator_action_approvals(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
        "count": len(records),
        "operator_action_approvals": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "bulk_agent_dispatch": False,
        },
    }


@app.get("/api/agy/operator-action-approvals/latest")
@app.get("/api/gateway/agy/operator-action-approvals/latest")
async def latest_one_agent_operator_action_approval(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return the latest approval, materializing an approve preview record if needed."""

    approval = latest_or_record_operator_action_approval(requested_by=requested_by)
    if approval is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
            "operator_action_approval": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "bulk_agent_dispatch": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
        "operator_action_approval": approval,
        "side_effects": approval["side_effects"],
    }


@app.get("/api/agy/operator-action-approvals/{operator_action_approval_id}")
@app.get("/api/gateway/agy/operator-action-approvals/{operator_action_approval_id}")
async def get_one_agent_operator_action_approval(
    operator_action_approval_id: str,
) -> dict[str, Any]:
    """Return one durable operator action approval record."""

    approval = get_operator_action_approval(operator_action_approval_id)
    if approval is None:
        raise HTTPException(
            status_code=404, detail="operator action approval not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
        "operator_action_approval": approval,
        "side_effects": approval["side_effects"],
    }


@app.get(
    "/api/agy/operator-action-approvals/{operator_action_approval_id}/executor-dry-run/preview"
)
@app.get(
    "/api/gateway/agy/operator-action-approvals/{operator_action_approval_id}/executor-dry-run/preview"
)
async def preview_one_agent_approved_action_executor(
    operator_action_approval_id: str,
    requested_by: str = Query(default="dashboard"),
    executor_mode: str = Query(default="dry_run"),
    final_authorization_token: str | None = Query(default=None),
) -> dict[str, Any]:
    """Preview an approved-action executor dry-run request without persisting."""

    try:
        executor = build_approved_action_executor(
            operator_action_approval_id,
            requested_by=requested_by,
            executor_mode=executor_mode,
            final_authorization_token=final_authorization_token,
        ).as_dict()
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="operator action approval not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "approved_action_executor": executor,
        "persisted": False,
        "side_effects": executor["side_effects"],
    }


@app.post(
    "/api/agy/operator-action-approvals/{operator_action_approval_id}/executor-dry-run"
)
@app.post(
    "/api/gateway/agy/operator-action-approvals/{operator_action_approval_id}/executor-dry-run"
)
def record_one_agent_approved_action_executor(
    operator_action_approval_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable approved-action executor dry-run request."""

    body = payload or {}
    try:
        executor = record_approved_action_executor(
            operator_action_approval_id,
            requested_by=str(body.get("requested_by") or "dashboard"),
            executor_mode=str(body.get("executor_mode") or "dry_run"),
            final_authorization_token=body.get("final_authorization_token"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="operator action approval not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "approved_action_executor": executor,
        "persisted": True,
        "side_effects": executor["side_effects"],
    }


@app.get("/api/agy/approved-action-executors")
@app.get("/api/gateway/agy/approved-action-executors")
async def list_one_agent_approved_action_executors(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable approved-action executor dry-run requests."""

    records = list_approved_action_executors(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "count": len(records),
        "approved_action_executors": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
        },
    }


@app.get("/api/agy/approved-action-executors/latest")
@app.get("/api/gateway/agy/approved-action-executors/latest")
async def latest_one_agent_approved_action_executor(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return latest executor dry-run request, materializing one if possible."""

    executor = latest_or_record_approved_action_executor(requested_by=requested_by)
    if executor is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
            "approved_action_executor": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "approved_action_executor": executor,
        "side_effects": executor["side_effects"],
    }


@app.get("/api/agy/approved-action-executors/{approved_action_executor_id}")
@app.get("/api/gateway/agy/approved-action-executors/{approved_action_executor_id}")
async def get_one_agent_approved_action_executor(
    approved_action_executor_id: str,
) -> dict[str, Any]:
    """Return one durable approved-action executor dry-run request."""

    executor = get_approved_action_executor(approved_action_executor_id)
    if executor is None:
        raise HTTPException(
            status_code=404, detail="approved action executor not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
        "approved_action_executor": executor,
        "side_effects": executor["side_effects"],
    }


@app.get(
    "/api/agy/approved-action-executors/{approved_action_executor_id}/final-authorization/preview"
)
@app.get(
    "/api/gateway/agy/approved-action-executors/{approved_action_executor_id}/final-authorization/preview"
)
async def preview_one_agent_final_action_authorization(
    approved_action_executor_id: str,
    authorization_decision: str = Query(default="authorize"),
    requested_by: str = Query(default="dashboard"),
    authorization_token: str | None = Query(default=None),
) -> dict[str, Any]:
    """Preview final authorization for an approved-action executor without persisting."""

    try:
        authorization = build_final_action_authorization(
            approved_action_executor_id,
            authorization_decision=authorization_decision,
            requested_by=requested_by,
            authorization_token=authorization_token,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="approved action executor not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
        "final_action_authorization": authorization,
        "persisted": False,
        "side_effects": authorization["side_effects"],
    }


@app.post(
    "/api/agy/approved-action-executors/{approved_action_executor_id}/final-authorization"
)
@app.post(
    "/api/gateway/agy/approved-action-executors/{approved_action_executor_id}/final-authorization"
)
def record_one_agent_final_action_authorization(
    approved_action_executor_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable final authorization gate record."""

    body = payload or {}
    try:
        authorization = record_final_action_authorization(
            approved_action_executor_id,
            authorization_decision=str(
                body.get("authorization_decision") or "authorize"
            ),
            requested_by=str(body.get("requested_by") or "dashboard"),
            authorization_token=body.get("authorization_token"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="approved action executor not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
        "final_action_authorization": authorization,
        "persisted": True,
        "side_effects": authorization["side_effects"],
    }


@app.get("/api/agy/final-action-authorizations")
@app.get("/api/gateway/agy/final-action-authorizations")
async def list_one_agent_final_action_authorizations(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable final action authorization gate records."""

    records = list_final_action_authorizations(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
        "count": len(records),
        "final_action_authorizations": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "real_executor_invoked": False,
        },
    }


@app.get("/api/agy/final-action-authorizations/latest")
@app.get("/api/gateway/agy/final-action-authorizations/latest")
async def latest_one_agent_final_action_authorization(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return latest final authorization, auto-recording a safe default if possible."""

    authorization = latest_or_record_final_action_authorization(
        requested_by=requested_by
    )
    if authorization is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
            "final_action_authorization": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
                "real_executor_invoked": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
        "final_action_authorization": authorization,
        "side_effects": authorization["side_effects"],
    }


@app.get("/api/agy/final-action-authorizations/{final_action_authorization_id}")
@app.get("/api/gateway/agy/final-action-authorizations/{final_action_authorization_id}")
async def get_one_agent_final_action_authorization(
    final_action_authorization_id: str,
) -> dict[str, Any]:
    """Return one durable final action authorization record."""

    authorization = get_final_action_authorization(final_action_authorization_id)
    if authorization is None:
        raise HTTPException(
            status_code=404, detail="final action authorization not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
        "final_action_authorization": authorization,
        "side_effects": authorization["side_effects"],
    }


@app.get(
    "/api/agy/final-action-authorizations/{final_action_authorization_id}/quarantined-adapter/preview"
)
@app.get(
    "/api/gateway/agy/final-action-authorizations/{final_action_authorization_id}/quarantined-adapter/preview"
)
async def preview_one_agent_quarantined_execution_adapter(
    final_action_authorization_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Preview quarantined execution adapter envelope without persisting."""

    try:
        adapter = build_quarantined_execution_adapter(
            final_action_authorization_id, requested_by=requested_by
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="final action authorization not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "quarantined_execution_adapter": adapter,
        "persisted": False,
        "side_effects": adapter["side_effects"],
    }


@app.post(
    "/api/agy/final-action-authorizations/{final_action_authorization_id}/quarantined-adapter"
)
@app.post(
    "/api/gateway/agy/final-action-authorizations/{final_action_authorization_id}/quarantined-adapter"
)
def record_one_agent_quarantined_execution_adapter(
    final_action_authorization_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable quarantined execution adapter preview record."""

    body = payload or {}
    try:
        adapter = record_quarantined_execution_adapter(
            final_action_authorization_id,
            requested_by=str(body.get("requested_by") or "dashboard"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="final action authorization not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "quarantined_execution_adapter": adapter,
        "persisted": True,
        "side_effects": adapter["side_effects"],
    }


@app.get("/api/agy/quarantined-execution-adapters")
@app.get("/api/gateway/agy/quarantined-execution-adapters")
async def list_one_agent_quarantined_execution_adapters(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable quarantined execution adapter preview records."""

    records = list_quarantined_execution_adapters(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "count": len(records),
        "quarantined_execution_adapters": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "real_executor_invoked": False,
            "executed": False,
        },
    }


@app.get("/api/agy/quarantined-execution-adapters/latest")
@app.get("/api/gateway/agy/quarantined-execution-adapters/latest")
async def latest_one_agent_quarantined_execution_adapter(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return latest quarantined adapter, auto-recording a safe default if possible."""

    adapter = latest_or_record_quarantined_execution_adapter(requested_by=requested_by)
    if adapter is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
            "quarantined_execution_adapter": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
                "real_executor_invoked": False,
                "executed": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "quarantined_execution_adapter": adapter,
        "side_effects": adapter["side_effects"],
    }


@app.get("/api/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}")
@app.get(
    "/api/gateway/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}"
)
async def get_one_agent_quarantined_execution_adapter(
    quarantined_execution_adapter_id: str,
) -> dict[str, Any]:
    """Return one durable quarantined execution adapter preview record."""

    adapter = get_quarantined_execution_adapter(quarantined_execution_adapter_id)
    if adapter is None:
        raise HTTPException(
            status_code=404, detail="quarantined execution adapter not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
        "quarantined_execution_adapter": adapter,
        "side_effects": adapter["side_effects"],
    }


@app.get(
    "/api/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}/sandbox-canary/preview"
)
@app.get(
    "/api/gateway/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}/sandbox-canary/preview"
)
async def preview_one_agent_sandboxed_execution_canary(
    quarantined_execution_adapter_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Preview sandboxed execution canary without persisting."""

    try:
        canary = build_sandboxed_execution_canary(
            quarantined_execution_adapter_id, requested_by=requested_by
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="quarantined execution adapter not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        "sandboxed_execution_canary": canary,
        "persisted": False,
        "side_effects": canary["side_effects"],
    }


@app.post(
    "/api/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}/sandbox-canary"
)
@app.post(
    "/api/gateway/agy/quarantined-execution-adapters/{quarantined_execution_adapter_id}/sandbox-canary"
)
def record_one_agent_sandboxed_execution_canary(
    quarantined_execution_adapter_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable sandboxed execution canary record."""

    body = payload or {}
    try:
        canary = record_sandboxed_execution_canary(
            quarantined_execution_adapter_id,
            requested_by=str(body.get("requested_by") or "dashboard"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="quarantined execution adapter not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        "sandboxed_execution_canary": canary,
        "persisted": True,
        "side_effects": canary["side_effects"],
    }


@app.get("/api/agy/sandboxed-execution-canaries")
@app.get("/api/gateway/agy/sandboxed-execution-canaries")
async def list_one_agent_sandboxed_execution_canaries(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable sandboxed execution canary records."""

    records = list_sandboxed_execution_canaries(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        "count": len(records),
        "sandboxed_execution_canaries": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "real_executor_invoked": False,
            "executed": False,
        },
    }


@app.get("/api/agy/sandboxed-execution-canaries/latest")
@app.get("/api/gateway/agy/sandboxed-execution-canaries/latest")
async def latest_one_agent_sandboxed_execution_canary(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return latest sandbox canary, auto-recording a safe default if possible."""

    canary = latest_or_record_sandboxed_execution_canary(requested_by=requested_by)
    if canary is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
            "sandboxed_execution_canary": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
                "real_executor_invoked": False,
                "executed": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        "sandboxed_execution_canary": canary,
        "side_effects": canary["side_effects"],
    }


@app.get("/api/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}")
@app.get(
    "/api/gateway/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}"
)
async def get_one_agent_sandboxed_execution_canary(
    sandboxed_execution_canary_id: str,
) -> dict[str, Any]:
    """Return one durable sandboxed execution canary record."""

    canary = get_sandboxed_execution_canary(sandboxed_execution_canary_id)
    if canary is None:
        raise HTTPException(
            status_code=404, detail="sandboxed execution canary not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
        "sandboxed_execution_canary": canary,
        "side_effects": canary["side_effects"],
    }


@app.get(
    "/api/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}/real-executor-arming/preview"
)
@app.get(
    "/api/gateway/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}/real-executor-arming/preview"
)
async def preview_one_agent_real_executor_arming_gate(
    sandboxed_execution_canary_id: str,
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Preview real executor arming readiness without persisting."""

    try:
        gate = build_real_executor_arming_gate(
            sandboxed_execution_canary_id, requested_by=requested_by
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="sandboxed execution canary not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        "real_executor_arming_gate": gate,
        "persisted": False,
        "side_effects": gate["side_effects"],
    }


@app.post(
    "/api/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}/real-executor-arming"
)
@app.post(
    "/api/gateway/agy/sandboxed-execution-canaries/{sandboxed_execution_canary_id}/real-executor-arming"
)
def record_one_agent_real_executor_arming_gate(
    sandboxed_execution_canary_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist a durable real executor arming readiness gate."""

    body = payload or {}
    try:
        gate = record_real_executor_arming_gate(
            sandboxed_execution_canary_id,
            requested_by=str(body.get("requested_by") or "dashboard"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="sandboxed execution canary not found"
        ) from exc
    return {
        "status": "ok",
        "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        "real_executor_arming_gate": gate,
        "persisted": True,
        "side_effects": gate["side_effects"],
    }


@app.get("/api/agy/real-executor-arming-gates")
@app.get("/api/gateway/agy/real-executor-arming-gates")
async def list_one_agent_real_executor_arming_gates(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List durable real executor arming gate records."""

    records = list_real_executor_arming_gates(limit=limit)
    return {
        "status": "ok",
        "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        "count": len(records),
        "real_executor_arming_gates": records,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "real_executor_armed": False,
            "real_executor_invoked": False,
            "executed": False,
        },
    }


@app.get("/api/agy/real-executor-arming-gates/latest")
@app.get("/api/gateway/agy/real-executor-arming-gates/latest")
async def latest_one_agent_real_executor_arming_gate(
    requested_by: str = Query(default="dashboard"),
) -> dict[str, Any]:
    """Return latest real executor arming gate, auto-recording safe default."""

    gate = latest_or_record_real_executor_arming_gate(requested_by=requested_by)
    if gate is None:
        return {
            "status": "empty",
            "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
            "real_executor_arming_gate": None,
            "side_effects": {
                "linear_comment_posted": False,
                "github_pr_created": False,
                "auto_merge_enabled": False,
                "production_deployed": False,
                "real_executor_armed": False,
                "real_executor_invoked": False,
                "executed": False,
            },
        }
    return {
        "status": "ok",
        "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        "real_executor_arming_gate": gate,
        "side_effects": gate["side_effects"],
    }


@app.get("/api/agy/real-executor-arming-gates/{real_executor_arming_gate_id}")
@app.get("/api/gateway/agy/real-executor-arming-gates/{real_executor_arming_gate_id}")
async def get_one_agent_real_executor_arming_gate(
    real_executor_arming_gate_id: str,
) -> dict[str, Any]:
    """Return one durable real executor arming gate record."""

    gate = get_real_executor_arming_gate(real_executor_arming_gate_id)
    if gate is None:
        raise HTTPException(
            status_code=404, detail="real executor arming gate not found"
        )
    return {
        "status": "ok",
        "marker": ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
        "real_executor_arming_gate": gate,
        "side_effects": gate["side_effects"],
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
        result = execute_approved_real_pr_creation(
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
        side_effects = (
            result.get("side_effects", {})
            if isinstance(result.get("side_effects"), dict)
            else {}
        )
        executor_result = (
            result.get("executor_result", {})
            if isinstance(result.get("executor_result"), dict)
            else {}
        )
        result.setdefault(
            "real_github_pr_created",
            bool(
                executor_result.get("real_github_pr_created")
                or side_effects.get("real_github_pr_created")
                or side_effects.get("github_pr_created")
            ),
        )
        result.setdefault(
            "git_branch_created",
            bool(
                executor_result.get("git_branch_created")
                or side_effects.get("git_branch_created")
            ),
        )
        result.setdefault("auto_merge_enabled", bool(side_effects.get("auto_merge")))
        result.setdefault(
            "production_deployed", bool(side_effects.get("production_deploy"))
        )
        result.setdefault("AGY_dispatch", False)
        audit_run = record_executor_run(
            result,
            requested_by=str(body.get("requested_by") or "operator"),
            log_path=str(body.get("log_path") or "") or None,
        )
        result["audit_writeback"] = {
            "status": "ok",
            "marker": PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_MARKER,
            "recorded": True,
            "run_id": audit_run["run_id"],
            "executor_run_marker": audit_run["marker"],
        }
        result["executor_run_id"] = audit_run["run_id"]
        return result
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="completed work row not found"
        ) from exc


@app.post("/api/gateway/agy/merge-backlog/{completed_work_id}/pr-executor")
def gateway_agy_merge_backlog_approved_real_pr_executor(
    completed_work_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return agy_merge_backlog_approved_real_pr_executor(completed_work_id, payload)


@app.get("/api/agy/executor-runs")
def agy_executor_runs(limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    return list_executor_runs(limit=limit)


@app.get("/api/gateway/agy/executor-runs")
def gateway_agy_executor_runs(limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    return agy_executor_runs(limit=limit)


@app.get("/api/agy/executor-runs/{run_id}")
def agy_executor_run_detail(run_id: str) -> dict[str, Any]:
    try:
        return get_executor_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="executor run not found") from exc


@app.get("/api/gateway/agy/executor-runs/{run_id}")
def gateway_agy_executor_run_detail(run_id: str) -> dict[str, Any]:
    return agy_executor_run_detail(run_id)


@app.post("/api/agy/executor-runs/canary-dry-run")
def agy_executor_runs_canary_dry_run(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    return build_prompt6_executor_canary_dry_run(
        completed_work_id=str(body.get("completed_work_id") or "") or None,
        requested_by=str(body.get("requested_by") or "dashboard"),
        executor_mode=str(body.get("executor_mode") or "dry_run"),
        execute=bool(body.get("execute", False)),
        allow_real_side_effects=bool(body.get("allow_real_side_effects", False)),
        log_path=str(body.get("log_path") or "") or None,
    )


@app.post("/api/gateway/agy/executor-runs/canary-dry-run")
def gateway_agy_executor_runs_canary_dry_run(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return agy_executor_runs_canary_dry_run(payload)


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


# ── Authenticated durable task admission ───────────────────────


def _task_admission_error(exc: Exception) -> JSONResponse:
    from prismatic.task_admission import TaskAdmissionError

    if isinstance(exc, TaskAdmissionError):
        return JSONResponse(
            {"ok": False, "error": exc.code}, status_code=exc.status_code
        )
    logger.error("task admission failed", exc_info=True)
    return JSONResponse(
        {"ok": False, "error": "admission_internal_error"}, status_code=500
    )


@app.post("/api/dashboard/task-admissions", response_model=None)
async def create_task_admission(request: Request) -> dict[str, Any] | JSONResponse:
    """Atomically record one exact operator admission and pending outbox event.

    This route records durable intent only. It never launches a producer.
    """

    from prismatic.task_admission import (
        MAX_ADMISSION_BODY_BYTES,
        TaskAdmissionError,
        TaskAdmissionStore,
        parse_admission_json,
    )

    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return JSONResponse(
            {"ok": False, "error": "content_type_required"}, status_code=415
        )
    try:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise TaskAdmissionError("invalid_body_size", 413) from exc
            if declared_size < 0 or declared_size > MAX_ADMISSION_BODY_BYTES:
                raise TaskAdmissionError("invalid_body_size", 413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_ADMISSION_BODY_BYTES:
                raise TaskAdmissionError("invalid_body_size", 413)
            body.extend(chunk)
        payload = parse_admission_json(bytes(body))
        result = TaskAdmissionStore().admit(
            payload,
            header_key=request.headers.get("Idempotency-Key", ""),
            actor=str(request.state.control_actor),
        )
    except Exception as exc:
        return _task_admission_error(exc)
    return JSONResponse(
        {
            "ok": True,
            "replayed": result.replayed,
            "launch_performed": False,
            "record": result.record,
        },
        status_code=200 if result.replayed else 201,
    )


@app.post(
    "/api/dashboard/task-admissions/{task_id}/terminal-reconciliation",
    response_model=None,
)
async def reconcile_terminal_task_admission(
    task_id: str, request: Request
) -> dict[str, Any] | JSONResponse:
    """Terminalize a failed admission launch without synthesizing success."""

    from prismatic.task_admission import TaskAdmissionError, TaskAdmissionStore
    from prismatic.task_admission_consumer import (
        MAX_TERMINAL_RECONCILIATION_BODY_BYTES,
        TaskAdmissionConsumer,
        parse_terminal_reconciliation_json,
    )

    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return JSONResponse(
            {"ok": False, "error": "content_type_required"}, status_code=415
        )
    try:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise TaskAdmissionError("invalid_body_size", 413) from exc
            if (
                declared_size < 0
                or declared_size > MAX_TERMINAL_RECONCILIATION_BODY_BYTES
            ):
                raise TaskAdmissionError("invalid_body_size", 413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_TERMINAL_RECONCILIATION_BODY_BYTES:
                raise TaskAdmissionError("invalid_body_size", 413)
            body.extend(chunk)
        payload = parse_terminal_reconciliation_json(bytes(body))
        if payload["task_id"] != task_id:
            raise TaskAdmissionError("terminal_reconciliation_task_id_mismatch", 409)
        admission_store = TaskAdmissionStore()
        if admission_store.policy_path is None:
            raise TaskAdmissionError("terminal_reconciliation_policy_unavailable", 503)
        result = TaskAdmissionConsumer(
            db_path=admission_store.db_path,
            policy_path=admission_store.policy_path,
            identity=f"terminal-reconcile:{request.state.control_actor}",
        ).terminal_reconcile(payload)
    except Exception as exc:
        return _task_admission_error(exc)
    return {
        "ok": True,
        "replayed": result.replayed,
        "launch_performed": False,
        "record": result.record,
    }


@app.get("/api/dashboard/task-admissions", response_model=None)
async def list_task_admissions(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any] | JSONResponse:
    """Return operator-protected durable admission history."""

    from prismatic.task_admission import TaskAdmissionStore

    try:
        records = TaskAdmissionStore().list(limit=limit)
    except Exception as exc:
        return _task_admission_error(exc)
    return {"ok": True, "count": len(records), "records": records}


@app.get("/api/dashboard/task-admissions/{task_id}", response_model=None)
async def get_task_admission(task_id: str) -> dict[str, Any] | JSONResponse:
    """Return one operator-protected durable admission row."""

    from prismatic.task_admission import TaskAdmissionStore

    try:
        record = TaskAdmissionStore().get(task_id)
    except Exception as exc:
        return _task_admission_error(exc)
    if record is None:
        return JSONResponse(
            {"ok": False, "error": "task_admission_not_found"}, status_code=404
        )
    return {"ok": True, "record": record}


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


@app.get("/api/gateway/agents/governance-status")
async def gateway_agents_governance_status() -> dict[str, Any]:
    """Return no-side-effect Kai/Fred governance status for the dashboard."""
    from prismatic.agent_governance_status import build_agent_governance_status

    inputs = _dashboard_agent_inputs()
    return build_agent_governance_status(
        agents=("kai", "fred"),
        run_records=inputs["run_records"],
        registry=inputs["registry"],
    )


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
    from prismatic.recovery_runtime import consumer_runtime_status

    payload = recovery_status_payload(
        _read_dashboard_recovery_state(),
        _run_records_for_dashboard(),
        counters=_webhook_counters,
    )
    payload.update(consumer_runtime_status())
    return payload


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


@app.post("/api/skills/upload", response_model=None)
@app.post("/api/gateway/skills/upload", response_model=None)
async def dashboard_skill_upload(request: Request) -> dict[str, Any]:
    try:
        from prismatic.skills import upload_skill
        body = await request.json()
        name = body.get("name", "custom-skill")
        content = body.get("content", "")
        manifest = upload_skill(name, content)

        try:
            from prismatic.gateway.event_bus import SwarmEvent, get_event_bus
            get_event_bus().publish(SwarmEvent("skills.synced", {"skill": name}))
        except Exception:
            pass

        return {"ok": True, "status": "uploaded", "skill": manifest}
    except Exception as exc:
        return JSONResponse({"error": "upload_failed", "detail": str(exc)}, status_code=400)


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


@app.get("/api/jules/capacity")
@app.get("/api/gateway/jules/capacity")
async def dashboard_jules_capacity() -> dict[str, Any]:
    from prismatic.jules_capacity import capacity_payload

    return capacity_payload()


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
@app.get("/api/gateway/overnight-report/latest", response_model=None)
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
        # GitHub signs the exact raw request body with HMAC-SHA256. The
        # ``X-Hub-Signature-256`` header name is not part of the signed bytes.
        # See GitHub's webhook-validation contract.
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
    from fastapi import HTTPException

    from prismatic.capabilities.chat_agy import ChatAGYCapability

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
    from fastapi.responses import JSONResponse

    from prismatic.schedules import UnauthorizedMutationError, request_schedule_mutation

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


# ── Opaque, descriptor-relative workspace-tree boundary ─────────────────


def _workspace_http_error(exc: WorkspaceTreeError) -> HTTPException:
    """Translate only path-free workspace boundary errors to HTTP."""
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _workspace_tree_html(workspace_id: str, relative_path: str) -> str:
    preview: dict[str, Any] = {"ok": False}
    status = "Select an opaque workspace and relative file path in the dashboard."
    if workspace_id and relative_path:
        try:
            with load_registry() as registry:
                preview = get_preview(registry, workspace_id, relative_path)
            status = "Loaded preview"
        except WorkspaceTreeError as exc:
            status = exc.detail
    safe_name = html.escape(str(preview.get("relative_path", "No file selected")))
    safe_content = html.escape(str(preview.get("content", "")))
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
    <p>This contained read-only route uses opaque workspace identifiers and relative paths.</p>
  </section>
  <section class=\"card\">
    <h2>{safe_name}</h2>
    <p>{html.escape(status)}</p>
    <p><a href=\"/dashboard#workspaces\">Open canonical Workspaces dashboard</a></p>
    <pre>{safe_content}</pre>
  </section>
</main>
</body>
</html>"""


_GOVERNANCE_DASHBOARD_HTML = (
    Path(__file__).resolve().parent / "templates" / "dashboard.html"
)
_GOVERNANCE_DASHBOARD_CSS = Path(__file__).resolve().parent / "static" / "dashboard.css"


def _serve_governance_dashboard_html() -> HTMLResponse:
    """Serve the canonical Prismatic governance/control-plane dashboard."""
    if not _GOVERNANCE_DASHBOARD_HTML.exists():
        return HTMLResponse(
            "Prismatic governance dashboard HTML not found",
            status_code=404,
        )
    return HTMLResponse(_GOVERNANCE_DASHBOARD_HTML.read_text(encoding="utf-8"))


@app.get("/static/dashboard.css", response_class=Response)
async def serve_governance_dashboard_css() -> Response:
    """Serve the built dashboard CSS without a browser-time Tailwind runtime."""
    if not _GOVERNANCE_DASHBOARD_CSS.exists():
        return Response(
            "Prismatic governance dashboard CSS not found",
            status_code=404,
            media_type="text/plain",
        )
    return Response(
        _GOVERNANCE_DASHBOARD_CSS.read_bytes(),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/", response_class=HTMLResponse)
async def serve_governance_index() -> HTMLResponse:
    """Governance gateway root: serve the canonical dashboard, not fallback shell."""
    return _serve_governance_dashboard_html()


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/workspaces", response_class=HTMLResponse)
@app.get("/tasks", response_class=HTMLResponse)
@app.get("/telemetry", response_class=HTMLResponse)
@app.get("/merge", response_class=HTMLResponse)
@app.get("/foundation", response_class=HTMLResponse)
@app.get("/skills", response_class=HTMLResponse)
@app.get("/signals", response_class=HTMLResponse)
@app.get("/crons", response_class=HTMLResponse)
@app.get("/pwp", response_class=HTMLResponse)
@app.get("/plugins", response_class=HTMLResponse)
@app.get("/quota", response_class=HTMLResponse)
@app.get("/tab/{tab_name}", response_class=HTMLResponse)
async def serve_governance_tab(tab_name: str | None = None) -> HTMLResponse:
    """Serve the canonical dashboard UI for clean top-level tab routes."""
    return _serve_governance_dashboard_html()


@app.get("/api/workspaces")
async def workspace_tree_workspaces() -> dict[str, Any]:
    try:
        with load_registry() as registry:
            return list_workspaces(registry)
    except RegistryError as exc:
        raise _workspace_http_error(exc) from None


@app.get("/workspaces")
async def legacy_workspaces_deep_link(file: str = Query(...)) -> RedirectResponse:
    """Redirect retired workspace links into the canonical Hub Workspaces tab."""
    query = urlencode({"file": file})
    return RedirectResponse(url=f"/dashboard?{query}#workspaces", status_code=307)


@app.get("/api/workspace-tree/resolve")
async def workspace_tree_resolve(file: str = Query(...)) -> dict[str, Any]:
    """Resolve a legacy relative file path to an opaque workspace identifier."""
    try:
        with load_registry() as registry:
            return resolve_legacy_file(registry, file)
    except WorkspaceTreeError as exc:
        raise _workspace_http_error(exc) from None


@app.get("/api/workspace-tree/preview")
async def workspace_tree_preview(
    workspace_id: str = Query(...), path: str = Query(...)
) -> dict[str, Any]:
    try:
        with load_registry() as registry:
            return get_preview(registry, workspace_id, path)
    except WorkspaceTreeError as exc:
        raise _workspace_http_error(exc) from None


@app.get("/api/workspace-tree/node")
async def workspace_tree_node(
    workspace_id: str = Query(...),
    path: str = Query(""),
    depth: int = Query(1, ge=0, le=3),
) -> dict[str, Any]:
    """Return a bounded descriptor-relative subtree."""
    try:
        with load_registry() as registry:
            return get_node(registry, workspace_id, path, depth)
    except WorkspaceTreeError as exc:
        raise _workspace_http_error(exc) from None


@app.get("/workspace-tree")
async def workspace_tree_page(
    workspace_id: str = Query(""), path: str = Query("")
) -> HTMLResponse:
    return HTMLResponse(_workspace_tree_html(workspace_id, path))


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


@app.get("/api/pwp/kpi/sites")
async def pwp_kpi_list_sites() -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import list_sites

        return {"sites": list_sites()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/pwp/kpi/sites/{slug}")
async def pwp_kpi_get_site(slug: str) -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import load_site

        return load_site(slug)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Site KPI collection not found for slug: {slug}"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/pwp/kpi/refresh")
async def pwp_kpi_refresh() -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import list_sites

        return {"status": "ok", "sites_refreshed": len(list_sites())}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/pwp/kpi/publish-dashboard")
async def pwp_kpi_publish_dashboard(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        publish_root = body.get("publish_root", "")
        if not publish_root:
            raise HTTPException(status_code=400, detail="publish_root required")
        from plugins.pwp.capabilities.publish_kpi_tracker import (
            publish_publish_kpi_dashboard,
        )

        return publish_publish_kpi_dashboard(publish_root)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


from prismatic.review_factory.routes import (  # noqa: E402
    create_review_factory_router,
)
from prismatic.workspace.routes import create_workspace_router  # noqa: E402
from prismatic.deploy.routes import create_deploy_router  # noqa: E402

_rf_router = create_review_factory_router()
if _rf_router:
    app.include_router(_rf_router, prefix="/api")

_ws_router = create_workspace_router()
if _ws_router:
    app.include_router(_ws_router, prefix="/api")

_dep_router = create_deploy_router()
if _dep_router:
    app.include_router(_dep_router, prefix="/api")


@app.get("/api/workspace/tree")
async def gateway_workspace_tree(
    workspace_id: str | None = Query(default=None),
    path: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        if not workspace_id:
            try:
                workspaces = list_workspaces()
            except Exception:
                workspaces = []

            by_category: dict[str, list[dict[str, Any]]] = {}
            total_entries = 0
            try:
                from prismatic.workspace.routes import (
                    WorkspaceTreeWalker,
                    default_deployed_docs_root,
                )

                walker = WorkspaceTreeWalker(docs_root=default_deployed_docs_root())
                entries = walker.walk()
                total_entries = len(entries)
                for entry in entries:
                    cat = entry.category
                    by_category.setdefault(cat, []).append(entry.to_dict())
            except Exception:
                pass

            return {
                "workspaces": workspaces,
                "total_entries": total_entries,
                "by_category": by_category,
                "timestamp": time.time(),
            }
        return get_node(workspace_id=workspace_id, rel_path=path or "")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/deploy/status")
async def gateway_deploy_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "deploy_receiver": "active",
        "mode": "standalone",
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    main()
