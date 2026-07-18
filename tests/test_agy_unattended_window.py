from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.agy_unattended_window import (
    AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER,
    AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER,
    UnattendedWindowStore,
    evaluate_unattended_window,
    request_approval,
    set_pause,
    status_payload,
)


def queue_ok(depth: int = 0):
    return {
        "marker": "LINEAR_WEBHOOK_QUEUE_ACTIVE_OK",
        "assigned_agent_marker": "ASSIGNED_AGENT_EVENT_DISPATCH_OK",
        "result_writeback_marker": "ASSIGNED_AGENT_RESULT_WRITEBACK_OK",
        "dispatch_recovery_marker": "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK",
        "queue_depth": depth,
        "pending_count": depth,
    }


def queue_missing():
    payload = queue_ok()
    payload["assigned_agent_marker"] = "MISSING"
    return payload


def guard_ok(**kwargs):
    return {
        "allowed": True,
        "readiness_state": "ready",
        "reason": "ready",
        "blockers": [],
        "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK",
    }


def guard_blocked(**kwargs):
    return {
        "allowed": False,
        "readiness_state": "blocked",
        "reason": "blocked by test",
        "blockers": ["blocked by test"],
        "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK",
    }


def latest_ok():
    return {
        "marker": "AGY_LIMITED_OVERNIGHT_DRY_RUN_OK",
        "status": "pass",
        "stop_reason": "completed_one_task_and_stopped",
        "launched_tasks": 1,
        "verification_gate": "pass",
    }


def latest_blocked():
    return {
        "marker": "AGY_LIMITED_OVERNIGHT_DRY_RUN_OK",
        "status": "blocked",
        "stop_reason": "blocked: test",
    }


def model_ok(model: str):
    assert model == "Gemini 3.5 Flash (Medium)"
    return True, "OK"


def eval_with(tmp_path: Path, **overrides):
    return evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2, **overrides},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )


def test_unattended_window_allows_approved_two_task_guard_without_launch(tmp_path):
    result = eval_with(tmp_path, operator_approved=True)
    assert result["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER
    assert result["allowed"] is True
    assert result["max_tasks"] == 2
    assert result["one_task_at_a_time"] is True
    assert result["stop_on_first_failure"] is True
    assert result["two_AGY_tasks_launched"] is False
    assert result["launched_tasks"] == 0
    assert result["auto_merge"] is False
    assert result["production_deploy"] is False
    assert result["real_github_pr_create"] is False
    assert result["bulk_dispatch"] is False
    assert result["live_Linear_mutations"] is False
    stored = UnattendedWindowStore(tmp_path / "window.db").get(result["evaluation"]["evaluation_id"])
    assert stored["allowed"] is True


def test_unattended_window_warns_when_operator_approval_not_yet_present(tmp_path):
    result = eval_with(tmp_path)
    assert result["allowed"] is True
    assert "operator approval required" in result["warnings"][0]
    assert result["launched_tasks"] == 0


@pytest.mark.parametrize("payload,reason", [
    ({"agent": "kai"}, "resolved agent is not agy"),
    ({"allowed_agents": ["agy", "kai"]}, 'allowed_agents must be exactly ["agy"]'),
    ({"max_tasks": 3}, "max_tasks > 2"),
    ({"one_task_at_a_time": False}, "one_task_at_a_time must be true"),
    ({"stop_on_first_failure": False}, "stop_on_first_failure must be true"),
    ({"operator_approval_required": False}, "operator_approval_required must be true"),
    ({"auto_merge": True}, "auto_merge requested"),
    ({"production_deploy": True}, "production_deploy requested"),
    ({"real_github_pr_create": True}, "real_github_pr_create requested"),
    ({"bulk_dispatch": True}, "bulk_dispatch requested"),
    ({"live_linear_mutations": True}, "live_Linear_mutations requested"),
])
def test_unattended_window_blocks_forbidden_requests(tmp_path, payload, reason):
    result = eval_with(tmp_path, **payload)
    assert result["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER
    assert reason in result["blockers"]
    assert result["launched_tasks"] == 0


def test_unattended_window_blocks_missing_prompt1_success(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=lambda: None,
    )
    assert result["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER
    assert "previous AGY_LIMITED_OVERNIGHT_DRY_RUN_OK missing" in result["blockers"]
    assert result["launched_tasks"] == 0


def test_unattended_window_blocks_assigned_agent_marker_absent_before_launch(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=queue_missing,
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )
    assert result["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER
    assert any("assigned-agent recovery markers missing" in blocker for blocker in result["blockers"])
    assert result["launched_tasks"] == 0


def test_unattended_window_blocks_overnight_guard_blocked(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_blocked,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )
    assert result["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_BLOCKED_MARKER
    assert "overnight guard blocked/paused/manual_review" in result["blockers"]


def test_unattended_window_blocks_queue_depth_unless_accepted(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(depth=1),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )
    assert "queue depth is nonzero and not explicitly accepted" in result["blockers"]
    accepted = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2, "accept_nonzero_queue": True},
        db_path=tmp_path / "accepted.db",
        queue_status_fn=lambda: queue_ok(depth=1),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )
    assert "queue depth is nonzero and not explicitly accepted" not in accepted["blockers"]


def test_unattended_window_blocks_operator_pause(tmp_path):
    db = tmp_path / "window.db"
    set_pause(True, db_path=db)
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=db,
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_ok,
    )
    assert "operator pause active" in result["blockers"]


def test_unattended_window_blocks_latest_unresolved_blocker(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_ok,
        model_preflight_fn=model_ok,
        latest_run_fn=latest_blocked,
    )
    assert "latest limited overnight run is not pass" in result["blockers"]
    assert "latest run has unresolved blocker" in result["blockers"]


def test_unattended_window_blocks_model_preflight_failure_without_launch(tmp_path):
    result = evaluate_unattended_window(
        {"agent": "agy", "max_tasks": 2},
        db_path=tmp_path / "window.db",
        queue_status_fn=lambda: queue_ok(),
        guard_fn=guard_ok,
        model_preflight_fn=lambda model: (False, "missing model"),
        latest_run_fn=latest_ok,
    )
    assert "model preflight fails" in result["blockers"]
    assert result["launched_tasks"] == 0


def test_request_approval_status_pause_resume(tmp_path):
    db = tmp_path / "window.db"
    requested = request_approval({"agent": "agy", "max_tasks": 2}, db_path=db)
    assert requested["status"] == "approval_requested"
    assert requested["evaluation"]["operator_approved"] is False
    assert status_payload(db_path=db)["latest"]["status"] == "approval_requested"
    assert set_pause(True, db_path=db)["operator_pause"] is True
    assert status_payload(db_path=db)["operator_pause"] is True
    assert set_pause(False, db_path=db)["operator_pause"] is False


def test_cli_status_and_evaluate_work_from_outside_repo_root(tmp_path):
    env = os.environ.copy()
    env["PRISMATIC_STATE_DIR"] = str(tmp_path)
    repo = Path(__file__).resolve().parents[1]
    status = subprocess.run(
        [sys.executable, str(repo / "scripts" / "agy_unattended_window.py"), "status"],
        cwd="/tmp",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert json.loads(status.stdout)["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER
