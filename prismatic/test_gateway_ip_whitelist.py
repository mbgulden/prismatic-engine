from fastapi.testclient import TestClient

from prismatic.gateway.security import reset_dynamic_ip_allowlist_for_tests
from prismatic.gateway.server import app


def setup_function() -> None:
    reset_dynamic_ip_allowlist_for_tests()


def teardown_function() -> None:
    reset_dynamic_ip_allowlist_for_tests()


def test_dynamic_ip_registration_bypasses_gateway_api_block(monkeypatch):
    monkeypatch.setenv("PRISMATIC_ALLOWED_IPS", "198.51.100.7")
    monkeypatch.setenv("PRISMATIC_TRUSTED_PROXIES", "testclient")
    monkeypatch.setenv("PRISMATIC_IP_WHITELIST_SECRET", "one-shot")

    client = TestClient(app)
    headers = {"x-forwarded-for": "203.0.113.9"}

    blocked = client.post("/api/gateway/github", headers=headers, content=b"{}")
    assert blocked.status_code == 403
    assert blocked.json()["client_ip"] == "203.0.113.9"

    registered = client.post(
        "/api/gateway/auth/ip-whitelist",
        headers=headers,
        json={"secret": "one-shot"},
    )
    assert registered.status_code == 200
    assert registered.json()["registered_ip"] == "203.0.113.9"

    allowed = client.post("/api/gateway/github", headers=headers, content=b"{}")
    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok", "message": "webhook received"}


def test_ip_registration_secret_is_one_time(monkeypatch):
    monkeypatch.setenv("PRISMATIC_ALLOWED_IPS", "198.51.100.7")
    monkeypatch.setenv("PRISMATIC_TRUSTED_PROXIES", "testclient")
    monkeypatch.setenv("PRISMATIC_IP_WHITELIST_SECRET", "one-shot")

    client = TestClient(app)
    headers = {"x-forwarded-for": "203.0.113.10"}

    first = client.post(
        "/api/gateway/auth/ip-whitelist",
        headers=headers,
        json={"secret": "one-shot"},
    )
    assert first.status_code == 200

    replay = client.post(
        "/api/gateway/auth/ip-whitelist",
        headers={"x-forwarded-for": "203.0.113.11"},
        json={"secret": "one-shot"},
    )
    assert replay.status_code == 409
    assert "already been used" in replay.json()["detail"]


def test_forwarded_headers_ignored_without_trusted_proxy(monkeypatch):
    monkeypatch.setenv("PRISMATIC_ALLOWED_IPS", "203.0.113.12")
    monkeypatch.delenv("PRISMATIC_TRUSTED_PROXIES", raising=False)

    client = TestClient(app)
    response = client.post(
        "/api/gateway/github",
        headers={"x-forwarded-for": "203.0.113.12"},
        content=b"{}",
    )

    assert response.status_code == 403
    assert response.json()["client_ip"] == "testclient"
