from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from prismatic.gateway import event_bus, server


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client: TestClient, body: bytes, signature: str):
    return client.post(
        "/api/gateway/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "00000000-0000-0000-0000-000000000099",
            "X-Hub-Signature-256": signature,
        },
    )


def test_github_webhook_accepts_official_raw_body_hmac(monkeypatch):
    secret = "test-primary-secret"
    body = b'{"zen":"raw-body-contract"}'
    monkeypatch.setattr(server, "get_github_secrets", lambda: [secret])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)

    with TestClient(server.app) as client:
        response = _post(client, body, _signature(secret, body))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_github_webhook_rejects_header_prefixed_payload_hmac(monkeypatch):
    secret = "test-primary-secret"
    body = b'{"zen":"raw-body-contract"}'
    prefixed = b"x-hub-signature-256:" + body
    monkeypatch.setattr(server, "get_github_secrets", lambda: [secret])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)

    with TestClient(server.app) as client:
        response = _post(client, body, _signature(secret, prefixed))

    assert response.status_code == 401
    assert response.json()["status"] == "auth-failed"


def test_github_webhook_accepts_secondary_rotation_secret(monkeypatch):
    primary = "test-new-primary"
    secondary = "test-old-secondary"
    body = b'{"zen":"rotation-window"}'
    monkeypatch.setattr(server, "get_github_secrets", lambda: [primary, secondary])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)

    with TestClient(server.app) as client:
        response = _post(client, body, _signature(secondary, body))

    assert response.status_code == 200
