from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def test_overnight_report_writes_json_and_html(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    report_dir = tmp_path / "reports"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("PRISMATIC_RUN_RECORDS", str(state_dir / "run_records.json"))

    from prismatic.run_records import AgentRunRecordStore
    from prismatic.reports.overnight import generate_report, write_report

    store = AgentRunRecordStore(store_path=str(state_dir / "run_records.json"))
    run_id = store.create_run("GRO-3301", "kai")
    artifact = tmp_path / "artifact.md"
    artifact.write_text("Deployed https://activeoahu.example/tours/new via PR #47")
    assert store.update_run(run_id, "completed", output_path=str(artifact))

    report = generate_report(hours=24, report_dir=report_dir)
    json_path, html_path = write_report(report, report_dir=report_dir)

    data = json.loads(json_path.read_text())
    assert data["tasks_processed"] == 1
    assert data["tasks_autonomous"] == 1
    assert data["deliverables"][0]["url"] == "https://activeoahu.example/tours/new"
    assert "Overnight Factory Briefing" in html_path.read_text()


def test_latest_report_gateway_route(tmp_path, monkeypatch):
    home = tmp_path / "home"
    report_dir = home / ".prismatic" / "reports"
    report_dir.mkdir(parents=True)
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tasks_processed": 2,
    }
    (report_dir / "latest.json").write_text(json.dumps(report_data))
    monkeypatch.setenv("HOME", str(home))

    from prismatic.gateway.server import app

    client = TestClient(app)
    res1 = client.get("/api/report/latest")
    res2 = client.get("/api/gateway/overnight-report/latest")
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.json() == res2.json() == report_data


def test_latest_report_gateway_route_missing_404(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".prismatic" / "reports").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    from prismatic.gateway.server import app

    client = TestClient(app)
    res1 = client.get("/api/report/latest")
    res2 = client.get("/api/gateway/overnight-report/latest")
    assert res1.status_code == 404
    assert res2.status_code == 404


def test_latest_report_gateway_route_invalid_json_500(tmp_path, monkeypatch):
    home = tmp_path / "home"
    report_dir = home / ".prismatic" / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest.json").write_text("{invalid json")
    monkeypatch.setenv("HOME", str(home))

    from prismatic.gateway.server import app

    client = TestClient(app)
    res1 = client.get("/api/report/latest")
    res2 = client.get("/api/gateway/overnight-report/latest")
    assert res1.status_code == 500
    assert res2.status_code == 500
    assert res1.json() == res2.json()
