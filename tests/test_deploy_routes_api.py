"""End-to-end API route tests for Deploy Hook V1 (/api/deploy/*) (PR #418)."""

from fastapi.testclient import TestClient
from prismatic.gateway.server import app


def test_deploy_recent_endpoint():
    client = TestClient(app)
    res = client.get("/api/deploy/recent")
    assert res.status_code == 200
    data = res.json()
    assert "count" in data
    assert "deploys" in data


def test_deploy_trigger_manual_dry_run():
    client = TestClient(app)
    res = client.post(
        "/api/deploy/trigger?pr_sha=1234567890123456789012345678901234567890&pr_title=Test_PR&dry_run=true"
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "record" in data
    assert data["record"]["pr_sha"] == "1234567890123456789012345678901234567890"

    # Verify recent deploys now contains the record
    res_recent = client.get("/api/deploy/recent")
    assert res_recent.status_code == 200
    assert res_recent.json()["count"] >= 1

    # Verify latest deploy returns the record
    res_latest = client.get("/api/deploy/latest")
    assert res_latest.status_code == 200
    assert (
        res_latest.json()["deploy"]["pr_sha"]
        == "1234567890123456789012345678901234567890"
    )
