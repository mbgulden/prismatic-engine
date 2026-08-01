from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from prismatic.agy_limited_overnight_runner import (
    AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER,
    DEFAULT_MODEL,
    REQUIRED_ASSIGNED_AGENT_MARKERS,
    LimitedOvernightRunStore,
    default_model_preflight,
)
from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    evaluate_overnight_readiness,
)
from prismatic.ingestion_queue import queue_status_payload

AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER = "AGY_LIMITED_UNATTENDED_WINDOW_GUARD_OK"
AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER = "AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED"
DEFAULT_DB_NAME = "agy_unattended_window.db"

NON_CLAIMS = {
    "two_AGY_tasks_launched": False,
    "unbounded_overnight_autopilot": False,
    "auto_merge_enabled": False,
    "bulk_agy_dispatch": False,
    "production_deploy": False,
    "canonical_full_suite_green": False,
    "real_github_pr_created": False,
    "live_Linear_mutations": False,
}


@dataclass(frozen=True)
class UnattendedWindowRequest:
    requested_by: str = "fred"
    agent: str = "agy"
    allowed_agents: tuple[str, ...] = ("agy",)
    max_tasks: int = 2
    one_task_at_a_time: bool = True
    stop_on_first_failure: bool = True
    operator_approval_required: bool = True
    operator_approved: bool = False
    accept_nonzero_queue: bool = False
    auto_merge: bool = False
    production_deploy: bool = False
    real_github_pr_create: bool = False
    bulk_dispatch: bool = False
    live_linear_mutations: bool = False
    model: str = DEFAULT_MODEL

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> UnattendedWindowRequest:
        payload = dict(data or {})
        agents = payload.get("allowed_agents") or payload.get("agents") or [payload.get("agent") or "agy"]
        if not isinstance(agents, Sequence) or isinstance(agents, (str, bytes)):
            agents = [str(agents)]
        return cls(
            requested_by=str(payload.get("requested_by") or "fred"),
            agent=str(payload.get("agent") or "agy").strip().lower(),
            allowed_agents=tuple(str(agent).strip().lower() for agent in agents if str(agent).strip()),
            max_tasks=int(payload.get("max_tasks", 2)),
            one_task_at_a_time=bool(payload.get("one_task_at_a_time", True)),
            stop_on_first_failure=bool(payload.get("stop_on_first_failure", True)),
            operator_approval_required=bool(payload.get("operator_approval_required", True)),
            operator_approved=bool(payload.get("operator_approved", False)),
            accept_nonzero_queue=bool(payload.get("accept_nonzero_queue", False)),
            auto_merge=bool(payload.get("auto_merge", False)),
            production_deploy=bool(payload.get("production_deploy", False)),
            real_github_pr_create=bool(payload.get("real_github_pr_create", False)),
            bulk_dispatch=bool(payload.get("bulk_dispatch", False)),
            live_linear_mutations=bool(payload.get("live_linear_mutations", payload.get("live_Linear_mutations", False))),
            model=str(payload.get("model") or DEFAULT_MODEL),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_by": self.requested_by,
            "agent": self.agent,
            "allowed_agents": list(self.allowed_agents),
            "max_tasks": self.max_tasks,
            "one_task_at_a_time": self.one_task_at_a_time,
            "stop_on_first_failure": self.stop_on_first_failure,
            "operator_approval_required": self.operator_approval_required,
            "operator_approved": self.operator_approved,
            "accept_nonzero_queue": self.accept_nonzero_queue,
            "auto_merge": self.auto_merge,
            "production_deploy": self.production_deploy,
            "real_github_pr_create": self.real_github_pr_create,
            "bulk_dispatch": self.bulk_dispatch,
            "live_linear_mutations": self.live_linear_mutations,
            "model": self.model,
        }


def state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", Path.home() / ".prismatic"))


