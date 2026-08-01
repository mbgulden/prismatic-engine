from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_merge_backlog import (
    get_merge_backlog_item,
    verify_merge_backlog_item,
)
from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    evaluate_overnight_readiness,
    record_guard_decision,
)
from prismatic.ingestion_queue import queue_status_payload

AGY_LIMITED_OVERNIGHT_RUNNER_MARKER = "AGY_LIMITED_OVERNIGHT_RUNNER_OK"
AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER = "AGY_LIMITED_OVERNIGHT_DRY_RUN_OK"
AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER = "AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED"
AGY_LIMITED_OVERNIGHT_PACKET_MARKER = "AGY_LIMITED_OVERNIGHT_DRY_RUN_PACKET_OK"
DEFAULT_DB_NAME = "agy_limited_overnight_runs.db"
DEFAULT_MODEL = "Gemini 3.5 Flash (Medium)"
ASSIGNED_AGENT_WRITEBACK_DRY_RUN_STATE = "dry_run_no_live_linear_mutation"
REQUIRED_ASSIGNED_AGENT_MARKERS = {
    "marker": "LINEAR_WEBHOOK_QUEUE_ACTIVE_OK",
    "assigned_agent_marker": "ASSIGNED_AGENT_EVENT_DISPATCH_OK",
    "result_writeback_marker": "ASSIGNED_AGENT_RESULT_WRITEBACK_OK",
    "dispatch_recovery_marker": "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK",
}

NON_CLAIMS = {
    "overnight_autopilot_unbounded": False,
    "auto_merge_enabled": False,
    "bulk_agy_dispatch": False,
    "production_deploy": False,
    "canonical_full_suite_green": False,
    "real_github_pr_created": False,
    "more_than_one_AGY_task": False,
}


@dataclass(frozen=True)
class RunnerRequest:
    requested_by: str = "fred"
    allowed_agents: tuple[str, ...] = ("agy",)
    max_tasks: int = 1
    agent: str = "agy"
    stop_on_first_failure: bool = True
    auto_merge: bool = False
    production_deploy: bool = False
    real_github_pr_create: bool = False
    bulk_dispatch: bool = False
    model: str = DEFAULT_MODEL

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> RunnerRequest:
        payload = dict(data or {})
        agents = payload.get("allowed_agents") or payload.get("agents") or [payload.get("agent") or "agy"]
        if not isinstance(agents, Sequence) or isinstance(agents, (str, bytes)):
            agents = [str(agents)]
        return cls(
            requested_by=str(payload.get("requested_by") or "fred"),
            allowed_agents=tuple(str(agent).strip().lower() for agent in agents if str(agent).strip()),
            max_tasks=int(payload.get("max_tasks", 1)),
            agent=str(payload.get("agent") or "agy").strip().lower(),
            stop_on_first_failure=bool(payload.get("stop_on_first_failure", True)),
            auto_merge=bool(payload.get("auto_merge", False)),
            production_deploy=bool(payload.get("production_deploy", False)),
            real_github_pr_create=bool(payload.get("real_github_pr_create", False)),
            bulk_dispatch=bool(payload.get("bulk_dispatch", False)),
            model=str(payload.get("model") or DEFAULT_MODEL),
        )

    def as_guard_payload(self) -> dict[str, Any]:
        return {
            "allowed_agents": list(self.allowed_agents),
            "max_tasks": self.max_tasks,
            "requested_by": self.requested_by,
            "auto_merge": self.auto_merge,
            "production_deploy": self.production_deploy,
            "real_github_pr_create": self.real_github_pr_create,
            "bulk_dispatch": self.bulk_dispatch,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_by": self.requested_by,
            "allowed_agents": list(self.allowed_agents),
            "max_tasks": self.max_tasks,
            "agent": self.agent,
            "stop_on_first_failure": self.stop_on_first_failure,
            "auto_merge": self.auto_merge,
            "production_deploy": self.production_deploy,
            "real_github_pr_create": self.real_github_pr_create,
            "bulk_dispatch": self.bulk_dispatch,
            "model": self.model,
        }


def state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", Path.home() / ".prismatic"))


def default_db_path() -> Path:
    return state_dir() / DEFAULT_DB_NAME


