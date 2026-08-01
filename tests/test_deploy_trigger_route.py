"""HTTP Route Integration Tests for Deploy Trigger (/api/deploy/trigger)."""

from fastapi.testclient import TestClient

from prismatic.gateway.server import app

client = TestClient(app)


def test_deploy_trigger_dry_run_route():
    """W5 / Slice 4: Assert POST /api/deploy/trigger?dry_run=true returns 200 with record."""
    response = client.post(
        "/api/deploy/trigger?pr_sha=a1b2c3d4e5f6&pr_title=Test+PR&dry_run=true"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "record" in data
    assert data["record"]["pr_sha"] == "a1b2c3d4e5f6"
    assert data["record"]["success"] is True


def test_deploy_cancel_route():
    """P5 / Slice 1: Assert POST /api/deploy/cancel returns cancel_requested status."""
    response = client.post("/api/deploy/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cancel_requested"


def test_deploy_diff_route():
    """P6 / Slice 5: Assert GET /api/deploy/diff returns diff structure."""
    response = client.get("/api/deploy/diff?from_sha=HEAD~1&to_sha=HEAD")
    assert response.status_code == 200
    data = response.json()
    assert "from_sha" in data
    assert "to_sha" in data
    assert "stat" in data
    assert "patch" in data
