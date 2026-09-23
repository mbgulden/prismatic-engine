"""Portal Phase 1 (P0 #7): auth-provider identity resolution tests."""

from __future__ import annotations

import base64

import pytest

from pe.deploy import instance as deploy_instance
from prismatic.gateway import auth_providers
from prismatic.gateway.auth_providers import (
    AuthProviderError,
    get_provider,
)


class _StubClient:
    def __init__(self, host: str | None):
        self.host = host


class _StubRequest:
    """Minimal request surface the providers touch (headers + client.host)."""

    def __init__(
        self, headers: dict[str, str] | None = None, host: str | None = "198.51.100.7"
    ):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = _StubClient(host) if host is not None else None


def _basic_value(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def test_provider_names_known():
    assert set(auth_providers.PROVIDER_NAMES) == {
        "cloudflare-access",
        "basic-auth",
        "tailnet-only",
        "localhost-only",
    }
    assert auth_providers.DEFAULT_PROVIDER == "cloudflare-access"


def test_unknown_provider_rejected():
    with pytest.raises(AuthProviderError):
        get_provider("okta")


def test_get_provider_returns_each():
    for name in auth_providers.PROVIDER_NAMES:
        assert get_provider(name).name == name


@pytest.mark.asyncio
async def test_cloudflare_access_identity_from_email_header():
    provider = get_provider("cloudflare-access")
    identity = await provider.authenticate(
        _StubRequest({"Cf-Access-Authenticated-User-Email": "Michael@Example.com"})
    )
    assert identity is not None
    assert identity.provider == "cloudflare-access"
    assert identity.subject == "michael@example.com"


@pytest.mark.asyncio
async def test_cloudflare_access_no_header_no_identity():
    provider = get_provider("cloudflare-access")
    assert await provider.authenticate(_StubRequest()) is None


@pytest.mark.asyncio
async def test_cloudflare_access_malformed_email_rejected():
    provider = get_provider("cloudflare-access")
    assert (
        await provider.authenticate(
            _StubRequest({"cf-access-authenticated-user-email": "not-an-email"})
        )
        is None
    )


def _write_users(tmp_path, passwords: dict[str, str]):
    state = tmp_path / "state"
    deploy_instance.write_basic_auth_users(passwords, state, overwrite=True)
    return state


@pytest.mark.asyncio
async def test_basic_auth_valid_credentials(tmp_path):
    state = _write_users(tmp_path, {"ops": "s3cret"})
    provider = get_provider("basic-auth", state)
    identity = await provider.authenticate(
        _StubRequest({"authorization": _basic_value("ops", "s3cret")})
    )
    assert identity is not None
    assert identity.provider == "basic-auth"
    assert identity.subject == "ops"


@pytest.mark.asyncio
async def test_basic_auth_wrong_password_rejected(tmp_path):
    state = _write_users(tmp_path, {"ops": "s3cret"})
    provider = get_provider("basic-auth", state)
    assert (
        await provider.authenticate(
            _StubRequest({"authorization": _basic_value("ops", "wrong")})
        )
        is None
    )


@pytest.mark.asyncio
async def test_basic_auth_unknown_user_rejected(tmp_path):
    state = _write_users(tmp_path, {"ops": "s3cret"})
    provider = get_provider("basic-auth", state)
    assert (
        await provider.authenticate(
            _StubRequest({"authorization": _basic_value("nobody", "s3cret")})
        )
        is None
    )


@pytest.mark.asyncio
async def test_basic_auth_missing_users_file_fails_closed(tmp_path):
    provider = get_provider("basic-auth", tmp_path / "empty-state")
    assert (
        await provider.authenticate(
            _StubRequest({"authorization": _basic_value("ops", "s3cret")})
        )
        is None
    )


@pytest.mark.asyncio
async def test_basic_auth_challenge_header():
    assert get_provider("basic-auth").challenge == 'Basic realm="prismatic"'


@pytest.mark.asyncio
async def test_tailnet_only_rejects_public_ip():
    provider = get_provider("tailnet-only")
    assert await provider.authenticate(_StubRequest(host="8.8.8.8")) is None


@pytest.mark.asyncio
async def test_tailnet_only_accepts_tailscale_ip():
    # 100.64.0.0/10 is the Tailscale CGNAT range; whois is best-effort and
    # may fail on a box without tailscaled -- identity still establishes.
    provider = get_provider("tailnet-only")
    identity = await provider.authenticate(_StubRequest(host="100.64.0.23"))
    assert identity is not None
    assert identity.provider == "tailnet-only"
    assert identity.subject.startswith("tailnet:")


@pytest.mark.asyncio
async def test_localhost_only_accepts_loopback():
    provider = get_provider("localhost-only")
    for host in ("127.0.0.1", "127.0.0.2", "::1"):
        identity = await provider.authenticate(_StubRequest(host=host))
        assert identity is not None, host
        assert identity.subject == "localhost"


@pytest.mark.asyncio
async def test_localhost_only_rejects_non_loopback():
    provider = get_provider("localhost-only")
    assert await provider.authenticate(_StubRequest(host="10.0.0.9")) is None
    assert await provider.authenticate(_StubRequest(host=None)) is None
