"""Unit tests for Review Factory REST API routes (prismatic/review_factory/routes.py)."""

from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest

from prismatic.review_factory.routes import (
    router as review_factory_router,
    get_rf_principal,
)
from prismatic.core.merge_factory import Principal


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
