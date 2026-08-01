"""Prompt 6 durable audit ledger for approved executor attempts.

This module records Prompt 5.5 executor attempts without enabling real git/GitHub
side effects. The default canary path is a dry-run fixture that renders commands,
persists a run row, and keeps all completed-work-lane side-effect claims false.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_merge_backlog import (
    build_real_pr_creation_approval_gate,
    execute_approved_real_pr_creation,
)
from prismatic.completed_work_gate import demo_completed_work_packet

PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER = "PROMPT6_EXECUTOR_AUDIT_CANARY_OK"
PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED = "PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED"
PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_MARKER = "PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_OK"
DEFAULT_EXECUTOR_RUNS_STATE_NAME = "agy_executor_runs.json"


def default_executor_runs_state_path() -> Path:
    """Return the portable Prompt 6 executor-run ledger path."""

    explicit = os.environ.get("PRISMATIC_AGY_EXECUTOR_RUNS_STATE")
    if explicit:
        return Path(explicit)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / DEFAULT_EXECUTOR_RUNS_STATE_NAME
    return Path("prismatic_state") / DEFAULT_EXECUTOR_RUNS_STATE_NAME


@dataclass(frozen=True)
class ExecutorRunRecord:
    run_id: str
    completed_work_id: str
    approval_id: str | None
    requested_by: str
    executor_mode: str
    execute: bool
    allow_real_side_effects: bool
    status: str
    marker: str
    commands_rendered: bool
    commands_executed: bool
    real_github_pr_created: bool
    git_branch_created: bool
    auto_merge_enabled: bool
    production_deployed: bool
    AGY_dispatch: bool
    prompt55_executor_marker: str | None
    blocked_reasons: list[str]
    log_path: str | None
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "completed_work_id": self.completed_work_id,
            "approval_id": self.approval_id,
            "requested_by": self.requested_by,
            "executor_mode": self.executor_mode,
            "execute": self.execute,
            "allow_real_side_effects": self.allow_real_side_effects,
            "status": self.status,
            "marker": self.marker,
            "commands_rendered": self.commands_rendered,
            "commands_executed": self.commands_executed,
            "real_github_pr_created": self.real_github_pr_created,
            "git_branch_created": self.git_branch_created,
            "auto_merge_enabled": self.auto_merge_enabled,
            "production_deployed": self.production_deployed,
            "AGY_dispatch": self.AGY_dispatch,
            "prompt55_executor_marker": self.prompt55_executor_marker,
            "blocked_reasons": list(self.blocked_reasons),
            "log_path": self.log_path,
            "created_at": self.created_at,
        }


def _read_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or default_executor_runs_state_path()
    if not state_path.exists():
        return {"runs": []}
    try:
        data = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return {"runs": []}
    if not isinstance(data, dict):
        return {"runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        data["runs"] = []
    return data


def _write_state(data: Mapping[str, Any], path: Path | None = None) -> None:
    state_path = path or default_executor_runs_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def list_executor_runs(
    *, limit: int = 25, state_path: Path | None = None
) -> dict[str, Any]:
    """List recent Prompt 6 executor run records."""

    data = _read_state(state_path)
    runs = list(data.get("runs") or [])
    safe_limit = max(1, min(int(limit or 25), 100))
    return {
        "status": "ok",
        "marker": PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER,
        "runs": runs[-safe_limit:][::-1],
        "count": len(runs),
        "state_path": str(state_path or default_executor_runs_state_path()),
        "non_claims": _prompt6_non_claims(),
    }


def get_executor_run(run_id: str, *, state_path: Path | None = None) -> dict[str, Any]:
    """Return one Prompt 6 executor run record."""

    data = _read_state(state_path)
    for run in data.get("runs") or []:
        if isinstance(run, dict) and run.get("run_id") == run_id:
            return {
                "status": "ok",
                "marker": PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER,
                "run": run,
                "non_claims": _prompt6_non_claims(),
            }
    raise KeyError(run_id)


def record_executor_run(
    executor_payload: Mapping[str, Any],
    *,
    requested_by: str = "operator",
    log_path: str | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Persist one Prompt 5.5 executor result as a Prompt 6 audit run."""

    executor_result = _mapping(executor_payload.get("executor_result"))
    side_effects = _mapping(executor_payload.get("side_effects"))
    policy_gate = _mapping(executor_payload.get("policy_gate"))
    blocked_reasons = policy_gate.get("blocked_reasons") or []
    if not isinstance(blocked_reasons, list):
        blocked_reasons = [str(blocked_reasons)]
    executor_mode = str(executor_payload.get("executor_mode") or "dry_run")
    status = str(executor_payload.get("status") or "blocked")
    marker = (
        PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER
        if status == "ok" and executor_mode == "dry_run"
        else PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED
    )
    record = ExecutorRunRecord(
        run_id=f"prompt6-exec-run-{uuid4().hex[:12]}",
        completed_work_id=str(executor_payload.get("completed_work_id") or ""),
        approval_id=_approval_id(executor_payload),
        requested_by=requested_by
        or str(executor_payload.get("requested_by") or "operator"),
        executor_mode=executor_mode,
        execute=bool(
            _mapping(executor_payload.get("executor_plan")).get("execute_requested")
        ),
        allow_real_side_effects=bool(
            _mapping(executor_payload.get("executor_plan")).get(
                "allow_real_side_effects"
            )
        ),
        status=status,
        marker=marker,
        commands_rendered=bool(executor_payload.get("commands_rendered")),
        commands_executed=bool(executor_payload.get("commands_executed")),
        real_github_pr_created=bool(executor_result.get("real_github_pr_created"))
        or bool(side_effects.get("real_github_pr_created")),
        git_branch_created=bool(executor_result.get("git_branch_created"))
        or bool(side_effects.get("git_branch_created")),
        auto_merge_enabled=False,
        production_deployed=False,
        AGY_dispatch=False,
        prompt55_executor_marker=str(executor_payload.get("marker") or ""),
        blocked_reasons=[str(reason) for reason in blocked_reasons],
        log_path=log_path,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    data = _read_state(state_path)
    runs = data.setdefault("runs", [])
    if not isinstance(runs, list):
        runs = []
        data["runs"] = runs
    runs.append(record.as_dict())
    _write_state(data, state_path)
    return record.as_dict()


def build_prompt6_executor_canary_dry_run(
    *,
    completed_work_id: str | None = None,
    requested_by: str = "operator",
    executor_mode: str = "dry_run",
    execute: bool = False,
    allow_real_side_effects: bool = False,
    log_path: str | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Run and record a replay-safe Prompt 6 executor canary.

    If no completed_work_id is supplied, a safe demo completed-work fixture is
    ingested into the configured completed-work store. The executor call remains
    dry-run by default and never enables the real-mode environment gate.
    """

    mode = (executor_mode or "dry_run").strip().lower()
    row_id = completed_work_id or _create_canary_completed_work_id()
    approval_token = f"APPROVE_REAL_PR:{row_id}"
    approval_gate = build_real_pr_creation_approval_gate(
        row_id,
        requested_by=requested_by,
        approved_by="prompt6-canary-operator",
        approval_token=approval_token,
    )
    approval_record = _mapping(approval_gate.get("approval_record"))
    executor = execute_approved_real_pr_creation(
        row_id,
        approval_id=str(approval_record.get("approval_id") or ""),
        approved_by="prompt6-canary-operator",
        approval_token=approval_token,
        requested_by=requested_by,
        final_operator_trigger=True,
        execute=bool(execute),
        executor_mode=mode,
        allow_real_side_effects=bool(allow_real_side_effects),
    )
    record = record_executor_run(
        executor,
        requested_by=requested_by,
        log_path=log_path,
        state_path=state_path,
    )
    marker = (
        PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER
        if record["status"] == "ok" and mode == "dry_run"
        else PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED
    )
    blocked_reasons = list(record.get("blocked_reasons") or [])
    return {
        "status": "ok" if marker == PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER else "blocked",
        "marker": marker,
        "run_id": record["run_id"],
        "completed_work_id": row_id,
        "approval_id": record.get("approval_id"),
        "requested_by": requested_by,
        "executor_mode": mode,
        "commands_rendered": record["commands_rendered"],
        "commands_executed": record["commands_executed"],
        "real_github_pr_created": record["real_github_pr_created"],
        "git_branch_created": record["git_branch_created"],
        "auto_merge_enabled": False,
        "production_deployed": False,
        "AGY_dispatch": False,
        "blocked_reasons": blocked_reasons,
        "executor_result": executor,
        "run_record": record,
        "recent_runs": list_executor_runs(limit=5, state_path=state_path)["runs"],
        "non_claims": _prompt6_non_claims(),
    }


def _create_canary_completed_work_id() -> str:
    packet = demo_completed_work_packet()
    packet["issue_identifier"] = "GRO-3837"
    packet["source_branch"] = "feature/agy/GRO-3837-prompt6-canary"
    packet["base_branch"] = "origin/main"
    packet["verification_lane"] = "backend-api"
    packet["changed_files"] = [
        "prismatic/agy_merge_backlog.py",
        "tests/test_agy_merge_backlog.py",
    ]
    packet["proof"].update(
        {
            "result": "PASS",
            "command": "python3 -m pytest -q tests/test_agy_merge_backlog.py tests/test_agy_merge_backlog_api.py",
            "scope": "Prompt 6 executor canary fixture proof",
            "log": "/tmp/prompt6-executor-canary-proof.log",
            "marker": "AGY_PR_VERIFICATION_GATE_OK",
            "non_claims": ["auto_merge", "production_deploy"],
        }
    )
    row = ingest_completed_work(packet)
    return row.id


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _approval_id(payload: Mapping[str, Any]) -> str | None:
    gate = _mapping(payload.get("approval_gate"))
    record = _mapping(gate.get("approval_record"))
    approval_id = record.get("approval_id")
    return str(approval_id) if approval_id else None


def _prompt6_non_claims() -> dict[str, bool]:
    return {
        "real_github_pr_created_by_completed_work_lane": False,
        "git_branch_created_by_completed_work_lane": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "AGY_dispatch": False,
    }
