"""Integration tests for the Prismatic API Gateway dispatch endpoint.

Run:
    cd prismatic-engine && python -m pytest tests/api/test_dispatch_gateway.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Point auth at a known test key BEFORE importing the app
os.environ.setdefault("PRISMATIC_API_KEY", "test-api-key-12345")

from prismatic.api.server import app  # noqa: E402

client = TestClient(app)

VALID_TOKEN = "test-api-key-12345"
INVALID_TOKEN = "bad-token-00000"


# ── Health Endpoint ───────────────────────────────────────


def test_health_endpoint():
    """GET /api/v1/health returns 200 with status ok."""
    response = client.get(
        "/api/v1/health",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "timestamp" in data


# ── Auth: Missing / Invalid Token ─────────────────────────


def test_missing_token():
    """No auth header → 401."""
    response = client.get("/api/v1/health")
    assert response.status_code == 401
    assert "Missing" in response.json()["detail"]


def test_invalid_token():
    """Bad Bearer token → 401."""
    response = client.get(
        "/api/v1/health",
        headers={"Authorization": f"Bearer {INVALID_TOKEN}"},
    )
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]


# ── Valid Dispatch ────────────────────────────────────────


@patch("prismatic.api.routers.jobs.AGENT_LAUNCHERS", {"fred": lambda *a, **kw: None})
def test_valid_dispatch():
    """POST /api/v1/jobs with valid token and correct agent → 201 + job_id."""
    response = client.post(
        "/api/v1/jobs",
        json={"agent": "fred", "title": "Test job", "description": "Do the thing"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "queued"
    assert data["agent"] == "fred"
    assert "job_id" in data


# ── Credit Check ──────────────────────────────────────────


def test_credit_check():
    """GET /api/v1/credits returns policy info."""
    response = client.get(
        "/api/v1/credits",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "policy_action" in data
    assert "remaining_budget" in data


# ── Invalid Parameters ────────────────────────────────────


def test_invalid_agent():
    """POST with non-existent agent → 422."""
    response = client.post(
        "/api/v1/jobs",
        json={"agent": "ghost-agent"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 422
    assert "Invalid agent" in response.json()["detail"]


# ── Job Status Lookup ─────────────────────────────────────


@patch("prismatic.api.routers.jobs.AGENT_LAUNCHERS", {"fred": lambda *a, **kw: None})
def test_job_status_lookup():
    """POST a job, then GET /api/v1/jobs/{job_id} returns its status."""
    post = client.post(
        "/api/v1/jobs",
        json={"agent": "fred", "title": "Lookup test"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    job_id = post.json()["job_id"]

    response = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] in ("queued", "running", "completed", "failed")


def test_job_not_found():
    """GET /api/v1/jobs/<nonexistent> → 404."""
    response = client.get(
        "/api/v1/jobs/nonexistent-id",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
