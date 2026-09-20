"""tests/test_webhook_hardening.py — fail-closed webhook HMAC verification.

Covers the Prismatic generic scheme (X-Prismatic-Signature) on
/api/pwp/webhooks/zapier plus the fail-closed vendor webhooks
(/api/gateway/github, /api/gateway/linear): valid passes; missing, wrong,
stale, tampered, and unconfigured-secret deliveries are rejected (401) and
written to the audit ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from prismatic.gateway import webhook_auth
from prismatic.gateway import server
from prismatic.hypervisor.ledger import get_hypervisor_ledger

SECRET = "test-webhook-primary-secret"
SECONDARY = "test-webhook-secondary-secret"
GITHUB_SECRET = "test-github-secret"
LINEAR_SECRET = "test-linear-secret"


@pytest.fixture()
def signed_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("PRISMATIC_WEBHOOK_SECRET_SECONDARY", SECONDARY)
    return TestClient(server.app)


def _zapier_post(client: TestClient, body: bytes, sig_header: str | None):
    headers = {"Content-Type": "application/json"}
    if sig_header is not None:
        headers["X-Prismatic-Signature"] = sig_header
    return client.post("/api/pwp/webhooks/zapier", content=body, headers=headers)


def _ledger_has_auth_failure(source: str, reason: str) -> bool:
    events = get_hypervisor_ledger().list_events(limit=200, producer="gateway")
    return any(
        e.action == "WEBHOOK_AUTH_FAILED"
        and e.payload.get("source") == source
        and e.payload.get("reason") == reason
        for e in events
    )


# ── Prismatic generic scheme ─────────────────────────────────────────


def test_valid_signature_passes(signed_client):
    body = json.dumps({"site_slug": "prismatic-core", "event": "lead_captured"}).encode()
    sig = webhook_auth.sign_delivery(SECRET, body)
    resp = _zapier_post(signed_client, body, sig)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_secondary_rotation_secret_accepted(signed_client):
    body = json.dumps({"site_slug": "prismatic-core"}).encode()
    sig = webhook_auth.sign_delivery(SECONDARY, body)
    resp = _zapier_post(signed_client, body, sig)
    assert resp.status_code == 200


def test_missing_signature_rejected_and_logged(signed_client):
    body = b'{"site_slug": "prismatic-core"}'
    resp = _zapier_post(signed_client, body, None)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "missing-signature"
    assert _ledger_has_auth_failure("zapier", "missing-signature")


def test_wrong_signature_rejected(signed_client):
    body = b'{"site_slug": "prismatic-core"}'
    sig = webhook_auth.sign_delivery("wrong-secret", body)
    resp = _zapier_post(signed_client, body, sig)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "bad-signature"
    assert _ledger_has_auth_failure("zapier", "bad-signature")


def test_tampered_payload_rejected(signed_client):
    body = json.dumps({"site_slug": "prismatic-core"}).encode()
    sig = webhook_auth.sign_delivery(SECRET, body)
    tampered = json.dumps({"site_slug": "evil-corp"}).encode()
    resp = _zapier_post(signed_client, tampered, sig)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "bad-signature"


def test_stale_timestamp_rejected(signed_client, monkeypatch):
    monkeypatch.setenv("PRISMATIC_WEBHOOK_MAX_AGE_SECONDS", "300")
    body = b'{"site_slug": "prismatic-core"}'
    old_ts = int(time.time()) - 3600
    sig = webhook_auth.sign_delivery(SECRET, body, timestamp=old_ts)
    resp = _zapier_post(signed_client, body, sig)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "stale-timestamp"
    assert _ledger_has_auth_failure("zapier", "stale-timestamp")


def test_future_timestamp_beyond_window_rejected(signed_client):
    body = b'{"site_slug": "prismatic-core"}'
    future_ts = int(time.time()) + 3600
    sig = webhook_auth.sign_delivery(SECRET, body, timestamp=future_ts)
    resp = _zapier_post(signed_client, body, sig)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "stale-timestamp"


def test_malformed_signature_rejected(signed_client):
    body = b'{"site_slug": "prismatic-core"}'
    resp = _zapier_post(signed_client, body, "not-a-valid-header")
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "malformed-signature"


def test_no_secret_configured_rejects(monkeypatch):
    monkeypatch.delenv("PRISMATIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_WEBHOOK_SECRET_SECONDARY", raising=False)
    client = TestClient(server.app)
    body = b'{"site_slug": "prismatic-core"}'
    resp = _zapier_post(client, body, None)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "no-secret-configured"


def test_stripe_pwp_listener_also_fail_closed(signed_client):
    body = json.dumps({"site_slug": "prismatic-core", "amount_total": 5000}).encode()
    resp = signed_client.post(
        "/api/pwp/webhooks/stripe",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert _ledger_has_auth_failure("pwp-stripe", "missing-signature")


# ── Vendor webhooks: fail-closed ─────────────────────────────────────


def _github_post(client: TestClient, body: bytes, sig: str | None):
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "ping"}
    if sig is not None:
        headers["X-Hub-Signature-256"] = sig
    return client.post("/api/gateway/github", content=body, headers=headers)


def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_valid_signature_passes(monkeypatch):
    from prismatic.gateway import event_bus

    monkeypatch.setattr(server, "get_github_secrets", lambda: [GITHUB_SECRET])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)
    client = TestClient(server.app)
    body = b'{"zen":"ok"}'
    resp = _github_post(client, body, _github_sig(GITHUB_SECRET, body))
    assert resp.status_code == 200


def test_github_missing_signature_rejected_and_logged(monkeypatch):
    from prismatic.gateway import event_bus

    monkeypatch.setattr(server, "get_github_secrets", lambda: [GITHUB_SECRET])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)
    client = TestClient(server.app)
    resp = _github_post(client, b'{"zen":"nope"}', None)
    assert resp.status_code == 401
    assert resp.json()["reason"] == "missing-signature"
    assert _ledger_has_auth_failure("github", "missing-signature")


def test_github_no_secret_configured_rejected(monkeypatch):
    from prismatic.gateway import event_bus

    monkeypatch.setattr(server, "get_github_secrets", lambda: [])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)
    client = TestClient(server.app)
    body = b'{"zen":"nope"}'
    resp = _github_post(client, body, _github_sig(GITHUB_SECRET, body))
    assert resp.status_code == 401
    assert resp.json()["reason"] == "no-secret-configured"


def _linear_post(client: TestClient, body: bytes, sig: str | None):
    headers = {"Content-Type": "application/json"}
    if sig is not None:
        headers["linear-signature"] = sig
    return client.post("/api/gateway/linear", content=body, headers=headers)


def test_linear_missing_signature_rejected(monkeypatch):
    from prismatic.gateway import event_bus

    monkeypatch.setattr(server, "get_linear_secrets", lambda: [LINEAR_SECRET])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)
    client = TestClient(server.app)
    resp = _linear_post(client, b'{"action":"x"}', None)
    assert resp.status_code == 401
    assert resp.json()["reason"] == "missing-signature"
    assert _ledger_has_auth_failure("linear", "missing-signature")


def test_linear_wrong_signature_rejected(monkeypatch):
    from prismatic.gateway import event_bus

    monkeypatch.setattr(server, "get_linear_secrets", lambda: [LINEAR_SECRET])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: None)
    client = TestClient(server.app)
    body = b'{"action":"x"}'
    bad = hmac.new(b"wrong", body, hashlib.sha256).hexdigest()
    resp = _linear_post(client, body, bad)
    assert resp.status_code == 401
    assert resp.json()["reason"] == "bad-signature"


# ── Unit-level: constant-time multi-secret compare ───────────────────


def test_verify_vendor_hmac_accepts_any_slot():
    body = b"payload"
    secrets = ["primary", "secondary"]
    good = hmac.new(b"secondary", body, hashlib.sha256).hexdigest()
    ok, reason = webhook_auth.verify_vendor_hmac(body, good, secrets)
    assert (ok, reason) == (True, "ok")


def test_verify_vendor_hmac_rejects_without_secrets():
    ok, reason = webhook_auth.verify_vendor_hmac(b"x", "ab" * 32, [])
    assert (ok, reason) == (False, "no-secret-configured")
