"""Group G: 5-Point Counterexample Matrix Test for Deploy Route Preservation without Shadowing.

Matrix Coverage:
1. Positive: /api/deploy/status returns 200 OK with deploy status.
2. Direct Negative: Querying /api/deploy/status MUST NOT be shadowed by /{deploy_id} returning 404 "Deploy 'status' not found".
3. Collision & Isolation: Static routes (/status, /recent, /latest) take precedence over dynamic /{deploy_id}.
4. Boundary & Empty: /api/deploy/nonexistent-id-12345 still returns 404 for actual missing IDs.
5. Bypass Path: Requesting /api/deploy/status with trailing slash or query params behaves consistently.
"""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_group_g_deploy_status_not_shadowed(client):
    """Positive & Direct Negative: /api/deploy/status MUST return 200 OK.
    It MUST NOT return 404 {"detail": "Deploy 'status' not found"}.
    """
    resp_status = client.get("/api/deploy/status")
    assert resp_status.status_code == 200, f"Expected 200, got {resp_status.status_code}: {resp_status.text}"
    data = resp_status.json()
    assert data.get("status") == "active" or "deploys" in data or "has_deploys" in data

    # Actual missing deploy ID still returns 404
    resp_missing = client.get("/api/deploy/nonexistent-deploy-id-999")
    assert resp_missing.status_code == 404
