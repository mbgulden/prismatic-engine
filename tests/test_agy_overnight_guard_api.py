from __future__ import annotations

from fastapi.testclient import TestClient

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_overnight_guard import AGY_OVERNIGHT_READINESS_GUARD_MARKER, record_overnight_run_attempt
from prismatic.gateway import server


def agy_packet(issue: str = "LOCAL-AGY-OVERNIGHT-GUARD-API") -> dict:
    return {
        "agent": "agy",
        "issue_identifier": issue,
        "branch": f"feature/{issue.lower()}",
        "base_branch": "main",
        "merge_lane": "docs",
        "changed_files": ["docs/agy-overnight-guard-api.md"],
        "result_artifacts": [{"path": f"/home/ubuntu/.prismatic/agy-canaries/{issue}/RESULT.md"}],
        "result_summary": "AGY one-task API guard fixture",
        "verification": {
            "result": "PASS",
            "commands": ["local fixture proof"],
            "log_path": "/tmp/agy-overnight-api.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": [
            "bulk_agy_dispatch",
            "overnight_autopilot_ready",
            "auto_merge_enabled",
            "production_deploy",
            "real_github_pr_created",
        ],
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }


def seed(monkeypatch, tmp_path):
    completed_db = tmp_path / "completed_work.db"
    guard_db = tmp_path / "overnight_guard.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(completed_db))
    monkeypatch.setenv("PRISMATIC_AGY_OVERNIGHT_GUARD_STATE", str(guard_db))
    row = ingest_completed_work(agy_packet(), db_path=completed_db)
    return row, guard_db


def test_overnight_guard_api_status_evaluate_and_gateway_alias(monkeypatch, tmp_path):
    row, _ = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    status = client.get("/api/gateway/agy/overnight-guard")
    assert status.status_code == 200
    body = status.json()
    assert body["marker"] == AGY_OVERNIGHT_READINESS_GUARD_MARKER
    assert body["guard"]["allowed"] is True
    assert body["guard"]["readiness_state"] == "ready"
    assert body["guard"]["latest_completed_work_id"] == row.id
    assert body["tasks_launched"] == 0

    local = client.post("/api/agy/overnight-guard/evaluate", json={"max_tasks": 2, "allowed_agents": ["agy"]})
    assert local.status_code == 200
    eval_body = local.json()
    assert eval_body["marker"] == AGY_OVERNIGHT_READINESS_GUARD_MARKER
    assert eval_body["guard"]["requested_max_tasks"] == 2
    assert eval_body["guard"]["allowed"] is True
    assert eval_body["non_claims"]["auto_merge_enabled"] is False


def test_overnight_guard_api_blocks_bad_requests_and_persists_pause(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    blocked = client.post(
        "/api/gateway/agy/overnight-guard/evaluate",
        json={"max_tasks": 3, "allowed_agents": ["agy", "ned"], "auto_merge": True, "bulk_dispatch": True},
    )
    assert blocked.status_code == 200
    guard = blocked.json()["guard"]
    assert guard["allowed"] is False
    assert "auto_merge=true is forbidden" in guard["blockers"]
    assert "unknown or disabled agent requested: ned" in guard["blockers"]
    assert "bulk dispatch requested" in guard["blockers"]

    pause = client.post("/api/gateway/agy/overnight-guard/pause")
    assert pause.status_code == 200
    assert pause.json()["operator_pause"] is True
    paused_status = client.get("/api/agy/overnight-guard")
    assert paused_status.json()["guard"]["readiness_state"] == "paused"

    resume = client.post("/api/gateway/agy/overnight-guard/resume")
    assert resume.status_code == 200
    assert resume.json()["operator_pause"] is False
    resumed_status = client.get("/api/gateway/agy/overnight-guard")
    assert resumed_status.json()["guard"]["readiness_state"] == "ready"


def test_overnight_guard_runs_api(monkeypatch, tmp_path):
    _, guard_db = seed(monkeypatch, tmp_path)
    record_overnight_run_attempt(
        db_path=guard_db,
        requested_by="fred",
        allowed_agents=["agy"],
        max_tasks=1,
        run_status="not_started",
        summary="guard design only",
    )
    client = TestClient(server.app)

    runs = client.get("/api/gateway/agy/overnight-guard/runs")

    assert runs.status_code == 200
    body = runs.json()
    assert body["marker"] == AGY_OVERNIGHT_READINESS_GUARD_MARKER
    assert body["count"] == 1
    assert body["runs"][0]["run_status"] == "not_started"
    assert body["tasks_launched"] == 0
