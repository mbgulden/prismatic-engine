"""Tests for the external Task Intake API (GRO-548).

Run:
    cd prismatic-engine && python -m pytest tests/api/test_intake.py -v
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Point auth at a known test key BEFORE importing the app
os.environ.setdefault("PRISMATIC_API_KEY", "test-api-key-12345")

from prismatic.api.server import app  # noqa: E402

client = TestClient(app)

VALID_TOKEN = "test-api-key-12345"
AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}


# ── Auth gate ───────────────────────────────────────────────


def test_submit_requires_auth():
    response = client.post(
        "/api/v1/intake",
        json={"source": "zapier", "payload": {}},
    )
    assert response.status_code == 401


def test_submit_rejects_invalid_token():
    response = client.post(
        "/api/v1/intake",
        json={"source": "zapier", "payload": {}},
        headers={"Authorization": "Bearer not-the-real-key"},
    )
    assert response.status_code == 401


# ── Validation ──────────────────────────────────────────────


def test_submit_rejects_missing_source():
    response = client.post(
        "/api/v1/intake",
        json={"payload": {"x": 1}},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_submit_rejects_priority_out_of_range():
    response = client.post(
        "/api/v1/intake",
        json={"source": "zapier", "priority": 9, "payload": {}},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_submit_strips_and_lowercases_tags():
    response = client.post(
        "/api/v1/intake",
        json={
            "source": "zapier",
            "payload": {"x": 1},
            "tags": ["  URGENT  ", "Follow-Up", ""],
        },
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["tags"] == ["urgent", "follow-up"]


def test_submit_rejects_too_many_tags():
    response = client.post(
        "/api/v1/intake",
        json={
            "source": "zapier",
            "payload": {},
            "tags": [f"t{i}" for i in range(20)],
        },
        headers=AUTH,
    )
    assert response.status_code == 422


# ── Enrichment ──────────────────────────────────────────────


def test_submit_enriches_with_metadata():
    response = client.post(
        "/api/v1/intake",
        json={
            "source": "cf-webhook",
            "title": "Form submission",
            "payload": {"email": "a@b.com", "plan": "pro"},
        },
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "cf-webhook"
    assert body["title"] == "Form submission"
    assert body["status"] == "queued"
    assert body["priority"] == 3  # default
    assert body["task_id"]  # uuid populated
    assert body["payload_hash"]  # sha256 populated
    assert len(body["payload_hash"]) == 64
    assert body["received_at"]  # ISO timestamp populated


def test_submit_with_explicit_priority_and_agent():
    response = client.post(
        "/api/v1/intake",
        json={
            "source": "cli",
            "title": "Urgent bug",
            "agent": "kai",
            "priority": 1,
            "payload": {"file": "auth.py"},
        },
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["priority"] == 1
    assert body["agent"] == "kai"


def test_payload_hash_is_stable_for_same_payload():
    payload = {"b": 2, "a": 1, "nested": {"y": 9, "x": 8}}
    r1 = client.post(
        "/api/v1/intake", json={"source": "s", "payload": payload}, headers=AUTH
    )
    r2 = client.post(
        "/api/v1/intake", json={"source": "s", "payload": payload}, headers=AUTH
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Hashes must match regardless of dict insertion order
    assert r1.json()["payload_hash"] == r2.json()["payload_hash"]


# ── Idempotency ─────────────────────────────────────────────


def test_idempotency_key_returns_same_task():
    key = "abc-123-retry-token"
    payload = {"source": "zapier", "payload": {"event": "purchase"}}
    r1 = client.post(
        "/api/v1/intake",
        json=payload,
        headers={**AUTH, "Idempotency-Key": key},
    )
    r2 = client.post(
        "/api/v1/intake",
        json=payload,
        headers={**AUTH, "Idempotency-Key": key},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["task_id"] == r2.json()["task_id"]
    assert r1.json()["payload_hash"] == r2.json()["payload_hash"]


def test_idempotency_key_too_long_returns_400():
    response = client.post(
        "/api/v1/intake",
        json={"source": "zapier", "payload": {}},
        headers={**AUTH, "Idempotency-Key": "x" * 200},
    )
    assert response.status_code == 400


def test_without_idempotency_key_creates_new_task_each_call():
    payload = {"source": "zapier", "payload": {"x": 1}}
    r1 = client.post("/api/v1/intake", json=payload, headers=AUTH)
    r2 = client.post("/api/v1/intake", json=payload, headers=AUTH)
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Different task_ids — no dedupe without a key
    assert r1.json()["task_id"] != r2.json()["task_id"]


# ── Read endpoints ──────────────────────────────────────────


def test_get_task_by_id():
    created = client.post(
        "/api/v1/intake",
        json={"source": "cli", "title": "find-me", "payload": {}},
        headers=AUTH,
    ).json()
    task_id = created["task_id"]

    response = client.get(f"/api/v1/intake/{task_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["title"] == "find-me"


def test_get_unknown_task_returns_404():
    response = client.get("/api/v1/intake/nonexistent-id", headers=AUTH)
    assert response.status_code == 404


def test_list_tasks_returns_recent_first():
    for i in range(3):
        client.post(
            "/api/v1/intake",
            json={"source": "cli", "title": f"task-{i}", "payload": {}},
            headers=AUTH,
        )
    response = client.get("/api/v1/intake", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 3
    titles = [t["title"] for t in body["tasks"]]
    # Newest first (last inserted comes first because of reverse sort)
    assert titles[0] == "task-2"


def test_list_tasks_filter_by_source():
    client.post(
        "/api/v1/intake", json={"source": "filter-A", "payload": {}}, headers=AUTH
    )
    client.post(
        "/api/v1/intake", json={"source": "filter-B", "payload": {}}, headers=AUTH
    )
    response = client.get("/api/v1/intake?source=filter-A", headers=AUTH)
    assert response.status_code == 200
    sources = {t["source"] for t in response.json()["tasks"]}
    assert sources == {"filter-A"}


def test_list_tasks_pagination_limit():
    response = client.get("/api/v1/intake?limit=99999", headers=AUTH)
    assert response.status_code == 422  # FastAPI Query(le=500) rejects


# ── Delete ──────────────────────────────────────────────────


def test_delete_task_removes_it():
    created = client.post(
        "/api/v1/intake",
        json={"source": "cli", "title": "delete-me", "payload": {}},
        headers=AUTH,
    ).json()
    task_id = created["task_id"]

    response = client.delete(f"/api/v1/intake/{task_id}", headers=AUTH)
    assert response.status_code == 204

    # Gone
    response = client.get(f"/api/v1/intake/{task_id}", headers=AUTH)
    assert response.status_code == 404


def test_delete_unknown_task_returns_404():
    response = client.delete("/api/v1/intake/does-not-exist", headers=AUTH)
    assert response.status_code == 404


def test_delete_clears_idempotency_index_so_replay_re_enqueues():
    key = "delete-then-replay"
    payload = {"source": "zapier", "payload": {"x": 1}}

    # Create with idempotency key
    r1 = client.post(
        "/api/v1/intake", json=payload, headers={**AUTH, "Idempotency-Key": key}
    ).json()
    task_id_1 = r1["task_id"]

    # Delete
    client.delete(f"/api/v1/intake/{task_id_1}", headers=AUTH)

    # Replay with same key → should create a new task (not return the deleted one)
    r2 = client.post(
        "/api/v1/intake", json=payload, headers={**AUTH, "Idempotency-Key": key}
    ).json()
    assert r2["task_id"] != task_id_1


# ── Auth gates on read endpoints too ────────────────────────


def test_get_requires_auth():
    response = client.get("/api/v1/intake")
    assert response.status_code == 401


def test_list_requires_auth():
    response = client.get("/api/v1/intake/anything")
    assert response.status_code == 401
