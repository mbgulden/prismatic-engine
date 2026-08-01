from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic import recovery_runtime
from prismatic.gateway import server


def result(code: int, stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["systemctl", "is-active", "prismatic-consumer.service"],
        returncode=code,
        stdout=stdout,
        stderr="",
    )


def test_consumer_runtime_reports_active_without_fabricating_missing_evidence(tmp_path):
    payload = recovery_runtime.consumer_runtime_status(
        heartbeat_path=tmp_path / "missing.json",
        runner=lambda command: result(0, "active\n"),
        now=1000,
    )

    assert payload["service_name"] == "prismatic-consumer.service"
    assert payload["systemd_available"] is True
    assert payload["systemd_active"] is True
    assert payload["systemd_state"] == "active"
    assert payload["heartbeat"] == {
        "available": False,
        "exists": False,
        "status": "unavailable",
        "fresh": None,
        "age_seconds": None,
        "source": "durable_heartbeat_not_present",
    }
    assert payload["pool_stats"]["available"] is False
    assert payload["pool_stats"]["live_count"] is None
    assert "path" not in json.dumps(payload).lower()


def test_consumer_runtime_distinguishes_offline_from_unavailable_systemd(tmp_path):
    offline = recovery_runtime.consumer_runtime_status(
        heartbeat_path=tmp_path / "missing.json",
        runner=lambda command: result(3, "inactive\n"),
    )
    assert offline["systemd_available"] is True
    assert offline["systemd_active"] is False
    assert offline["systemd_state"] == "inactive"

    def unavailable(command):
        raise OSError("synthetic local detail must not escape")

    unknown = recovery_runtime.consumer_runtime_status(
        heartbeat_path=tmp_path / "missing.json", runner=unavailable
    )
    assert unknown["systemd_available"] is False
    assert unknown["systemd_active"] is False
    assert unknown["systemd_state"] == "unknown"
    assert "synthetic local detail" not in json.dumps(unknown)


def test_consumer_runtime_reports_fresh_stale_and_unreadable_heartbeat(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"timestamp": 950}), encoding="utf-8")
    fresh = recovery_runtime.consumer_runtime_status(
        heartbeat_path=path,
        runner=lambda command: result(0, "active\n"),
        now=1000,
    )
    assert fresh["heartbeat"]["status"] == "fresh"
    assert fresh["heartbeat"]["age_seconds"] == 50
    assert fresh["heartbeat"]["fresh"] is True

    stale = recovery_runtime.consumer_runtime_status(
        heartbeat_path=path,
        runner=lambda command: result(0, "active\n"),
        now=1200,
    )
    assert stale["heartbeat"]["status"] == "stale"
    assert stale["heartbeat"]["fresh"] is False

    path.write_text("not-json local-secret-detail", encoding="utf-8")
    unreadable = recovery_runtime.consumer_runtime_status(
        heartbeat_path=path,
        runner=lambda command: result(0, "active\n"),
    )
    assert unreadable["heartbeat"]["status"] == "unreadable"
    assert "local-secret-detail" not in json.dumps(unreadable)


def test_recovery_api_merges_runtime_truth(monkeypatch):
    monkeypatch.setattr(server, "_read_dashboard_recovery_state", dict)
    monkeypatch.setattr(server, "_run_records_for_dashboard", list)
    monkeypatch.setattr(
        recovery_runtime,
        "consumer_runtime_status",
        lambda: {
            "runtime_source": "test_systemd_read_only",
            "service_name": "prismatic-consumer.service",
            "systemd_available": True,
            "systemd_active": True,
            "systemd_state": "active",
            "heartbeat": {"available": False, "exists": False, "status": "unavailable"},
            "pool_stats": {
                "available": False,
                "live_count": None,
                "total_skipped_dlq": None,
            },
        },
    )

    response = TestClient(server.app).get("/api/recovery/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["systemd_active"] is True
    assert payload["heartbeat"]["status"] == "unavailable"
    assert payload["pool_stats"]["live_count"] is None
    assert payload["failure_taxonomy"]


def test_dashboard_source_labels_unavailable_evidence_without_zero_fabrication():
    source = Path("prismatic/gateway/dashboard_src/scripts/dashboard.js").read_text(
        encoding="utf-8"
    )
    assert 'const serviceState = !systemdAvailable ? "UNAVAILABLE"' in source
    assert 'heartbeat.available === false ? "unavailable"' in source
    assert 'const liveCount = poolAvailable ? (pool.live_count ?? "—") : "—"' in source
    assert "No durable heartbeat producer configured" in source
    assert "durable pool snapshot unavailable" in source
