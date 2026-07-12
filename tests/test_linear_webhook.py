import hmac
import hashlib
import json
import pytest
from fastapi.testclient import TestClient

from prismatic.gateway.server import app

client = TestClient(app)


def test_linear_webhook_no_signature(monkeypatch):
    """If no signature is provided, the request should be rejected with 401."""
    monkeypatch.setenv("LINEAR_WEBHOOK_SIGNING_SECRET", "test_secret")
    response = client.post("/api/gateway/linear", json={"action": "test"})
    assert response.status_code == 401
    assert response.json() == {"status": "auth-failed"}


def test_linear_webhook_invalid_signature(monkeypatch):
    """If signature does not match, the request should be rejected with 401."""
    monkeypatch.setenv("LINEAR_WEBHOOK_SIGNING_SECRET", "test_secret")
    response = client.post(
        "/api/gateway/linear",
        json={"action": "test"},
        headers={"linear-signature": "wrong_signature_value"},
    )
    assert response.status_code == 401
    assert response.json() == {"status": "auth-failed"}


def test_linear_webhook_valid_signature(monkeypatch):
    """If signature is valid, the request should succeed."""
    secret = "test_secret"
    monkeypatch.setenv("LINEAR_WEBHOOK_SIGNING_SECRET", secret)

    payload = {"action": "test"}
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/gateway/linear",
        content=body,
        headers={"linear-signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "webhook received"}


def test_linear_webhook_alias_valid_signature(monkeypatch):
    """If signature is valid, the alias route /webhooks/linear should also succeed."""
    secret = "test_secret"
    monkeypatch.setenv("LINEAR_WEBHOOK_SIGNING_SECRET", secret)

    payload = {"action": "test"}
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/linear",
        content=body,
        headers={"linear-signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "webhook received"}
