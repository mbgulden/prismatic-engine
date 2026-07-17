from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.agy_limited_overnight_runner import (
    AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER,
    AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER,
    AGY_LIMITED_OVERNIGHT_RUNNER_MARKER,
    AGY_LIMITED_OVERNIGHT_PACKET_MARKER,
    LimitedOvernightRunStore,
    RunnerRequest,
    run_limited_overnight_dry_run,
    status_payload,
)


def allowed_guard(**kwargs):
    return {
        "allowed": True,
        "readiness_state": "ready",
        "reason": "ready",
        "blockers": [],
        "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK",
        "requested_agents": list(kwargs.get("allowed_agents") or ["agy"]),
        "requested_max_tasks": int(kwargs.get("max_tasks", 1)),
    }


def blocked_guard(**kwargs):
    return {
        "allowed": False,
        "readiness_state": "blocked",
        "reason": "fixture guard blocked",
        "blockers": ["fixture guard blocked"],
        "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK",
    }


def packet(tmp_path: Path, issue: str = "AGY-LIMITED-TEST") -> dict:
    source = Path("/home/ubuntu/.prismatic/agy-test-artifacts") / tmp_path.name
    artifact = source / "OBSERVATION.md"
    return {
        "agent": "agy",
        "issue_identifier": issue,
        "source_branch": "feature/agy-limited-overnight-test",
        "source_path": str(source),
        "base_branch": "main",
        "merge_lane": "docs",
        "verification_lane": "docs",
        "changed_files": ["docs/agy-limited-overnight-test.md"],
        "result_artifacts": [{"path": str(artifact)}],
        "result_summary": "Safe observation-only limited overnight dry run.",
        "proof": {
            "result": "PASS",
            "command": "fake agy observation artifact write; no production mutation",
            "log": "/tmp/fake-agy-limited-overnight.log",
            "scope": "docs observation only",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "marker": AGY_LIMITED_OVERNIGHT_PACKET_MARKER,
            "non_claims": [
                "bulk_agy_dispatch",
                "overnight_autopilot_unbounded",
                "auto_merge_enabled",
                "production_deploy",
                "real_github_pr_created",
                "more_than_one_AGY_task",
            ],
        },
        "non_claims": [
            "bulk_agy_dispatch",
            "overnight_autopilot_unbounded",
            "auto_merge_enabled",
            "production_deploy",
            "real_github_pr_created",
            "more_than_one_AGY_task",
        ],
        "marker": AGY_LIMITED_OVERNIGHT_PACKET_MARKER,
    }


def ok_model(model: str):
    assert model == "Gemini 3.5 Flash (Medium)"
    return True, "OK"


def run_with(tmp_path: Path, **overrides):
    launched = {"count": 0}

    def launch(model: str):
        launched["count"] += 1
        return 0, json.dumps(packet(tmp_path))

    req = RunnerRequest.from_mapping({"max_tasks": 1, "agent": "agy", **overrides})
    result = run_limited_overnight_dry_run(
        req,
        db_path=tmp_path / "runs.db",
        guard_fn=allowed_guard,
        model_preflight_fn=ok_model,
        agy_launch_fn=launch,
    )
    return result, launched


