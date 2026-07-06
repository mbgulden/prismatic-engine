"""Regression tests for gateway system-mode change notifications."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.gateway.event_bus import EventBus, set_event_bus


def _reset_gateway_state(tmp_path, monkeypatch) -> EventBus:
    mode_file = tmp_path / "system_mode.json"
    monkeypatch.setenv("PRISMATIC_SYSTEM_MODE_FILE", str(mode_file))
    server._ws_clients.clear()
    bus = EventBus()
    set_event_bus(bus)
    return bus


def test_set_mode_persists_and_publishes_bus_event(tmp_path, monkeypatch):
    bus = _reset_gateway_state(tmp_path, monkeypatch)
    client = TestClient(server.app)

    response = client.post("/api/mode", json={"mode": "interactive"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "interactive"
    assert body["previous_mode"] == "autonomous"
    assert body["event"]["type"] == "system.mode_changed"
    assert body["event"]["payload"] == {
        "mode": "interactive",
        "previous_mode": "autonomous",
    }
    assert bus.get_history(limit=1)[0]["type"] == "system.mode_changed"
    assert json.loads((tmp_path / "system_mode.json").read_text())["mode"] == "interactive"


def test_mode_change_reaches_fastapi_websocket_clients(tmp_path, monkeypatch):
    _reset_gateway_state(tmp_path, monkeypatch)
    client = TestClient(server.app)

    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_json()["type"] == "connected"

        response = client.post("/api/mode", json={"mode": "collaborative"})
        assert response.status_code == 200
        assert response.json()["websocket_clients"] == 1

        message = websocket.receive_json()
        assert message["type"] == "system.mode_changed"
        assert message["source"] == "gateway"
        assert message["payload"] == {
            "mode": "collaborative",
            "previous_mode": "autonomous",
        }


def test_ipc_bridge_accepts_system_mode_changed_event():
    from prismatic.gateway.ipc_bridge import validate_event

    ok, reason = validate_event(
        {
            "type": "system.mode_changed",
            "source": "gateway",
            "payload": {"mode": "autonomous"},
        }
    )

    assert ok is True
    assert reason == ""
