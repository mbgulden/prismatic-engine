"""Limited AGY overnight readiness guard.

This module is the control layer for *readiness only*. It never launches AGY,
creates PRs, enables auto-merge, or deploys production. The guard evaluates
whether a tiny future unattended AGY run would be permitted under explicit
fail-closed policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from prismatic.agy_completed_work import list_completed_work
from prismatic.agy_merge_backlog import list_merge_backlog, verify_merge_backlog_item

AGY_OVERNIGHT_READINESS_GUARD_MARKER = "AGY_OVERNIGHT_READINESS_GUARD_OK"
OVERNIGHT_READINESS_GUARD_DESIGN_MARKER = "OVERNIGHT_READINESS_GUARD_DESIGN_OK"
AGY_OVERNIGHT_READINESS_GUARD_BLOCKED_MARKER = "AGY_OVERNIGHT_READINESS_GUARD_BLOCKED"
DEFAULT_DB_NAME = "agy_overnight_guard.db"
ALLOWED_AGENTS = ("agy",)
MAX_TASKS_CAP = 2


@dataclass(frozen=True)
class OvernightGuardPolicy:
    allowed_agents: tuple[str, ...] = ALLOWED_AGENTS
    max_tasks_per_run: int = 2
    max_consecutive_failures: int = 1
    stop_on_first_failure: bool = True
    auto_merge_enabled: bool = False
    production_deploy_enabled: bool = False
    real_github_pr_create_enabled: bool = False
    requires_one_task_success: bool = True
    requires_gateway_healthy: bool = True
    requires_ingestion_healthy: bool = True
    requires_merge_backlog_healthy: bool = True
    requires_verification_gate_healthy: bool = True
    requires_operator_pause_control: bool = True
    requires_operator_summary: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_agents": list(self.allowed_agents),
            "max_tasks_per_run": self.max_tasks_per_run,
            "max_consecutive_failures": self.max_consecutive_failures,
            "stop_on_first_failure": self.stop_on_first_failure,
            "auto_merge_enabled": self.auto_merge_enabled,
            "production_deploy_enabled": self.production_deploy_enabled,
            "real_github_pr_create_enabled": self.real_github_pr_create_enabled,
            "requires_one_task_success": self.requires_one_task_success,
            "requires_gateway_healthy": self.requires_gateway_healthy,
            "requires_ingestion_healthy": self.requires_ingestion_healthy,
            "requires_merge_backlog_healthy": self.requires_merge_backlog_healthy,
            "requires_verification_gate_healthy": self.requires_verification_gate_healthy,
            "requires_operator_pause_control": self.requires_operator_pause_control,
            "requires_operator_summary": self.requires_operator_summary,
        }


@dataclass(frozen=True)
class OvernightGuardDecision:
    allowed: bool
    readiness_state: str
    reason: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    policy: dict[str, Any]
    requested_by: str
    requested_agents: tuple[str, ...]
    requested_max_tasks: int
    latest_one_task_success_marker: str | None
    latest_completed_work_id: str | None
    latest_merge_backlog_id: str | None
    operator_pause: bool
    marker: str = AGY_OVERNIGHT_READINESS_GUARD_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "readiness_state": self.readiness_state,
            "reason": self.reason,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "policy": self.policy,
            "requested_by": self.requested_by,
            "requested_agents": list(self.requested_agents),
            "requested_max_tasks": self.requested_max_tasks,
            "latest_one_task_success_marker": self.latest_one_task_success_marker,
            "latest_completed_work_id": self.latest_completed_work_id,
            "latest_merge_backlog_id": self.latest_merge_backlog_id,
            "operator_pause": self.operator_pause,
            "marker": self.marker,
            "non_claims": {
                "overnight_autopilot_active": False,
                "auto_merge_enabled": False,
                "bulk_agy_dispatch": False,
                "production_deploy": False,
                "real_github_pr_created": False,
            },
            "next_safe_action": next_safe_action(self.readiness_state, self.blockers),
        }


@dataclass(frozen=True)
class PersistedDecision:
    guard_decision_id: str
    created_at: str
    requested_by: str
    allowed_agents: tuple[str, ...]
    max_tasks: int
    policy_result: dict[str, Any]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    last_success_marker: str | None
    operator_pause: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "guard_decision_id": self.guard_decision_id,
            "created_at": self.created_at,
            "requested_by": self.requested_by,
            "allowed_agents": list(self.allowed_agents),
            "max_tasks": self.max_tasks,
            "policy_result": self.policy_result,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "last_success_marker": self.last_success_marker,
            "operator_pause": self.operator_pause,
        }


@dataclass(frozen=True)
class OvernightRunAttempt:
    run_attempt_id: str
    created_at: str
    requested_by: str
    allowed_agents: tuple[str, ...]
    max_tasks: int
    run_status: str
    summary: str
    guard_decision_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_attempt_id": self.run_attempt_id,
            "created_at": self.created_at,
            "requested_by": self.requested_by,
            "allowed_agents": list(self.allowed_agents),
            "max_tasks": self.max_tasks,
            "run_status": self.run_status,
            "summary": self.summary,
            "guard_decision_id": self.guard_decision_id,
        }


def default_state_dir() -> Path:
    configured = os.environ.get("PRISMATIC_STATE_DIR")
    if configured:
        return Path(configured)
    return Path.cwd() / "prismatic_state"


def default_db_path() -> Path:
    explicit = os.environ.get("PRISMATIC_AGY_OVERNIGHT_GUARD_STATE")
    if explicit:
        return Path(explicit)
    return default_state_dir() / DEFAULT_DB_NAME


def default_policy() -> OvernightGuardPolicy:
    return OvernightGuardPolicy()


def next_safe_action(readiness_state: str, blockers: Sequence[str]) -> str:
    if readiness_state == "ready":
        return "Operator may approve a separate limited overnight dry-run packet; this guard does not launch it."
    if readiness_state == "paused":
        return "Resume operator pause only after reviewing blockers and current fleet state."
    if blockers:
        return f"Resolve blocker: {blockers[0]}"
    return "Review guard warnings before any unattended run."


def evaluate_overnight_readiness(
    *,
    requested_by: str = "fred",
    allowed_agents: Sequence[str] | None = None,
    max_tasks: int = 1,
    auto_merge: bool = False,
    production_deploy: bool = False,
    real_github_pr_create: bool = False,
    bulk_dispatch: bool = False,
    gateway_healthy: bool = True,
    ingestion_healthy: bool | None = None,
    merge_backlog_healthy: bool | None = None,
    verification_gate_healthy: bool | None = None,
    operator_pause: bool | None = None,
    operator_summary_required: bool = True,
    required_preflight_ok: bool = True,
    unresolved_previous_failure: bool | None = None,
    policy: OvernightGuardPolicy | None = None,
    db_path: str | Path | None = None,
) -> OvernightGuardDecision:
    """Evaluate the limited overnight guard. Pure policy; no task launch side effects."""

    policy = policy or default_policy()
    requested_agents = tuple(allowed_agents or policy.allowed_agents)
    blockers: list[str] = []
    warnings: list[str] = []
    store = AgyOvernightGuardStore(db_path)
    paused = store.operator_pause() if operator_pause is None else bool(operator_pause)
    previous_failed = store.has_unresolved_failure() if unresolved_previous_failure is None else bool(unresolved_previous_failure)

    latest_cw = _latest_completed_work_row()
    latest_backlog = _latest_merge_backlog_item()
    verify_payload = _verify_latest_backlog(latest_backlog.completed_work_id if latest_backlog else None)

    inferred_ingestion = latest_cw is not None and latest_cw.ingestion_marker == "AGY_COMPLETED_WORK_INGESTION_OK"
    inferred_backlog = latest_backlog is not None and "AGY_CLEAN_PR_CREATE_UPDATE_OK" in set(getattr(latest_backlog, "markers", ()))
    inferred_verify = bool(verify_payload and verify_payload.get("verification_gate") == "pass")

    ingestion_ok = inferred_ingestion if ingestion_healthy is None else bool(ingestion_healthy)
    backlog_ok = inferred_backlog if merge_backlog_healthy is None else bool(merge_backlog_healthy)
    verify_ok = inferred_verify if verification_gate_healthy is None else bool(verification_gate_healthy)
    one_task_marker = _one_task_success_marker(latest_cw, latest_backlog, verify_payload)

    if paused:
        blockers.append("operator pause is active")
    if not gateway_healthy:
        blockers.append("gateway health check failed")
    if policy.requires_ingestion_healthy and not ingestion_ok:
        blockers.append("completed-work ingestion unavailable or no healthy row")
    if policy.requires_merge_backlog_healthy and not backlog_ok:
        blockers.append("merge backlog unavailable or latest row missing")
    if policy.requires_verification_gate_healthy and not verify_ok:
        blockers.append("verification gate unavailable or latest row not passing")
    if policy.requires_one_task_success and one_task_marker != "AGY_AUTOPILOT_ONE_TASK_DRY_RUN_OK":
        blockers.append("latest one-task AGY proof missing")
    if auto_merge or policy.auto_merge_enabled:
        blockers.append("auto_merge=true is forbidden")
    if production_deploy or policy.production_deploy_enabled:
        blockers.append("production_deploy=true is forbidden")
    if real_github_pr_create or policy.real_github_pr_create_enabled:
        blockers.append("real GitHub PR creation is disabled by default")
    if bulk_dispatch:
        blockers.append("bulk dispatch requested")
    if max_tasks < 1:
        blockers.append("max_tasks must be at least 1")
    if max_tasks > min(policy.max_tasks_per_run, MAX_TASKS_CAP):
        blockers.append(f"max_tasks exceeds allowed cap {min(policy.max_tasks_per_run, MAX_TASKS_CAP)}")
    unknown = [agent for agent in requested_agents if agent not in policy.allowed_agents]
    if unknown:
        blockers.append(f"unknown or disabled agent requested: {', '.join(unknown)}")
    if previous_failed:
        blockers.append("previous run failed and is unresolved")
    if policy.requires_operator_summary and not operator_summary_required:
        blockers.append("operator summary is required")
    if not required_preflight_ok:
        blockers.append("required skills/preflight missing")

    if paused:
        state = "paused"
    elif blockers:
        state = "blocked"
    elif warnings:
        state = "needs_manual_review"
    else:
        state = "ready"

    allowed = state == "ready"
    reason = "limited AGY overnight guard ready; no tasks launched" if allowed else blockers[0] if blockers else warnings[0]
    return OvernightGuardDecision(
        allowed=allowed,
        readiness_state=state,
        reason=reason,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        policy=policy.as_dict(),
        requested_by=requested_by,
        requested_agents=requested_agents,
        requested_max_tasks=max_tasks,
        latest_one_task_success_marker=one_task_marker,
        latest_completed_work_id=latest_cw.id if latest_cw else None,
        latest_merge_backlog_id=latest_backlog.completed_work_id if latest_backlog else None,
        operator_pause=paused,
    )


class AgyOvernightGuardStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS overnight_guard_decisions (
                    guard_decision_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    allowed_agents_json TEXT NOT NULL,
                    max_tasks INTEGER NOT NULL,
                    policy_result_json TEXT NOT NULL,
                    blockers_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    last_success_marker TEXT,
                    operator_pause INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS overnight_run_attempts (
                    run_attempt_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    allowed_agents_json TEXT NOT NULL,
                    max_tasks INTEGER NOT NULL,
                    run_status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    guard_decision_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS overnight_guard_state (
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
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO overnight_guard_state (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("operator_pause", "true" if paused else "false", now),
            )
            conn.commit()
        return {"operator_pause": paused, "updated_at": now, "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER}

    def _get_state(self, key: str, default: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM overnight_guard_state WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def record_guard_decision(self, decision: OvernightGuardDecision) -> PersistedDecision:
        payload = decision.as_dict()
        decision_id = _stable_id("agy-ogd", payload)
        created_at = _now()
        values = (
            decision_id,
            created_at,
            decision.requested_by,
            json.dumps(list(decision.requested_agents), sort_keys=True),
            decision.requested_max_tasks,
            json.dumps(payload, sort_keys=True),
            json.dumps(list(decision.blockers), sort_keys=True),
            json.dumps(list(decision.warnings), sort_keys=True),
            decision.latest_one_task_success_marker,
            1 if decision.operator_pause else 0,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO overnight_guard_decisions (
                    guard_decision_id, created_at, requested_by, allowed_agents_json,
                    max_tasks, policy_result_json, blockers_json, warnings_json,
                    last_success_marker, operator_pause
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guard_decision_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    requested_by=excluded.requested_by,
                    allowed_agents_json=excluded.allowed_agents_json,
                    max_tasks=excluded.max_tasks,
                    policy_result_json=excluded.policy_result_json,
                    blockers_json=excluded.blockers_json,
                    warnings_json=excluded.warnings_json,
                    last_success_marker=excluded.last_success_marker,
                    operator_pause=excluded.operator_pause
                """,
                values,
            )
            conn.commit()
        return self.get_decision(decision_id)

    def list_guard_decisions(self, *, limit: int = 20) -> list[PersistedDecision]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM overnight_guard_decisions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_decision_from_row(row) for row in rows]

    def get_decision(self, decision_id: str) -> PersistedDecision:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM overnight_guard_decisions WHERE guard_decision_id = ?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return _decision_from_row(row)

    def record_overnight_run_attempt(
        self,
        *,
        requested_by: str,
        allowed_agents: Sequence[str],
        max_tasks: int,
        run_status: str,
        summary: str,
        guard_decision_id: str | None = None,
    ) -> OvernightRunAttempt:
        payload = {
            "requested_by": requested_by,
            "allowed_agents": list(allowed_agents),
            "max_tasks": max_tasks,
            "run_status": run_status,
            "summary": summary,
            "guard_decision_id": guard_decision_id,
            "created_at": _now(),
        }
        run_id = _stable_id("agy-ogr", payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO overnight_run_attempts (
                    run_attempt_id, created_at, requested_by, allowed_agents_json,
                    max_tasks, run_status, summary, guard_decision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_attempt_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    requested_by=excluded.requested_by,
                    allowed_agents_json=excluded.allowed_agents_json,
                    max_tasks=excluded.max_tasks,
                    run_status=excluded.run_status,
                    summary=excluded.summary,
                    guard_decision_id=excluded.guard_decision_id
                """,
                (
                    run_id,
                    payload["created_at"],
                    requested_by,
                    json.dumps(list(allowed_agents), sort_keys=True),
                    max_tasks,
                    run_status,
                    summary,
                    guard_decision_id,
                ),
            )
            conn.commit()
        return self.get_run_attempt(run_id)

    def get_run_attempt(self, run_attempt_id: str) -> OvernightRunAttempt:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM overnight_run_attempts WHERE run_attempt_id = ?", (run_attempt_id,)).fetchone()
        if row is None:
            raise KeyError(run_attempt_id)
        return _run_from_row(row)

    def list_run_attempts(self, *, limit: int = 20) -> list[OvernightRunAttempt]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM overnight_run_attempts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def has_unresolved_failure(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_status FROM overnight_run_attempts ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return bool(row and str(row["run_status"]) == "failed_unresolved")


def record_guard_decision(decision: OvernightGuardDecision, *, db_path: str | Path | None = None) -> PersistedDecision:
    return AgyOvernightGuardStore(db_path).record_guard_decision(decision)


def list_guard_decisions(*, db_path: str | Path | None = None, limit: int = 20) -> list[PersistedDecision]:
    return AgyOvernightGuardStore(db_path).list_guard_decisions(limit=limit)


def record_overnight_run_attempt(
    *,
    db_path: str | Path | None = None,
    requested_by: str = "fred",
    allowed_agents: Sequence[str] = ALLOWED_AGENTS,
    max_tasks: int = 1,
    run_status: str,
    summary: str,
    guard_decision_id: str | None = None,
) -> OvernightRunAttempt:
    return AgyOvernightGuardStore(db_path).record_overnight_run_attempt(
        requested_by=requested_by,
        allowed_agents=allowed_agents,
        max_tasks=max_tasks,
        run_status=run_status,
        summary=summary,
        guard_decision_id=guard_decision_id,
    )


def list_overnight_run_attempts(*, db_path: str | Path | None = None, limit: int = 20) -> list[OvernightRunAttempt]:
    return AgyOvernightGuardStore(db_path).list_run_attempts(limit=limit)


def set_operator_pause(paused: bool, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return AgyOvernightGuardStore(db_path).set_operator_pause(paused)


def operator_pause(*, db_path: str | Path | None = None) -> bool:
    return AgyOvernightGuardStore(db_path).operator_pause()


def _latest_completed_work_row() -> Any | None:
    try:
        rows = list_completed_work(limit=1)
    except Exception:
        return None
    return rows[0] if rows else None


def _latest_merge_backlog_item() -> Any | None:
    try:
        items = list_merge_backlog(limit=1)
    except Exception:
        return None
    return items[0] if items else None


def _verify_latest_backlog(completed_work_id: str | None) -> dict[str, Any] | None:
    if not completed_work_id:
        return None
    try:
        return verify_merge_backlog_item(completed_work_id)
    except Exception:
        return None


def _one_task_success_marker(latest_cw: Any | None, latest_backlog: Any | None, verify_payload: Mapping[str, Any] | None) -> str | None:
    if not latest_cw or not latest_backlog or not verify_payload:
        return None
    packet: Mapping[str, Any] = latest_cw.packet if isinstance(latest_cw.packet, Mapping) else {}
    raw_normalization = packet.get("normalization")
    normalization: Mapping[str, Any] = raw_normalization if isinstance(raw_normalization, Mapping) else {}
    if (
        latest_cw.classification == "merge_ready"
        and latest_cw.agent == "agy"
        and latest_cw.proof_result == "PASS"
        and normalization.get("marker") == "AGY_RESULT_PACKET_NORMALIZED_OK"
        and latest_backlog.recommended_action == "open_or_update_pr"
        and latest_backlog.verification_gate == "pass"
        and latest_backlog.eligible_for_auto_merge is False
        and verify_payload.get("eligible_for_auto_merge") is False
    ):
        return "AGY_AUTOPILOT_ONE_TASK_DRY_RUN_OK"
    return None


def _decision_from_row(row: sqlite3.Row) -> PersistedDecision:
    return PersistedDecision(
        guard_decision_id=str(row["guard_decision_id"]),
        created_at=str(row["created_at"]),
        requested_by=str(row["requested_by"]),
        allowed_agents=tuple(json.loads(row["allowed_agents_json"])),
        max_tasks=int(row["max_tasks"]),
        policy_result=json.loads(row["policy_result_json"]),
        blockers=tuple(json.loads(row["blockers_json"])),
        warnings=tuple(json.loads(row["warnings_json"])),
        last_success_marker=row["last_success_marker"],
        operator_pause=bool(row["operator_pause"]),
    )


def _run_from_row(row: sqlite3.Row) -> OvernightRunAttempt:
    return OvernightRunAttempt(
        run_attempt_id=str(row["run_attempt_id"]),
        created_at=str(row["created_at"]),
        requested_by=str(row["requested_by"]),
        allowed_agents=tuple(json.loads(row["allowed_agents_json"])),
        max_tasks=int(row["max_tasks"]),
        run_status=str(row["run_status"]),
        summary=str(row["summary"]),
        guard_decision_id=row["guard_decision_id"],
    )


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
