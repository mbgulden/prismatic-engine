from __future__ import annotations

import importlib
from pathlib import Path


def test_agent_signal_stream_records_groups_and_redacts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    import prismatic.agent_signal_stream as signals

    signals = importlib.reload(signals)
    log = tmp_path / "fred.log"
    log.write_text(
        "hello from fred\napi_key=should_not_show\nRESULT=PASS\n", encoding="utf-8"
    )

    signals.record_agent_signal(
        agent="fred",
        event_type="WAKE_DISPATCHED",
        issue_id="GRO-SIGNAL",
        status="dispatched",
        message="token=should_not_show visible work started",
        run_id="assigned-fred-signal",
        log_path=str(log),
    )
    payload = signals.list_agent_signals(limit=20)

    assert payload["count"] == 1
    assert payload["counts"]["fred"] == 1
    item = payload["by_agent"]["fred"][0]
    assert item["event_type"] == "WAKE_DISPATCHED"
    assert item["issue_id"] == "GRO-SIGNAL"
    assert "[REDACTED]" in item["message"]
    assert "should_not_show" not in item["message"]
    assert "RESULT=PASS" in item["transcript"]


def test_dashboard_signals_has_mobile_tabs_and_agent_panes():
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(
        encoding="utf-8"
    )
    assert 'data-proof-marker="assigned-agent-signals-dashboard"' in html
    assert 'data-proof-marker="signals-mobile-agent-tabs"' in html
    assert 'id="signals-agent-panes"' in html
    assert "setSignalsAgent(" in html
    assert "/api/gateway/signals?limit=200" in html
    for agent in ["kai", "fred", "agy", "george"]:
        assert f'data-agent-tab="{agent}"' in html


def test_gateway_signals_api_uses_durable_stream(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    import prismatic.agent_signal_stream as signals

    signals = importlib.reload(signals)
    signals.record_agent_signal(
        agent="kai", event_type="WORK_RESULT_PACKET", issue_id="GRO-API", message="done"
    )

    from fastapi.testclient import TestClient
    from prismatic.gateway.server import app

    response = TestClient(app).get("/api/gateway/signals?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "prismatic.agent_signal_stream"
    assert data["counts"]["kai"] == 1
    assert data["by_agent"]["kai"][0]["issue_id"] == "GRO-API"
