from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.pwp_integration import (
    PWP_PLUGIN_ID,
    PWPIntegrationStore,
    connect_pwp,
    disconnect_pwp,
    integration_status,
    run_pwp_reference_lifecycle,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _isolate_lifecycle_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(
        "PRISMATIC_PWP_INTEGRATION_STATE", str(tmp_path / "pwp_state.json")
    )
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_JOBS_STATE", str(tmp_path / "plugin_jobs.json")
    )
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE", str(tmp_path / "plugin_artifacts.json")
    )


def test_pwp_integration_status_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "PRISMATIC_PWP_INTEGRATION_STATE", str(tmp_path / "pwp_state.json")
    )
    payload = integration_status()

    assert payload["plugin_id"] == PWP_PLUGIN_ID
    assert payload["manifest"]["exists"] is True
    assert payload["manifest"]["name"] == PWP_PLUGIN_ID
    assert payload["manifest"]["version"] == "1.2.0"
    assert payload["status"] == "disconnected"
    assert len(payload["capabilities"]) >= 3
    assert "pwp_credentials_refresh" in payload["tool_names"]
    assert any("PE dashboard" in item for item in payload["connect_points"])
    assert any("disconnect" in item.lower() for item in payload["disconnect_points"])
    assert not [
        b for b in payload["production_blockers"] if b["severity"] == "blocking"
    ]


def test_pwp_connect_disconnect_state_is_durable(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "pwp_state.json"
    monkeypatch.setenv("PRISMATIC_PWP_INTEGRATION_STATE", str(state_path))
    store = PWPIntegrationStore(state_path)

    connected = connect_pwp(store)
    assert connected["connected"] is True
    assert connected["state"] == "connected"
    assert state_path.exists()
    raw = json.loads(state_path.read_text())
    assert raw["acknowledged_contract_version"] == "1.2.0"

    disconnected = disconnect_pwp(store)
    assert disconnected["connected"] is False
    assert disconnected["state"] == "disconnected"
    assert json.loads(state_path.read_text())["disconnected_at"]


def test_pwp_gateway_endpoints(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "PRISMATIC_PWP_INTEGRATION_STATE", str(tmp_path / "pwp_state.json")
    )
    client = TestClient(server.app)

    status = client.get("/api/pwp/status")
    assert status.status_code == 200
    assert status.json()["plugin_id"] == PWP_PLUGIN_ID

    connect = client.post("/api/pwp/connect")
    assert connect.status_code == 200
    assert connect.json()["connected"] is True

    disconnect = client.post("/api/pwp/disconnect")
    assert disconnect.status_code == 200
    assert disconnect.json()["state"] == "disconnected"

    refresh = client.post("/api/pwp/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["connection"]["last_action"] == "refresh"


def test_pwp_cli_integration_status_uses_repo_local_state(
    monkeypatch, tmp_path: Path
) -> None:
    env = {
        **os.environ,
        "PRISMATIC_PWP_INTEGRATION_STATE": str(tmp_path / "pwp_state.json"),
    }
    result = subprocess.run(
        [sys.executable, "scripts/pwp", "integration", "status"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["plugin_id"] == PWP_PLUGIN_ID
    assert "pwp theme validate" in payload["tool_names"]
    assert payload["state_path"] == str(tmp_path / "pwp_state.json")


def test_pwp_plugin_manifest_and_plugin_contract() -> None:
    manifest = (
        REPO_ROOT / "prismatic" / "shipped_plugins" / "pwp" / "plugin-manifest.yaml"
    ).read_text()
    assert "connect_points:" in manifest
    assert "disconnect_points:" in manifest
    assert "pwp.visual-governance" in manifest

    from prismatic.shipped_plugins.pwp.plugin import PWPDesignTokenPlugin

    plugin = PWPDesignTokenPlugin()
    contract = plugin.capability_contract()
    assert contract["plugin_id"] == PWP_PLUGIN_ID
    assert "credential provider refresh/status" in contract["capabilities"]
    assert plugin.connection_contract()["disconnect_points"]


def test_pwp_full_lifecycle_reference_demo(monkeypatch, tmp_path: Path) -> None:
    _isolate_lifecycle_state(monkeypatch, tmp_path)

    payload = run_pwp_reference_lifecycle(actor="pytest")

    assert payload["ok"] is True
    assert payload["plugin_id"] == PWP_PLUGIN_ID
    assert payload["connection"]["state"] == "disconnected"
    assert payload["job"]["status"] == "completed"
    assert payload["artifact"]["approval_state"] == "approved"
    assert payload["artifact"]["publish_state"] == "publish_ready"
    assert payload["artifact"]["provenance"]["source_plugin"] == PWP_PLUGIN_ID
    assert payload["artifact"]["sha256"]
    assert any(
        step["step"] == "approval_before_publish" and step["blocked_before_approval"]
        for step in payload["steps"]
    )
    assert any(
        step["step"] == "safe_disconnect" and step["artifacts_preserved"]
        for step in payload["steps"]
    )

    status = integration_status()
    lifecycle = status["lifecycle_summary"]
    assert lifecycle["reference_plugin"] is True
    assert lifecycle["jobs_total"] == 1
    assert lifecycle["artifacts_total"] == 1
    assert lifecycle["latest_job"]["job_id"] == payload["job_id"]
    assert lifecycle["latest_artifact"]["artifact_id"] == payload["artifact_id"]


def test_pwp_lifecycle_gateway_endpoint(monkeypatch, tmp_path: Path) -> None:
    _isolate_lifecycle_state(monkeypatch, tmp_path)
    client = TestClient(server.app)

    response = client.post("/api/pwp/lifecycle-demo", json={"actor": "pytest-gateway"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["artifact"]["publish_state"] == "publish_ready"

    status = client.get("/api/pwp/status")
    assert status.status_code == 200
    lifecycle = status.json()["lifecycle_summary"]
    assert lifecycle["jobs_total"] == 1
    assert lifecycle["artifacts_total"] == 1


def test_pwp_lifecycle_cli_demo(monkeypatch, tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PRISMATIC_STATE_DIR": str(tmp_path / "state"),
        "PRISMATIC_PWP_INTEGRATION_STATE": str(tmp_path / "pwp_state.json"),
        "PRISMATIC_PLUGIN_JOBS_STATE": str(tmp_path / "plugin_jobs.json"),
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE": str(tmp_path / "plugin_artifacts.json"),
    }
    result = subprocess.run(
        [sys.executable, "scripts/pwp", "lifecycle", "demo"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["connection"]["state"] == "disconnected"
    assert payload["artifact"]["publish_state"] == "publish_ready"


def test_dashboard_contains_pwp_operator_surface() -> None:
    dashboard = (
        REPO_ROOT / "prismatic" / "gateway" / "templates" / "dashboard.html"
    ).read_text()
    assert "tab-btn-pwp" in dashboard
    assert "section-pwp" in dashboard
    assert "loadPWPStatus" in dashboard
    assert "pwpAction('connect')" in dashboard
    assert "pwpAction('disconnect')" in dashboard
    assert "/api/pwp/status" in dashboard
    assert "pwpAction('lifecycle-demo')" in dashboard
    assert "pwp-lifecycle-history" in dashboard
    assert "renderPWPLifecycleHistory" in dashboard
    assert "approval before publish" in dashboard
