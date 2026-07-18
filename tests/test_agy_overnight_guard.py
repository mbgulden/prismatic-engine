from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    AgyOvernightGuardStore,
    evaluate_overnight_readiness,
    list_overnight_run_attempts,
    operator_pause,
    record_guard_decision,
    record_overnight_run_attempt,
    set_operator_pause,
)


REPO = Path(__file__).resolve().parents[1]


def agy_packet(
    issue: str = "LOCAL-AGY-OVERNIGHT-GUARD-TEST",
    marker: str = "AGY_TASK_RESULT_PACKET_OK",
) -> dict:
    return {
        "agent": "agy",
        "issue_identifier": issue,
        "branch": f"feature/{issue.lower()}",
        "base_branch": "main",
        "merge_lane": "docs",
        "changed_files": ["docs/agy-overnight-guard-test.md"],
        "result_artifacts": [
            {
                "path": str(
                    Path.home() / ".prismatic" / "agy-canaries" / issue / "RESULT.md"
                )
            }
        ],
        "result_summary": "AGY one-task guard fixture",
        "verification": {
            "result": "PASS",
            "commands": ["local fixture proof"],
            "log_path": "/tmp/agy-overnight-guard-test.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": [
            "bulk_agy_dispatch",
            "overnight_autopilot_ready",
            "auto_merge_enabled",
            "production_deploy",
            "real_github_pr_created",
        ],
        "marker": marker,
    }


def seed_one_task_success(monkeypatch, tmp_path):
    completed_db = tmp_path / "completed_work.db"
    guard_db = tmp_path / "overnight_guard.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(completed_db))
    monkeypatch.setenv("PRISMATIC_AGY_OVERNIGHT_GUARD_STATE", str(guard_db))
    row = ingest_completed_work(agy_packet(), db_path=completed_db)
    return row, guard_db


def seed_limited_overnight_success(monkeypatch, tmp_path):
    completed_db = tmp_path / "completed_work.db"
    guard_db = tmp_path / "overnight_guard.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(completed_db))
    monkeypatch.setenv("PRISMATIC_AGY_OVERNIGHT_GUARD_STATE", str(guard_db))
    row = ingest_completed_work(
        agy_packet(
            "LOCAL-AGY-LIMITED-OVERNIGHT-GUARD-TEST",
            marker="AGY_LIMITED_OVERNIGHT_DRY_RUN_PACKET_OK",
        ),
        db_path=completed_db,
    )
    return row, guard_db


def test_allows_exactly_configured_agy_one_and_two_task_policy(monkeypatch, tmp_path):
    seed_one_task_success(monkeypatch, tmp_path)

    one = evaluate_overnight_readiness(max_tasks=1, allowed_agents=["agy"])
    two = evaluate_overnight_readiness(max_tasks=2, allowed_agents=["agy"])

    assert one.allowed is True
    assert one.readiness_state == "ready"
    assert one.latest_one_task_success_marker == "AGY_AUTOPILOT_ONE_TASK_DRY_RUN_OK"
    assert one.policy["auto_merge_enabled"] is False
    assert one.policy["production_deploy_enabled"] is False
    assert one.policy["real_github_pr_create_enabled"] is False
    assert two.allowed is True
    assert two.requested_max_tasks == 2


def test_limited_overnight_dry_run_marker_satisfies_readiness_for_prompt2(
    monkeypatch, tmp_path
):
    seed_limited_overnight_success(monkeypatch, tmp_path)

    result = evaluate_overnight_readiness(max_tasks=2, allowed_agents=["agy"])

    assert result.allowed is True
    assert result.latest_one_task_success_marker == "AGY_LIMITED_OVERNIGHT_DRY_RUN_OK"
    assert "latest one-task AGY proof missing" not in result.blockers


def test_blocks_auto_merge_production_bulk_and_high_task_count(monkeypatch, tmp_path):
    seed_one_task_success(monkeypatch, tmp_path)

    assert (
        "auto_merge=true is forbidden"
        in evaluate_overnight_readiness(auto_merge=True).blockers
    )
    assert (
        "production_deploy=true is forbidden"
        in evaluate_overnight_readiness(production_deploy=True).blockers
    )
    assert (
        "bulk dispatch requested"
        in evaluate_overnight_readiness(bulk_dispatch=True).blockers
    )
    high = evaluate_overnight_readiness(max_tasks=3)
    assert high.allowed is False
    assert "max_tasks exceeds allowed cap 2" in high.blockers


def test_blocks_unknown_agent_unresolved_failure_pause_and_missing_preflight(
    monkeypatch, tmp_path
):
    _, guard_db = seed_one_task_success(monkeypatch, tmp_path)
    assert (
        "unknown or disabled agent requested: ned"
        in evaluate_overnight_readiness(allowed_agents=["agy", "ned"]).blockers
    )
    assert (
        "required skills/preflight missing"
        in evaluate_overnight_readiness(required_preflight_ok=False).blockers
    )

    store = AgyOvernightGuardStore(guard_db)
    store.record_overnight_run_attempt(
        requested_by="fred",
        allowed_agents=["agy"],
        max_tasks=1,
        run_status="failed_unresolved",
        summary="fixture unresolved failure",
    )
    failed = evaluate_overnight_readiness()
    assert "previous run failed and is unresolved" in failed.blockers

    set_operator_pause(True, db_path=guard_db)
    paused = evaluate_overnight_readiness(unresolved_previous_failure=False)
    assert paused.readiness_state == "paused"
    assert "operator pause is active" in paused.blockers
    set_operator_pause(False, db_path=guard_db)
    assert operator_pause(db_path=guard_db) is False


def test_blocks_when_required_lane_markers_missing(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "PRISMATIC_AGY_COMPLETED_WORK_DB", str(tmp_path / "empty_completed.db")
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_OVERNIGHT_GUARD_STATE", str(tmp_path / "guard.db")
    )

    result = evaluate_overnight_readiness()

    assert result.allowed is False
    assert "latest one-task AGY proof missing" in result.blockers
    assert "completed-work ingestion unavailable or no healthy row" in result.blockers


def test_persistence_records_decisions_and_run_attempts(monkeypatch, tmp_path):
    seed_one_task_success(monkeypatch, tmp_path)
    decision = evaluate_overnight_readiness(max_tasks=1)
    persisted = record_guard_decision(decision)
    run = record_overnight_run_attempt(
        run_status="not_started", summary="guard dry-run only"
    )

    assert persisted.guard_decision_id.startswith("agy-ogd-")
    assert persisted.last_success_marker == "AGY_AUTOPILOT_ONE_TASK_DRY_RUN_OK"
    assert run.run_attempt_id.startswith("agy-ogr-")
    assert list_overnight_run_attempts(limit=1)[0].run_status == "not_started"


def test_cli_status_evaluate_pause_resume_from_repo_root(monkeypatch, tmp_path):
    seed_one_task_success(monkeypatch, tmp_path)
    env = os.environ.copy()
    env["PRISMATIC_AGY_OVERNIGHT_GUARD_STATE"] = str(tmp_path / "cli_guard.db")
    env["PRISMATIC_AGY_COMPLETED_WORK_DB"] = str(tmp_path / "completed_work.db")
    script = REPO / "scripts" / "agy_overnight_guard.py"

    status = subprocess.run(
        [sys.executable, str(script), "status", "--limit", "1"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    assert status.returncode == 0, status.stdout
    assert json.loads(status.stdout)["marker"] == AGY_OVERNIGHT_READINESS_GUARD_MARKER

    eval_cmd = subprocess.run(
        [sys.executable, str(script), "evaluate", "--max-tasks", "1", "--agent", "agy"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    assert eval_cmd.returncode == 0, eval_cmd.stdout
    assert json.loads(eval_cmd.stdout)["guard"]["readiness_state"] == "ready"

    pause = subprocess.run(
        [sys.executable, str(script), "pause"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    assert json.loads(pause.stdout)["operator_pause"] is True
    resume = subprocess.run(
        [sys.executable, str(script), "resume"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    assert json.loads(resume.stdout)["operator_pause"] is False
