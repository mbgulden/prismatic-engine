"""Integration tests for the Prismatic API Gateway dispatch endpoint.

Run:
    cd prismatic-engine && python -m pytest tests/api/test_dispatch_gateway.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

# Point auth/telemetry at deterministic test-only values BEFORE importing the app
AUTH_VALUE = "unit-test-key"
os.environ.setdefault("PRISMATIC_API_KEY", AUTH_VALUE)
os.environ.setdefault("PRISMATIC_STATE_DIR", "/tmp/prismatic-api-test-state")
os.makedirs(os.environ["PRISMATIC_STATE_DIR"], exist_ok=True)

from prismatic.api import auth as auth_module
from prismatic.api.server import app

client = TestClient(app)

INVALID_AUTH = "not-the-test-key"


# ── Health Endpoint ───────────────────────────────────────


def test_health_endpoint():
    """GET /api/v1/health returns 200 with status ok."""
    response = client.get(
        "/api/v1/health",
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
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
        headers={"Authorization": f"Bearer {INVALID_AUTH}"},
    )
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]


def test_auth_fails_closed_without_configured_keys(monkeypatch):
    """No configured key means no known fallback token unless explicitly enabled."""
    monkeypatch.delenv("PRISMATIC_API_KEY", raising=False)
    monkeypatch.delenv("PRISMATIC_API_KEYS", raising=False)
    monkeypatch.delenv("PRISMATIC_API_ALLOW_DEV_TOKEN", raising=False)

    assert auth_module._load_api_keys() == {}


def test_auth_loads_keys_dynamically_after_import(monkeypatch):
    """Keys added after module import are honored by request-time loading."""
    monkeypatch.setenv("PRISMATIC_API_KEY", "late-bound-key")

    assert auth_module._load_api_keys() == {"late-bound-key": ["admin"]}


# ── Valid Dispatch ────────────────────────────────────────


@patch("prismatic.api.routers.jobs.AGENT_LAUNCHERS", {"fred": lambda *a, **kw: None})
def test_valid_dispatch():
    """POST /api/v1/jobs with valid token and correct agent → 201 + job_id."""
    response = client.post(
        "/api/v1/jobs",
        json={"agent": "fred", "title": "Test job", "description": "Do the thing"},
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
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
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
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
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
    )
    assert response.status_code == 422
    assert "Invalid agent" in response.json()["detail"]


@patch("prismatic.api.routers.jobs.AGENT_LAUNCHERS", {"future_internal": lambda *a, **kw: None})
def test_future_internal_launcher_not_public_by_default():
    """New internal launchers are not remotely dispatchable by default."""
    response = client.post(
        "/api/v1/jobs",
        json={"agent": "future_internal", "title": "Nope"},
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
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
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
    )
    job_id = post.json()["job_id"]

    response = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] in ("queued", "running", "completed", "failed")


def test_job_not_found():
    """GET /api/v1/jobs/<nonexistent> → 404."""
    response = client.get(
        "/api/v1/jobs/nonexistent-id",
        headers={"Authorization": f"Bearer {AUTH_VALUE}"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