def test_runner_blocks_when_guard_blocks(tmp_path):
    result = run_limited_overnight_dry_run(
        {"agent": "agy", "max_tasks": 1},
        db_path=tmp_path / "runs.db",
        guard_fn=blocked_guard,
        model_preflight_fn=ok_model,
        agy_launch_fn=lambda model: pytest.fail("AGY must not launch"),
    )
    assert result["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER
    assert result["run"]["runner_called_guard"] is True
    assert result["run"]["launched_tasks"] == 0


def test_runner_blocks_operator_pause(tmp_path):
    def paused_guard(**kwargs):
        return {"allowed": False, "readiness_state": "paused", "reason": "operator pause", "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK"}

    result = run_limited_overnight_dry_run({"agent": "agy"}, db_path=tmp_path / "runs.db", guard_fn=paused_guard)
    assert result["status"] == "blocked"
    assert "guard readiness blocked" in result["reason"]
    assert result["run"]["launched_tasks"] == 0


@pytest.mark.parametrize("payload,reason", [
    ({"max_tasks": 2}, "max_tasks > 1"),
    ({"auto_merge": True}, "auto_merge requested"),
    ({"production_deploy": True}, "production_deploy requested"),
    ({"real_github_pr_create": True}, "real_github_pr_create requested"),
])
def test_runner_blocks_forbidden_requests_before_launch(tmp_path, payload, reason):
    result = run_limited_overnight_dry_run(
        {"agent": "agy", **payload},
        db_path=tmp_path / "runs.db",
        guard_fn=allowed_guard,
        model_preflight_fn=lambda model: pytest.fail("model preflight must not run"),
        agy_launch_fn=lambda model: pytest.fail("AGY must not launch"),
    )
    assert result["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER
    assert reason in result["reason"]
    assert result["run"]["launched_tasks"] == 0


def test_runner_launches_zero_tasks_on_failed_model_preflight(tmp_path):
    result = run_limited_overnight_dry_run(
        {"agent": "agy"},
        db_path=tmp_path / "runs.db",
        guard_fn=allowed_guard,
        model_preflight_fn=lambda model: (False, "no model"),
        agy_launch_fn=lambda model: pytest.fail("AGY must not launch"),
    )
    assert result["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER
    assert result["run"]["model_preflight_ok"] is False
    assert result["run"]["launched_tasks"] == 0


def test_runner_launches_exactly_one_task_persists_and_ingests_completed_work(tmp_path):
    result, launched = run_with(tmp_path)
    assert result["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER
    assert launched["count"] == 1
    assert result["runner_called_guard"] is True
    assert result["guard_allowed"] is True
    assert result["resolved_agent"] == "agy"
    assert result["AGY_task_count"] == 1
    assert result["no_other_tasks_launched"] is True
    assert result["bulk_dispatch"] is False
    assert result["auto_merge"] is False
    assert result["production_deploy"] is False
    assert result["real_github_pr_created"] is False
    assert result["stop_on_first_failure"] is True
    assert result["completed_work_ingested"] is True
    assert result["merge_backlog_evaluated"] is True
    assert result["verification_gate_evaluated"] is True
    assert result["completed_work_id"]
    assert result["merge_backlog_id"]
    assert result["verification_gate"] == "pass"
    stored = LimitedOvernightRunStore(tmp_path / "runs.db").get(result["run"]["run_id"])
    assert stored["status"] == "pass"
    assert stored["completed_work_id"] == result["completed_work_id"]


def test_runner_handles_invalid_packet_as_blocked_without_second_task(tmp_path):
    launched = {"count": 0}

    def bad_launch(model: str):
        launched["count"] += 1
        return 0, "not json"

    result = run_limited_overnight_dry_run(
        {"agent": "agy"},
        db_path=tmp_path / "runs.db",
        guard_fn=allowed_guard,
        model_preflight_fn=ok_model,
        agy_launch_fn=bad_launch,
    )
    assert result["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED_MARKER
    assert launched["count"] == 1
    assert result["run"]["launched_tasks"] == 1


def test_status_payload_reads_real_state(tmp_path):
    result, _ = run_with(tmp_path)
    status = status_payload(db_path=tmp_path / "runs.db")
    assert status["marker"] == AGY_LIMITED_OVERNIGHT_RUNNER_MARKER
    assert status["latest"]["run_id"] == result["run"]["run_id"]


def test_cli_preflight_and_status_work_from_outside_repo_root(tmp_path):
    env = os.environ.copy()
    env["PRISMATIC_STATE_DIR"] = str(tmp_path)
    repo = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(repo)
    status = subprocess.run(
        [sys.executable, str(repo / "scripts/agy_limited_overnight_runner.py"), "status"],
        cwd="/tmp",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert status.returncode == 0, status.stdout
    assert "AGY_LIMITED_OVERNIGHT_RUNNER_OK" in status.stdout
    blocked = subprocess.run(
        [sys.executable, str(repo / "scripts/agy_limited_overnight_runner.py"), "preflight", "--max-tasks", "2", "--agent", "agy"],
        cwd="/tmp",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert blocked.returncode == 2
    assert "AGY_LIMITED_OVERNIGHT_DRY_RUN_BLOCKED" in blocked.stdout
