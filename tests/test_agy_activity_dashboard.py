from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.agy_activity import list_agy_activity_runs
from prismatic.gateway.server import app

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _fixture_runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    run = runtime / "agy-dashboard-test"
    run.mkdir(parents=True)
    result = tmp_path / "spool" / "RESULT.md"
    plan = tmp_path / "spool" / "PLAN.md"
    result.parent.mkdir()
    result.write_text("PRISMATIC_AGY_RESULT_V1\n")
    plan.write_text("PLAN\n")
    _write_json(
        run / "harness-run.json",
        {
            "run_id": run.name,
            "task_ref": "GRO-DASH",
            "started_at": 1000.0,
            "result_path": str(result),
            "plan_path": str(plan),
            "attempt": 1,
            "event_id": "task-admission:test",
            "attempt_token": "must-not-project",
            "verification_status": "pending",
        },
    )
    _write_json(
        run / "launch-receipt.json",
        {
            "identifier": run.name,
            "started_at_unix": 1000.0,
            "pane_pid": os.getpid(),
            "pane_start_ticks": Path(f"/proc/{os.getpid()}/stat")
            .read_text()
            .split(") ", 1)[1]
            .split()[19],
            "runtime_deadline": None,
        },
    )
    _write_json(
        run / "activity.json",
        {
            "classification": "working",
            "observed_at_unix": 1001.0,
            "last_progress_at_unix": 1001.0,
            "quiet_seconds": 0,
            "process_alive": True,
            "runtime_deadline": None,
            "automatic_kill": False,
            "metrics": {
                "process_count": 3,
                "cpu_ticks": 42,
                "write_bytes": 99,
                "artifact_file_count": 2,
            },
        },
    )
    return runtime


def test_activity_projection_is_monitor_only_and_omits_attempt_token(tmp_path: Path):
    payload = list_agy_activity_runs(_fixture_runtime(tmp_path))
    assert payload["status"] == "ok"
    assert payload["runtime_deadline"] is None
    assert payload["automatic_kill"] is False
    assert payload["activity_counts"] == {"working": 1}
    run = payload["runs"][0]
    assert run["state"] == "running"
    assert run["activity"]["metrics"]["cpu_ticks"] == 42
    assert run["verification_status"] == "pending"
    assert "attempt_token" not in run
    assert "must-not-project" not in json.dumps(payload)


def test_stale_activity_does_not_claim_running_without_exact_pane(tmp_path: Path):
    runtime = _fixture_runtime(tmp_path)
    receipt_path = runtime / "agy-dashboard-test" / "launch-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["pane_pid"] = 999_999_999
    receipt["pane_start_ticks"] = "1"
    _write_json(receipt_path, receipt)
    projected = list_agy_activity_runs(runtime)["runs"][0]
    assert projected["state"] == "orphaned"
    assert projected["activity"]["classification"] == "stale_unverified"
    assert projected["activity"]["pane_identity_verified_alive"] is False


def test_direct_canonical_cli_run_is_also_projected(tmp_path: Path):
    runtime = _fixture_runtime(tmp_path)
    run_dir = runtime / "agy-dashboard-test"
    (run_dir / "harness-run.json").unlink()
    result = tmp_path / "spool" / "RESULT.md"
    plan = tmp_path / "spool" / "PLAN.md"
    _write_json(
        run_dir / "manifest.json",
        {
            "identifier": "agy-dashboard-test",
            "result_path": str(result),
            "plan_path": str(plan),
            "admission": {"event_id": "task-admission:direct", "attempt": 2},
        },
    )
    projected = list_agy_activity_runs(runtime)["runs"][0]
    assert projected["task_ref"] == "task-admission:direct"
    assert projected["event_id"] == "task-admission:direct"
    assert projected["attempt"] == 2


def test_activity_gateway_reads_configured_runtime(tmp_path: Path, monkeypatch):
    runtime = _fixture_runtime(tmp_path)
    monkeypatch.setenv("PRISMATIC_AGY_RUNTIME_DIR", str(runtime))
    response = TestClient(app).get("/api/gateway/agy/activity")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["run_id"] == "agy-dashboard-test"
    assert payload["runs"][0]["runtime_deadline"] is None


def test_dashboard_renders_canonical_agy_activity_surface():
    source = (ROOT / "prismatic/gateway/dashboard_src/tabs/dashboard.html").read_text()
    script = (ROOT / "prismatic/gateway/dashboard_src/scripts/dashboard.js").read_text()
    generated = (ROOT / "prismatic/gateway/templates/dashboard.html").read_text()
    for marker in (
        "agy-activity-panel",
        "AGY Exact-Run Activity",
        "No wall-clock cap · monitor only",
    ):
        assert marker in source
        assert marker in generated
    assert "loadCanonicalAgyActivity" in script
    assert "/api/gateway/agy/activity?limit=20" in script
    assert "no automatic runtime kill" in script
