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
import html
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from prismatic.api.routers.merge_factory import router as merge_factory_router
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
)
from prismatic.gateway.ws_broadcaster import (
    start_ws_broadcaster,
    stop_ws_broadcaster,
)
from prismatic.verification.receipt_store import (
    PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
    get_verification_receipt,
    list_verification_receipts,
    persist_verification_receipt,
    revoke_verification_receipt,
    verification_receipt_counts,
    verification_receipt_schema,
)
from prismatic.agy_activity import list_agy_activity_runs
from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    get_completed_work,
    ingest_completed_work,
    ingest_completed_work_text,
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
from prismatic.agy_promotion_ledger import (
    ONE_AGENT_PROMOTION_DECISION_LEDGER_MARKER,
    build_promotion_decision,
    get_promotion_decision,
    latest_or_record_decision,
    list_promotion_decisions,
    record_promotion_decision,
)
from prismatic.agy_operator_action_approval import (
    ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_MARKER,
    build_operator_action_approval,
    get_operator_action_approval,
    latest_or_record_operator_action_approval,
    list_operator_action_approvals,
    record_operator_action_approval,
)
from prismatic.agy_approved_action_executor import (
    ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_MARKER,
    build_approved_action_executor,
    get_approved_action_executor,
    latest_or_record_approved_action_executor,
    list_approved_action_executors,
    record_approved_action_executor,
)
from prismatic.agy_final_action_authorization import (
    ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_MARKER,
    build_final_action_authorization,
    get_final_action_authorization,
    latest_or_record_final_action_authorization,
    list_final_action_authorizations,
    record_final_action_authorization,
)
from prismatic.agy_quarantined_execution_adapter import (
    ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_MARKER,
    build_quarantined_execution_adapter,
    get_quarantined_execution_adapter,
    latest_or_record_quarantined_execution_adapter,
    list_quarantined_execution_adapters,
    record_quarantined_execution_adapter,
)
from prismatic.agy_sandboxed_execution_canary import (
    ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_MARKER,
    build_sandboxed_execution_canary,
    get_sandboxed_execution_canary,
    latest_or_record_sandboxed_execution_canary,
    list_sandboxed_execution_canaries,
    record_sandboxed_execution_canary,
)
from prismatic.agy_real_executor_arming_gate import (
    ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_MARKER,
    build_real_executor_arming_gate,
    get_real_executor_arming_gate,
    latest_or_record_real_executor_arming_gate,
    list_real_executor_arming_gates,
    record_real_executor_arming_gate,
)
from prismatic.agy_executor_runs import (
    PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_MARKER,
    build_prompt6_executor_canary_dry_run,
    get_executor_run,
    list_executor_runs,
    record_executor_run,
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


def _linear_state_is_terminal(state: dict | None) -> bool:
    if not state:
        return False
    name = (state.get("name") or "").lower()
    stype = (state.get("type") or "").lower()
    return stype in ("completed", "canceled") or name in ("done", "duplicate", "canceled")


def _prune_terminal_linear_pending(pending: dict, linear_states: dict) -> tuple[dict, list, list]:
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


# ── FastAPI Application ──────────────────────────────────────────────


app = FastAPI(
    title="Prismatic Engine Gateway",
    description="HTTP/gRPC gateway for the Prismatic Engine orchestration hub",
    version="0.1.0",
    openapi_url=None,  # Disable OpenAPI schema generation — internal gateway
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

try:
    from prismatic.review_factory.routes import router as review_factory_router
    if review_factory_router:
        app.include_router(review_factory_router, prefix="/api/merge-factory/review")
except Exception as _exc:
    logger.warning("review_factory_router gateway mounting skipped: %s", _exc)


try:
    from prismatic.workspace.routes import workspace_router
    if workspace_router:
        app.include_router(workspace_router, prefix="/api")
except Exception as _exc:
    logger.warning("workspace_router gateway mounting skipped: %s", _exc)

try:
    from prismatic.deploy.routes import deploy_router
    if deploy_router:
        app.include_router(deploy_router, prefix="/api")
except Exception as _exc:
    logger.warning("deploy_router gateway mounting skipped: %s", _exc)

