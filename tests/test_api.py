from fastapi.testclient import TestClient
import os
import pytest
from prismatic.api.main import app

client = TestClient(app)

# Use the default secret token for testing
VALID_TOKEN = "prismatic-secret-token"

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Prismatic Engine API Gateway is running"}

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_credits_unauthorized():
    response = client.get("/v1/credits")
    assert response.status_code == 401

def test_credits_authorized():
    # We might need to mock AIUltraCreditTracker if it touches DB
    response = client.get(
        "/v1/credits",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"}
    )
    assert response.status_code == 200
    assert "remaining_credits" in response.json()

def test_jobs_unauthorized():
    response = client.post("/v1/jobs", json={"agent": "fred", "issue_id": "GRO-123"})
    assert response.status_code == 401

def test_jobs_authorized():
    response = client.post(
        "/v1/jobs",
        json={"agent": "fred", "issue_id": "GRO-123", "title": "Test Job"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"

def test_jobs_invalid_agent():
    response = client.post(
        "/v1/jobs",
        json={"agent": "invalid-agent", "issue_id": "GRO-123"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"}
    )
    assert response.status_code == 400
    assert "Invalid agent" in response.json()["detail"]
