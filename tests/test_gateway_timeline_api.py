import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.timeline import list_timeline, record_timeline_item, timeline_summary


def test_timeline_record_persists_and_lists_manual_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    item = record_timeline_item(source="Hermes", severity="info", title="Test event", message="Recorded from test", entity_id="test-1", metadata={"ok": True})
    payload = list_timeline(limit=10)
    assert item["id"]
    assert payload["source"] == "prismatic.timeline"
    assert payload["items"][0]["title"] == "Test event"
    assert payload["items"][0]["metadata"]["ok"] is True
    assert (tmp_path / "state" / "timeline_manual_events.json").exists()


def test_timeline_summary_counts_kinds_and_severity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    record_timeline_item(source="Hermes", severity="success", title="Green event")
    record_timeline_item(source="Hermes", severity="error", title="Red event")
    summary = timeline_summary(limit=20)
    assert summary["source"] == "prismatic.timeline"
    assert summary["by_kind"]["manual"] >= 2
    assert summary["by_severity"]["success"] >= 1
    assert summary["by_severity"]["error"] >= 1


def test_gateway_timeline_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway.server import app
    client = TestClient(app)

    assert client.get("/api/timeline").status_code == 200
    summary = client.get("/api/timeline/summary")
    assert summary.status_code == 200
    assert "by_kind" in summary.json()
    assert "by_severity" in summary.json()

    record = client.post("/api/timeline/record", json={"source": "Hermes", "severity": "info", "title": "API event", "message": "Recorded via API"})
    assert record.status_code == 200
    assert record.json()["ok"] is True
    followup = client.get("/api/timeline?kind=manual")
    assert any(item["title"] == "API event" for item in followup.json()["items"])
    assert client.post("/api/timeline/record", json={"title": ""}).status_code == 400


def test_dashboard_timeline_html_is_live_api_backed() -> None:
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(encoding="utf-8")
    assert "mockSignals" not in html
    assert "/api/timeline" in html
    assert "dashboard-activity" in html
    assert "signals-log-box" in html


def test_timeline_cli_works_in_checkout(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PRISMATIC_STATE_DIR"] = str(tmp_path / "state")
    record = subprocess.run([sys.executable, "-m", "prismatic.timeline", "record", "--source", "Hermes", "--severity", "success", "--title", "CLI event"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    assert json.loads(record.stdout)["ok"] is True
    listed = subprocess.run([sys.executable, "-m", "prismatic.timeline", "list", "--limit", "5"], cwd=Path.cwd(), env=env, text=True, capture_output=True, check=True)
    assert any(item["title"] == "CLI event" for item in json.loads(listed.stdout)["items"])