def default_db_path() -> Path:
    return state_dir() / DEFAULT_DB_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnattendedWindowStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agy_unattended_window_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    requested_by TEXT NOT NULL,
                    resolved_agent TEXT NOT NULL,
                    max_tasks INTEGER NOT NULL,
                    operator_approved INTEGER NOT NULL,
                    operator_pause INTEGER NOT NULL,
                    stop_reason TEXT,
                    blockers_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agy_unattended_window_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def operator_pause(self) -> bool:
        return self._get_state("operator_pause", "false") == "true"

    def set_operator_pause(self, paused: bool) -> dict[str, Any]:
        updated = now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agy_unattended_window_state (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("operator_pause", "true" if paused else "false", updated),
            )
            conn.commit()
        return {"operator_pause": paused, "updated_at": updated, "marker": AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER}

    def _get_state(self, key: str, default: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM agy_unattended_window_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def upsert(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        timestamp = now_iso()
        payload.setdefault("evaluation_id", f"agy-window-{uuid.uuid4().hex[:12]}")
        payload.setdefault("created_at", timestamp)
        payload["updated_at"] = timestamp
        payload.setdefault("status", "blocked")
        payload.setdefault("marker", AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER)
        payload.setdefault("allowed", False)
        payload.setdefault("requested_by", "fred")
        payload.setdefault("resolved_agent", "agy")
        payload.setdefault("max_tasks", 2)
        payload.setdefault("operator_approved", False)
        payload.setdefault("operator_pause", False)
        payload.setdefault("stop_reason", "")
        payload.setdefault("blockers", [])
        payload.setdefault("warnings", [])
        values = (
            payload["evaluation_id"],
            payload["created_at"],
            payload["updated_at"],
            payload["status"],
            payload["marker"],
            1 if payload.get("allowed") else 0,
            payload["requested_by"],
            payload["resolved_agent"],
            int(payload["max_tasks"]),
            1 if payload.get("operator_approved") else 0,
            1 if payload.get("operator_pause") else 0,
            payload.get("stop_reason") or "",
            json.dumps(payload.get("blockers") or [], sort_keys=True),
            json.dumps(payload.get("warnings") or [], sort_keys=True),
            json.dumps(payload, sort_keys=True),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agy_unattended_window_evaluations (
                    evaluation_id, created_at, updated_at, status, marker, allowed,
                    requested_by, resolved_agent, max_tasks, operator_approved,
                    operator_pause, stop_reason, blockers_json, warnings_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evaluation_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    marker=excluded.marker,
                    allowed=excluded.allowed,
                    operator_approved=excluded.operator_approved,
                    operator_pause=excluded.operator_pause,
                    stop_reason=excluded.stop_reason,
                    blockers_json=excluded.blockers_json,
                    warnings_json=excluded.warnings_json,
                    payload_json=excluded.payload_json
                """,
                values,
            )
            conn.commit()
        return self.get(str(payload["evaluation_id"]))

    def get(self, evaluation_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agy_unattended_window_evaluations WHERE evaluation_id = ?", (evaluation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(evaluation_id)
        return row_to_dict(row)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agy_unattended_window_evaluations ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [row_to_dict(row) for row in rows]


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["allowed"] = bool(data.get("allowed"))
    data["operator_approved"] = bool(data.get("operator_approved"))
    data["operator_pause"] = bool(data.get("operator_pause"))
    data["blockers"] = json.loads(data.pop("blockers_json") or "[]")
    data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def _latest_limited_run() -> dict[str, Any] | None:
    runs = LimitedOvernightRunStore().list(limit=1)
    return runs[0] if runs else None


def _queue_depth(payload: Mapping[str, Any]) -> int:
    for key in ("queue_depth", "pending_count", "pending"):
        if key in payload:
            try:
                return int(payload.get(key) or 0)
            except Exception:
                return 0
    return 0


def validate_static_request(req: UnattendedWindowRequest) -> list[str]:
    blockers: list[str] = []
    if req.agent != "agy":
        blockers.append("resolved agent is not agy")
    if tuple(req.allowed_agents) != ("agy",):
        blockers.append('allowed_agents must be exactly ["agy"]')
    if req.max_tasks > 2:
        blockers.append("max_tasks > 2")
    if req.max_tasks < 1:
        blockers.append("max_tasks must be >= 1")
    if not req.one_task_at_a_time:
        blockers.append("one_task_at_a_time must be true")
    if not req.stop_on_first_failure:
        blockers.append("stop_on_first_failure must be true")
    if not req.operator_approval_required:
        blockers.append("operator_approval_required must be true")
    if req.auto_merge:
        blockers.append("auto_merge requested")
    if req.production_deploy:
        blockers.append("production_deploy requested")
    if req.real_github_pr_create:
        blockers.append("real_github_pr_create requested")
    if req.bulk_dispatch:
        blockers.append("bulk_dispatch requested")
    if req.live_linear_mutations:
        blockers.append("live_Linear_mutations requested")
    return blockers


def evaluate_unattended_window(
    request: UnattendedWindowRequest | Mapping[str, Any] | None = None,
    *,
    db_path: str | Path | None = None,
    queue_status_fn: Callable[[], Mapping[str, Any]] | None = None,
    guard_fn: Callable[..., Any] | None = None,
    model_preflight_fn: Callable[[str], tuple[bool, str]] | None = None,
    latest_run_fn: Callable[[], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    req = request if isinstance(request, UnattendedWindowRequest) else UnattendedWindowRequest.from_mapping(request)
    store = UnattendedWindowStore(db_path)
    blockers = validate_static_request(req)
    warnings: list[str] = []

    queue_payload = dict((queue_status_fn or queue_status_payload)())
    for key, expected in REQUIRED_ASSIGNED_AGENT_MARKERS.items():
        if queue_payload.get(key) != expected:
            blockers.append(f"assigned-agent recovery markers missing: {key}={expected}")
    queue_depth = _queue_depth(queue_payload)
    if queue_depth and not req.accept_nonzero_queue:
        blockers.append("queue depth is nonzero and not explicitly accepted")

    operator_pause = store.operator_pause()
    if operator_pause:
        blockers.append("operator pause active")

    latest_run = dict((latest_run_fn or _latest_limited_run)() or {})
    if latest_run.get("marker") != AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER:
        blockers.append("previous AGY_LIMITED_OVERNIGHT_DRY_RUN_OK missing")
    if latest_run.get("status") not in {"pass", "ok", "completed"} and latest_run.get("marker") == AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER:
        blockers.append("latest limited overnight run is not pass")
    if latest_run.get("stop_reason") and "blocked" in str(latest_run.get("stop_reason")).lower():
        blockers.append("latest run has unresolved blocker")

    guard_callable = guard_fn or evaluate_overnight_readiness
    guard_decision = guard_callable(
        requested_by=req.requested_by,
        allowed_agents=req.allowed_agents,
        max_tasks=req.max_tasks,
        auto_merge=req.auto_merge,
        production_deploy=req.production_deploy,
        real_github_pr_create=req.real_github_pr_create,
        bulk_dispatch=req.bulk_dispatch,
    )
    guard_payload = cast(dict[str, Any], guard_decision.as_dict()) if hasattr(guard_decision, "as_dict") else dict(cast(Mapping[str, Any], guard_decision))
    if guard_payload.get("marker") != AGY_OVERNIGHT_READINESS_GUARD_MARKER:
        blockers.append("overnight guard missing")
    if not guard_payload.get("allowed") or guard_payload.get("readiness_state") in {"blocked", "paused", "manual_review"}:
        blockers.append("overnight guard blocked/paused/manual_review")

    model_ok, model_output = (model_preflight_fn or default_model_preflight)(req.model)
    if not model_ok:
        blockers.append("model preflight fails")

    if req.operator_approval_required and not req.operator_approved:
        warnings.append("operator approval required before executing a two-task window")

    allowed = not blockers
    marker = AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER if allowed else AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER
    status = "allowed" if allowed else "blocked"
    payload = {
        "status": status,
        "marker": marker,
        "allowed": allowed,
        "requested_by": req.requested_by,
        "resolved_agent": req.agent,
        "allowed_agents": list(req.allowed_agents),
        "max_tasks": req.max_tasks,
        "one_task_at_a_time": req.one_task_at_a_time,
        "stop_on_first_failure": req.stop_on_first_failure,
        "operator_approval_required": req.operator_approval_required,
        "operator_approved": req.operator_approved,
        "operator_pause": operator_pause,
        "queue_depth": queue_depth,
        "queue_status": queue_payload,
        "guard": guard_payload,
        "latest_limited_overnight_run": latest_run,
        "model_preflight_ok": model_ok,
        "model_preflight_output": model_output[-1000:],
        "two_AGY_tasks_launched": False,
        "launched_tasks": 0,
        "auto_merge": False,
        "production_deploy": False,
        "real_github_pr_create": False,
        "bulk_dispatch": False,
        "live_Linear_mutations": False,
        "non_claims": NON_CLAIMS,
        "blockers": blockers,
        "warnings": warnings,
        "stop_reason": "; ".join(blockers) if blockers else "guard_allowed_no_tasks_launched",
        "request": req.as_dict(),
    }
    item = store.upsert(payload)
    return {**payload, "evaluation": item}


def status_payload(*, db_path: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    store = UnattendedWindowStore(db_path)
    evaluations = store.list(limit=limit)
    return {
        "status": "ok",
        "marker": AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER,
        "latest": evaluations[0] if evaluations else None,
        "evaluations": evaluations,
        "operator_pause": store.operator_pause(),
        "non_claims": NON_CLAIMS,
    }


def request_approval(request: UnattendedWindowRequest | Mapping[str, Any] | None = None, *, db_path: str | Path | None = None) -> dict[str, Any]:
    req = request if isinstance(request, UnattendedWindowRequest) else UnattendedWindowRequest.from_mapping(request)
    store = UnattendedWindowStore(db_path)
    item = store.upsert({
        "status": "approval_requested",
        "marker": AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER,
        "allowed": False,
        "requested_by": req.requested_by,
        "resolved_agent": req.agent,
        "max_tasks": req.max_tasks,
        "operator_approved": False,
        "operator_pause": store.operator_pause(),
        "stop_reason": "operator approval requested; no tasks launched",
        "blockers": [],
        "warnings": ["operator approval required before execution"],
        "request": req.as_dict(),
        "non_claims": NON_CLAIMS,
    })
    return {"status": "approval_requested", "marker": AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER, "evaluation": item, "non_claims": NON_CLAIMS}


def approve_window(request: UnattendedWindowRequest | Mapping[str, Any] | None = None, *, db_path: str | Path | None = None) -> dict[str, Any]:
    payload = dict(request.as_dict() if isinstance(request, UnattendedWindowRequest) else (request or {}))
    payload["operator_approved"] = True
    return evaluate_unattended_window(payload, db_path=db_path)


def set_pause(paused: bool, *, db_path: str | Path | None = None) -> dict[str, Any]:
    state = UnattendedWindowStore(db_path).set_operator_pause(paused)
    return {"status": "paused" if paused else "resumed", **state, "non_claims": NON_CLAIMS}
