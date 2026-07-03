from __future__ import annotations

import time

from fastapi.testclient import TestClient

from prismatic import plugin_health as plugin_health_module
from prismatic.gateway import server


class FakeLifecycleManager:
    def __init__(self, status):
        self._status = status

    def get_plugin_status(self, name: str):
        assert name == "demo-plugin"
        return dict(self._status)


def test_get_plugin_health_returns_not_found_without_lifecycle_or_metrics(monkeypatch):
    monkeypatch.setattr(plugin_health_module, "_plugin_metrics", lambda name: {})

    payload = plugin_health_module.get_plugin_health(
        "demo-plugin",
        lifecycle_manager=FakeLifecycleManager({"state": "NOT_FOUND", "name": "demo-plugin"}),
    )

    assert payload == {"status": "NOT_FOUND", "plugin_name": "demo-plugin"}


def test_get_plugin_health_maps_running_lifecycle_state(monkeypatch):
    monkeypatch.setattr(plugin_health_module, "_plugin_metrics", lambda name: {})
    started_at = time.time() - 12.4

    payload = plugin_health_module.get_plugin_health(
        "demo-plugin",
        lifecycle_manager=FakeLifecycleManager(
            {
                "state": "RUNNING",
                "container_id": "abc123",
                "runtime": "gvisor",
                "started_at": started_at,
                "last_error": "",
            }
        ),
    )

    assert payload["status"] == "healthy"
    assert payload["plugin_name"] == "demo-plugin"
    assert payload["state"] == "RUNNING"
    assert payload["container_id"] == "abc123"
    assert payload["runtime"] == "gvisor"
    assert payload["uptime_seconds"] >= 12.0
    assert payload["last_error"] == ""
    assert payload["metrics"]["total_starts"] == 0
    assert "timestamp" in payload


def test_get_plugin_health_maps_failed_state_to_unhealthy(monkeypatch):
    monkeypatch.setattr(plugin_health_module, "_plugin_metrics", lambda name: {})

    payload = plugin_health_module.get_plugin_health(
        "demo-plugin",
        lifecycle_manager=FakeLifecycleManager(
            {"state": "FAILED", "last_error": "pod crash", "started_at": 0}
        ),
    )

    assert payload["status"] == "unhealthy"
    assert payload["last_error"] == "pod crash"


def test_gateway_plugin_health_route_uses_http_status_mapping(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_plugin_health",
        lambda name: {"status": "unhealthy", "plugin_name": name, "state": "FAILED"},
    )

    client = TestClient(server.app)
    response = client.get("/api/v1/plugins/demo-plugin/health")

    assert response.status_code == 503
    assert response.json()["plugin_name"] == "demo-plugin"
    assert response.json()["state"] == "FAILED"


def test_gateway_plugin_health_route_returns_404_for_unknown(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_plugin_health",
        lambda name: {"status": "NOT_FOUND", "plugin_name": name},
    )

    client = TestClient(server.app)
    response = client.get("/api/v1/plugins/missing/health")

    assert response.status_code == 404
    assert response.json() == {"status": "NOT_FOUND", "plugin_name": "missing"}
