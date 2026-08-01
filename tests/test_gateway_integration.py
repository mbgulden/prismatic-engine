"""Gateway Server integration tests for Workspace & Deploy Routers.
"""

from fastapi.testclient import TestClient
from prismatic.gateway.server import app


def test_gateway_workspace_and_deploy_endpoints():
    client = TestClient(app)

    r1 = client.get("/api/workspace/tree")
    assert r1.status_code == 200, f"Workspace tree endpoint returned {r1.status_code}"
    data1 = r1.json()
    assert "by_category" in data1

    r2 = client.get("/api/deploy/recent")
    assert r2.status_code == 200, f"Deploy recent endpoint returned {r2.status_code}"
    data2 = r2.json()
    assert "deploys" in data2
