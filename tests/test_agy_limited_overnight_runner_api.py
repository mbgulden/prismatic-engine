from __future__ import annotations

import json

from fastapi.testclient import TestClient

import prismatic.gateway.server as server
from prismatic.agy_limited_overnight_runner import AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER


def test_limited_overnight_api_dry_run_endpoint_returns_non_claims_and_one_task(monkeypatch, tmp_path):
    def fake_run(request):
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
            "completed_work_id": "cw-test",
            "merge_backlog_id": "mb-test",
            "verification_gate": "pass",
            "run": {"run_id": "run-test", "launched_tasks": 1, "max_tasks": 1},
            "non_claims": {
                "overnight_autopilot_unbounded": False,
                "auto_merge_enabled": False,
                "bulk_agy_dispatch": False,
                "production_deploy": False,
                "canonical_full_suite_green": False,
                "real_github_pr_created": False,
                "more_than_one_AGY_task": False,
            },
        }

    monkeypatch.setattr(server, "run_limited_overnight_dry_run", fake_run)
    client = TestClient(server.app)
    resp = client.post("/api/gateway/agy/limited-overnight/dry-run", json={"agent": "agy", "max_tasks": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["marker"] == AGY_LIMITED_OVERNIGHT_DRY_RUN_MARKER
    assert body["AGY_task_count"] == 1
    assert body["run"]["launched_tasks"] <= 1
    assert body["non_claims"]["real_github_pr_created"] is False
    assert body["bulk_dispatch"] is False


def test_limited_overnight_api_status_run_and_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    from prismatic.agy_limited_overnight_runner import LimitedOvernightRunStore

    store = LimitedOvernightRunStore()
    row = store.upsert({"run_id": "run-api", "status": "ready", "marker": "AGY_LIMITED_OVERNIGHT_RUNNER_OK"})
    client = TestClient(server.app)

    listing = client.get("/api/gateway/agy/limited-overnight/runs")
    assert listing.status_code == 200
    assert listing.json()["latest"]["run_id"] == row["run_id"]

    detail = client.get("/api/gateway/agy/limited-overnight/runs/run-api")
    assert detail.status_code == 200
    assert detail.json()["run"]["run_id"] == "run-api"

    stopped = client.post("/api/gateway/agy/limited-overnight/stop")
    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] == "stopped"