class LimitedOvernightRunStore:
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
                CREATE TABLE IF NOT EXISTS agy_limited_overnight_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    requested_by TEXT,
                    resolved_agent TEXT,
                    max_tasks INTEGER NOT NULL,
                    launched_tasks INTEGER NOT NULL,
                    completed_work_id TEXT,
                    merge_backlog_id TEXT,
                    verification_gate TEXT,
                    stop_reason TEXT,
                    guard_allowed INTEGER NOT NULL,
                    guard_marker TEXT,
                    runner_called_guard INTEGER NOT NULL,
                    model_preflight_ok INTEGER NOT NULL,
                    auto_merge INTEGER NOT NULL,
                    production_deploy INTEGER NOT NULL,
                    real_github_pr_created INTEGER NOT NULL,
                    bulk_dispatch INTEGER NOT NULL,
                    stop_on_first_failure INTEGER NOT NULL,
                    assigned_agent_writeback_state TEXT,
                    non_claims_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(agy_limited_overnight_runs)").fetchall()}
            if "assigned_agent_writeback_state" not in columns:
                conn.execute("ALTER TABLE agy_limited_overnight_runs ADD COLUMN assigned_agent_writeback_state TEXT")
            conn.commit()

    def upsert(self, row: Mapping[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(row)
        payload.setdefault("run_id", f"agy-limited-{uuid.uuid4().hex[:12]}")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        payload.setdefault("status", "created")
        payload.setdefault("marker", AGY_LIMITED_OVERNIGHT_RUNNER_MARKER)
        payload.setdefault("requested_by", "fred")
        payload.setdefault("resolved_agent", "")
        payload.setdefault("max_tasks", 1)
        payload.setdefault("launched_tasks", 0)
        payload.setdefault("completed_work_id", "")
        payload.setdefault("merge_backlog_id", "")
        payload.setdefault("verification_gate", "")
        payload.setdefault("stop_reason", "")
        payload.setdefault("guard_allowed", False)
        payload.setdefault("guard_marker", "")
        payload.setdefault("runner_called_guard", False)
        payload.setdefault("model_preflight_ok", False)
        payload.setdefault("auto_merge", False)
        payload.setdefault("production_deploy", False)
        payload.setdefault("real_github_pr_created", False)
        payload.setdefault("bulk_dispatch", False)
        payload.setdefault("stop_on_first_failure", True)
        payload.setdefault("assigned_agent_writeback_state", "pending")
        payload.setdefault("non_claims", NON_CLAIMS)
        values = (
            payload["run_id"], payload["created_at"], payload["updated_at"], payload["status"], payload["marker"],
            payload["requested_by"], payload["resolved_agent"], int(payload["max_tasks"]), int(payload["launched_tasks"]),
            payload.get("completed_work_id") or "", payload.get("merge_backlog_id") or "", payload.get("verification_gate") or "",
            payload.get("stop_reason") or "", 1 if payload.get("guard_allowed") else 0, payload.get("guard_marker") or "",
            1 if payload.get("runner_called_guard") else 0, 1 if payload.get("model_preflight_ok") else 0,
            1 if payload.get("auto_merge") else 0, 1 if payload.get("production_deploy") else 0,
            1 if payload.get("real_github_pr_created") else 0, 1 if payload.get("bulk_dispatch") else 0,
            1 if payload.get("stop_on_first_failure") else 0, str(payload.get("assigned_agent_writeback_state") or "pending"),
            json.dumps(payload.get("non_claims") or NON_CLAIMS, sort_keys=True), json.dumps(payload, sort_keys=True),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agy_limited_overnight_runs (
                    run_id, created_at, updated_at, status, marker, requested_by,
                    resolved_agent, max_tasks, launched_tasks, completed_work_id,
                    merge_backlog_id, verification_gate, stop_reason, guard_allowed,
                    guard_marker, runner_called_guard, model_preflight_ok,
                    auto_merge, production_deploy, real_github_pr_created,
                    bulk_dispatch, stop_on_first_failure, assigned_agent_writeback_state, non_claims_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    updated_at=excluded.updated_at, status=excluded.status, marker=excluded.marker,
                    resolved_agent=excluded.resolved_agent, launched_tasks=excluded.launched_tasks,
                    completed_work_id=excluded.completed_work_id, merge_backlog_id=excluded.merge_backlog_id,
                    verification_gate=excluded.verification_gate, stop_reason=excluded.stop_reason,
                    guard_allowed=excluded.guard_allowed, guard_marker=excluded.guard_marker,
                    runner_called_guard=excluded.runner_called_guard, model_preflight_ok=excluded.model_preflight_ok,
                    assigned_agent_writeback_state=excluded.assigned_agent_writeback_state,
                    payload_json=excluded.payload_json
                """,
                values,
            )
            conn.commit()
        return self.get(str(payload["run_id"]))

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agy_limited_overnight_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row_to_dict(row)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agy_limited_overnight_runs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def stop_latest(self, reason: str = "operator_stop") -> dict[str, Any]:
        runs = self.list(limit=1)
        if not runs:
            return self.upsert({"status": "stopped", "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER, "stop_reason": reason})
        latest = runs[0]
        latest.update({"status": "stopped", "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER, "stop_reason": reason})
        return self.upsert(latest)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["guard_allowed"] = bool(data.get("guard_allowed"))
    data["runner_called_guard"] = bool(data.get("runner_called_guard"))
    data["model_preflight_ok"] = bool(data.get("model_preflight_ok"))
    data["auto_merge"] = bool(data.get("auto_merge"))
    data["production_deploy"] = bool(data.get("production_deploy"))
    data["real_github_pr_created"] = bool(data.get("real_github_pr_created"))
    data["bulk_dispatch"] = bool(data.get("bulk_dispatch"))
    data["stop_on_first_failure"] = bool(data.get("stop_on_first_failure"))
    if not data.get("assigned_agent_writeback_state"):
        data["assigned_agent_writeback_state"] = ASSIGNED_AGENT_WRITEBACK_DRY_RUN_STATE
    data["non_claims"] = json.loads(data.pop("non_claims_json") or "{}")
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def status_payload(*, db_path: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    store = LimitedOvernightRunStore(db_path)
    runs = store.list(limit=limit)
    latest = runs[0] if runs else {}
    return {
        "status": "ok",
        "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
        "latest": latest,
        "runs": runs,
        "count": len(runs),
        "non_claims": NON_CLAIMS,
    }


def _assigned_agent_runway_preflight(status: Mapping[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
    payload = dict(status or queue_status_payload())
    missing = [f"{key}={expected}" for key, expected in REQUIRED_ASSIGNED_AGENT_MARKERS.items() if payload.get(key) != expected]
    if missing:
        return False, "assigned-agent recovery markers missing: " + ", ".join(missing), payload
    return True, "assigned-agent recovery markers live", payload


def _blocked(run: dict[str, Any], reason: str, *, store: LimitedOvernightRunStore) -> dict[str, Any]:
    run.update({"status": "blocked", "marker": AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER, "stop_reason": reason})
    item = store.upsert(run)
    return {"ok": False, "marker": AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER, "status": "blocked", "reason": reason, "run": item, "non_claims": NON_CLAIMS}


def default_model_preflight(model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    agy = os.environ.get("AGY_BIN", "/home/ubuntu/.local/bin/agy")
    if not Path(agy).exists():
        return False, f"agy binary not found: {agy}"
    prompt = "Reply with exactly: OK"
    try:
        proc = subprocess.run(
            [agy, "--print", prompt, "--print-timeout", "60s", "--model", model],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"model preflight exception: {exc}"
    return proc.returncode == 0 and "OK" in proc.stdout, proc.stdout[-1000:]


def default_launch_agy_task(model: str = DEFAULT_MODEL) -> tuple[int, str]:
    agy = os.environ.get("AGY_BIN", "/home/ubuntu/.local/bin/agy")
    artifact_dir = state_dir() / "agy-limited-overnight-artifacts" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt = f"""
You are AGY. Run exactly one safe observation-only task.
Inspect only local Prismatic dashboard/completed-work/overnight-guard API surfaces conceptually from this prompt; do not edit production, do not create GitHub PRs, do not claim auto-merge.
Create a tiny observation artifact at {artifact_dir}/OBSERVATION.md if file tools are available; otherwise describe it in the packet.
Return ONLY valid JSON for a normalized completed-work packet with these fields:
agent='agy', issue_identifier='AGY-LIMITED-OVERNIGHT-DRY-RUN', source_branch='feature/agy-limited-overnight-dry-run-observation', source_path='{artifact_dir}', base_branch='main', merge_lane='docs', verification_lane='docs', changed_files=['docs/agy-limited-overnight-dry-run-observation.md'], result_summary, proof object with result='PASS', command, log, scope, ad_hoc_or_canonical='ad-hoc targeted runtime proof', marker='AGY_LIMITED_OVERNIGHT_DRY_RUN_PACKET_OK', result_artifacts containing {artifact_dir}/OBSERVATION.md, non_claims including bulk_agy_dispatch, overnight_autopilot_unbounded, auto_merge_enabled, production_deploy, real_github_pr_created, more_than_one_AGY_task, and marker='AGY_LIMITED_OVERNIGHT_DRY_RUN_PACKET_OK'.
""".strip()
    proc = subprocess.run(
        [agy, "--print", prompt, "--print-timeout", "10m0s", "--dangerously-skip-permissions", "--sandbox", "--add-dir", str(artifact_dir), "--model", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=660,
        check=False,
    )
    return proc.returncode, proc.stdout


def parse_packet(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", output, flags=re.DOTALL)
    if not match:
        raise ValueError("AGY output did not contain JSON packet")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("AGY packet JSON is not an object")
    return value


def _validate_request(req: RunnerRequest) -> str | None:
    if req.agent != "agy" or tuple(req.allowed_agents) != ("agy",):
        return "unknown or ambiguous agent; only agy is allowed"
    if req.max_tasks > 1:
        return "max_tasks > 1 blocked for first dry run"
    if req.max_tasks < 1:
        return "max_tasks must be at least 1"
    if not req.stop_on_first_failure:
        return "stop_on_first_failure must be true"
    if req.auto_merge:
        return "auto_merge requested"
    if req.production_deploy:
        return "production_deploy requested"
    if req.real_github_pr_create:
        return "real_github_pr_create requested"
    if req.bulk_dispatch:
        return "bulk dispatch requested"
    return None


def run_limited_overnight_dry_run(
    request: RunnerRequest | Mapping[str, Any] | None = None,
    *,
    db_path: str | Path | None = None,
    guard_fn: Callable[..., Any] | None = None,
    model_preflight_fn: Callable[[str], tuple[bool, str]] | None = None,
    agy_launch_fn: Callable[[str], tuple[int, str]] | None = None,
    assigned_agent_status_fn: Callable[[], Mapping[str, Any]] | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    req = request if isinstance(request, RunnerRequest) else RunnerRequest.from_mapping(request)
    store = LimitedOvernightRunStore(db_path)
    run_id = f"agy-limited-{uuid.uuid4().hex[:12]}"
    run: dict[str, Any] = {
        "run_id": run_id,
        "status": "preflight" if execute else "preflight_only",
        "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
        "requested_by": req.requested_by,
        "resolved_agent": req.agent,
        "max_tasks": req.max_tasks,
        "launched_tasks": 0,
        "guard_allowed": False,
        "guard_marker": "",
        "runner_called_guard": False,
        "model_preflight_ok": False,
        "auto_merge": req.auto_merge,
        "production_deploy": req.production_deploy,
        "real_github_pr_created": False,
        "bulk_dispatch": req.bulk_dispatch,
        "stop_on_first_failure": req.stop_on_first_failure,
        "assigned_agent_writeback_state": "pending",
        "non_claims": NON_CLAIMS,
        "request": req.as_dict(),
    }
    store.upsert(run)
    validation_error = _validate_request(req)
    if validation_error:
        return _blocked(run, validation_error, store=store)

    status_callable = assigned_agent_status_fn or queue_status_payload
    assigned_ok, assigned_reason, assigned_payload = _assigned_agent_runway_preflight(status_callable())
    run.update({
        "assigned_agent_runway": assigned_payload,
        "assigned_agent_writeback_state": ASSIGNED_AGENT_WRITEBACK_DRY_RUN_STATE,
    })
    store.upsert(run)
    if not assigned_ok:
        return _blocked(run, assigned_reason, store=store)

    guard_callable = guard_fn or evaluate_overnight_readiness
    decision = guard_callable(
        requested_by=req.requested_by,
        allowed_agents=req.allowed_agents,
        max_tasks=req.max_tasks,
        auto_merge=req.auto_merge,
        production_deploy=req.production_deploy,
        real_github_pr_create=req.real_github_pr_create,
        bulk_dispatch=req.bulk_dispatch,
    )
    if hasattr(decision, "as_dict"):
        guard_payload = cast(Mapping[str, Any], decision.as_dict())
        try:
            record_guard_decision(decision)
        except Exception:
            pass
    else:
        guard_payload = cast(Mapping[str, Any], dict(cast(Mapping[str, Any], decision)))
    run.update({
        "runner_called_guard": True,
        "guard_allowed": bool(guard_payload.get("allowed")),
        "guard_marker": str(guard_payload.get("marker") or AGY_OVERNIGHT_READINESS_GUARD_MARKER),
        "guard": guard_payload,
    })
    store.upsert(run)
    if not run["guard_allowed"] or guard_payload.get("readiness_state") in {"blocked", "paused", "manual_review"}:
        return _blocked(run, "guard readiness blocked: " + str(guard_payload.get("reason") or guard_payload.get("readiness_state")), store=store)
    if not execute:
        run.update({"status": "ready", "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER, "stop_reason": "preflight_only"})
        item = store.upsert(run)
        return {"ok": True, "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER, "status": "ready", "run": item, "non_claims": NON_CLAIMS}

    preflight_callable = model_preflight_fn or default_model_preflight
    model_ok, model_output = preflight_callable(req.model)
    run.update({"model_preflight_ok": model_ok, "model_preflight_output": model_output[-1000:]})
    store.upsert(run)
    if not model_ok:
        return _blocked(run, "model preflight failed", store=store)

    launch_callable = agy_launch_fn or default_launch_agy_task
    code, output = launch_callable(req.model)
    run.update({"launched_tasks": 1, "agy_exit_code": code, "agy_output_tail": output[-2000:]})
    store.upsert(run)
    if code != 0:
        return _blocked(run, f"AGY task failed with exit code {code}", store=store)

    try:
        packet = parse_packet(output)
    except Exception as exc:
        return _blocked(run, f"AGY emitted invalid packet: {exc}", store=store)
    if packet.get("marker") != AGY_LIMITED_OVERNIGHT_PACKET_MARKER:
        return _blocked(run, "AGY packet marker missing or wrong", store=store)
    try:
        row = ingest_completed_work(packet)
    except Exception as exc:
        return _blocked(run, f"completed-work ingestion rejected packet: {exc}", store=store)
    backlog = get_merge_backlog_item(row.id)
    verification = verify_merge_backlog_item(row.id)
    gate = str(verification.get("verification_gate") or "")
    run.update({
        "completed_work_id": row.id,
        "merge_backlog_id": backlog.merge_backlog_id,
        "verification_gate": gate,
        "completed_work_ingested": True,
        "merge_backlog_evaluated": True,
        "verification_gate_evaluated": True,
        "assigned_agent_writeback_state": ASSIGNED_AGENT_WRITEBACK_DRY_RUN_STATE,
    })
    if gate != "pass":
        return _blocked(run, f"verification gate {gate}", store=store)
    run.update({"status": "pass", "marker": AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER, "stop_reason": "completed_one_task_and_stopped"})
    item = store.upsert(run)
    return {
        "ok": True,
        "marker": AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER,
        "status": "pass",
        "runner_called_guard": True,
        "guard_allowed": True,
        "resolved_agent": "agy",
        "AGY_task_count": 1,
        "no_other_tasks_launched": True,
        "bulk_dispatch": False,
        "auto_merge": False,
        "production_deploy": False,
        "real_github_pr_created": False,
        "stop_on_first_failure": True,
        "completed_work_ingested": True,
        "merge_backlog_evaluated": True,
        "verification_gate_evaluated": True,
        "dashboard_or_api_readback": True,
        "assigned_agent_writeback_state": ASSIGNED_AGENT_WRITEBACK_DRY_RUN_STATE,
        "completed_work_id": row.id,
        "merge_backlog_id": backlog.merge_backlog_id,
        "verification_gate": gate,
        "run": item,
        "non_claims": NON_CLAIMS,
    }


def stop_latest_run(*, db_path: str | Path | None = None, reason: str = "operator_stop") -> dict[str, Any]:
    item = LimitedOvernightRunStore(db_path).stop_latest(reason)
    return {"status": "stopped", "marker": AGY_LIMITED_OVERNIGHT_RUNNER_MARKER, "run": item, "non_claims": NON_CLAIMS}
