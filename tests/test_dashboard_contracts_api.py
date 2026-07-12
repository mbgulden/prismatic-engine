import json
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.dashboard_contracts import (
    CONTRACTS,
    check_dashboard_html,
    classify_public_dashboard,
    dashboard_contract_manifest,
    verify_source_contract,
)


def test_dashboard_contract_manifest_shape() -> None:
    manifest = dashboard_contract_manifest()
    assert manifest["source"] == "prismatic.dashboard_contracts"
    assert manifest["dashboard_route"] == "/dashboard"
    assert {section["id"] for section in manifest["sections"]} >= {
        "agents",
        "queue",
        "timeline",
        "workspaces",
        "skills",
        "foundation",
        "merge",
        "quota",
        "dispatcher_recovery",
    }
    assert "GET /api/gateway/agents/status" in manifest["required_endpoints"]
    assert "GET /api/gateway/webhooks/queue/detail" in manifest["required_endpoints"]
    assert any(control["timeline_source"] == "QueueControl" for control in manifest["safe_controls"])


def test_gateway_dashboard_contracts_endpoint() -> None:
    from prismatic.gateway.server import app

    client = TestClient(app)
    response = client.get("/api/gateway/dashboard/contracts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "prismatic.dashboard_contracts"
    assert payload["dashboard_route"] == "/dashboard"
    assert payload["sections"]


def test_dashboard_html_contract_checks_live_wiring() -> None:
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(encoding="utf-8")
    result = check_dashboard_html(html, section="agents,queue")
    assert result["ok"], result["failures"]
    assert result["sections_checked"] == ["agents", "queue"]


def test_dashboard_html_contract_rejects_scoped_mock_fallback() -> None:
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(encoding="utf-8") + "\nmockQueue\n"
    result = check_dashboard_html(html, section="queue")
    assert result["ok"] is False
    assert any(item.get("forbidden_pattern") == "mockQueue" for item in result["failures"])


def test_source_contract_and_node_check_pass() -> None:
    result = verify_source_contract(section="agents,queue")
    assert result["ok"], result
    assert result["node_check"]["ok"] is True


def test_contracts_do_not_require_heavy_imports() -> None:
    assert "queue" in CONTRACTS
    assert "agents" in CONTRACTS


def test_public_url_classifier_with_local_dashboard(monkeypatch) -> None:
    def fake_urlopen(url, timeout=20):
        class Response:
            status = 200
            def geturl(self):
                return url
            def read(self):
                return b"<html>Prismatic Engine<script>const API_PREFIX='/api/gateway';</script></html>"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = classify_public_dashboard("https://example.test")
    assert result["status"] == "reachable"


def test_manifest_json_serializable() -> None:
    json.dumps(dashboard_contract_manifest())
