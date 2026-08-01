"""Unit tests for Review Factory REST API routes (prismatic/review_factory/routes.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prismatic.core.merge_factory import Principal
from prismatic.review_factory.routes import get_rf_principal
from prismatic.review_factory.routes import router as review_factory_router


@pytest.fixture
def app_client(tmp_path):
    app = FastAPI()
    app.include_router(review_factory_router, prefix="/api/merge-factory")
    app.dependency_overrides[get_rf_principal] = lambda: Principal(
        identity="test-actor", scopes=["merge-factory-admin", "merge-judge"]
    )
    client = TestClient(app)
    return client


def test_review_factory_health_endpoint(app_client):
    response = app_client.get("/api/merge-factory/review/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_list_review_jobs_endpoint(app_client):
    response = app_client.get("/api/merge-factory/review/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    assert isinstance(data["jobs"], list)


def test_get_nonexistent_job_returns_404(app_client):
    response = app_client.get("/api/merge-factory/review/job/nonexistent-id-12345")
    assert response.status_code == 404


def test_rf_dashboard_selectors_present():
    from pathlib import Path

    dashboard_path = Path("prismatic/gateway/templates/dashboard.html")
    assert dashboard_path.exists()
    content = dashboard_path.read_text(encoding="utf-8")

    assert 'id="section-review-factory"' in content
    assert 'id="rf-jobs-table"' in content
    assert 'id="rf-job-modal"' in content


def test_gateway_workspace_tree_route():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from prismatic.gateway.server import gateway_workspace_tree

    app = FastAPI()
    app.get("/api/workspace/tree")(gateway_workspace_tree)
    client = TestClient(app)
    res = client.get("/api/workspace/tree")
    assert res.status_code == 200
    assert "workspaces" in res.json()


def test_gateway_deploy_status_route():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from prismatic.gateway.server import gateway_deploy_status

    app = FastAPI()
    app.get("/api/deploy/status")(gateway_deploy_status)
    client = TestClient(app)
    res = client.get("/api/deploy/status")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
