"""Tests for StripeClient + step_register_stripe.

Coverage:
  - from_env precedence (STRIPE_RESTRICTED_KEY > STRIPE_API_KEY > STRIPE_SECRET_KEY)
  - direct construction rejects empty
  - validate() happy path
  - validate() auth failure (401)
  - list_products / list_prices happy paths
  - retry on 429
  - Bearer auth header
  - step_register_stripe soft-fail when no creds
  - step_register_stripe persists external_sources.stripe to kpi-collections.json
"""

from __future__ import annotations

import io
import http.client
import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prismatic.shipped_plugins.pwp.capabilities.provision_site.stripe_client import (
    STRIPE_API_URL,
    StripeClient,
    StripeError,
    StripePrice,
    StripeProduct,
)
from prismatic.shipped_plugins.pwp.capabilities.provision_site import auth_loader
from prismatic.shipped_plugins.pwp.capabilities.provision_site.steps.stripe import (
    step_register_stripe,
)


# --- helpers --------------------------------------------------------------

def _http_response(status: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.stripe.com/v1/x",
        status,
        "TestStatus",
        http.client.HTTPMessage(),
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def _ok_response(body: dict):
    class _Resp:
        def __init__(self, payload):
            self._payload = payload
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    return _Resp(json.dumps(body).encode("utf-8"))


# --- from_env precedence --------------------------------------------------

def test_from_env_prefers_stripe_restricted_key(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_live_primary")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_secondary")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_tertiary")
    c = StripeClient.from_env()
    assert c.token_source == "STRIPE_RESTRICTED_KEY"
    assert c.api_key == "rk_live_primary"


def test_from_env_falls_back_to_stripe_api_key(monkeypatch) -> None:
    monkeypatch.delenv("STRIPE_RESTRICTED_KEY", raising=False)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    c = StripeClient.from_env()
    assert c.token_source == "STRIPE_API_KEY"


def test_from_env_falls_back_to_stripe_secret_key(monkeypatch) -> None:
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_y")
    c = StripeClient.from_env()
    assert c.token_source == "STRIPE_SECRET_KEY"


def test_from_env_raises_when_no_token(monkeypatch) -> None:
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    # Block the auth_loader fallback so this test is hermetic
    with patch(
        "prismatic.shipped_plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=auth_loader.AuthResult(
            value=None, source="none", env_var="",
            hint="(test stub)", redaction="<missing>",
        ),
    ):
        with pytest.raises(ValueError, match="STRIPE_RESTRICTED_KEY"):
            StripeClient.from_env()


def test_direct_construction_rejects_empty() -> None:
    with pytest.raises(ValueError):
        StripeClient(api_key="")


def test_stripe_api_url_constant() -> None:
    assert STRIPE_API_URL == "https://api.stripe.com/v1"


# --- request shape --------------------------------------------------------

def test_validate_uses_bearer_auth(monkeypatch) -> None:
    """Stripe expects `Authorization: Bearer sk_live_...`."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["method"] = req.get_method()
        return _ok_response({"available": [{"currency": "usd", "amount": 12345}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = StripeClient(api_key="sk_test_x")
    balance = c.validate()
    assert captured["url"] == "https://api.stripe.com/v1/balance"
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"] == "Bearer sk_test_x"
    assert balance["available"][0]["amount"] == 12345


def test_validate_401_raises_stripe_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout=None):
        raise _http_response(401, {
            "error": {
                "type": "authentication_error",
                "code": "invalid_api_key",
                "message": "Invalid API Key provided: sk_test_***",
            },
        })
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = StripeClient(api_key="bad", max_retries=0)
    with pytest.raises(StripeError) as excinfo:
        c.validate()
    assert excinfo.value.status == 401
    assert excinfo.value.error_code == "invalid_api_key"
    assert excinfo.value.error_type == "authentication_error"


def test_retry_on_429(monkeypatch) -> None:
    """A 429 response must be retried (up to max_retries) and succeed."""
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _http_response(429, {"error": {"type": "rate_limit_error"}})
        return _ok_response({"available": [{"currency": "usd", "amount": 100}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = StripeClient(api_key="sk", max_retries=2, retry_backoff=0.0)
    out = c.validate()
    assert call_count["n"] == 2
    assert out["available"][0]["amount"] == 100


# --- list_products / list_prices -----------------------------------------

def test_list_products_happy_path(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": [
            {"id": "prod_1", "name": "Bookings", "active": True,
             "description": "Tour booking", "metadata": {}},
            {"id": "prod_2", "name": "Subscription", "active": False,
             "description": "", "metadata": {"plan": "pro"}},
        ],
    }))
    c = StripeClient(api_key="sk")
    products = c.list_products()
    assert len(products) == 2
    assert isinstance(products[0], StripeProduct)
    assert products[0].id == "prod_1"
    assert products[0].name == "Bookings"
    assert products[0].active is True
    assert products[1].active is False


def test_list_prices_happy_path(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": [
            {"id": "price_1", "product": "prod_1", "currency": "usd",
             "unit_amount": 9900, "active": True,
             "recurring": {"interval": "month"}},
        ],
    }))
    c = StripeClient(api_key="sk")
    prices = c.list_prices()
    assert len(prices) == 1
    assert isinstance(prices[0], StripePrice)
    assert prices[0].id == "price_1"
    assert prices[0].unit_amount == 9900
    assert prices[0].recurring_interval == "month"


# --- step_register_stripe --------------------------------------------------

def test_step_register_stripe_soft_fails_without_creds(
    tmp_path: Path, monkeypatch
) -> None:
    """When STRIPE_* keys are absent, the step must soft-fail (status=failed
    with _soft_failure=True in output)."""
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    # Block the auth_loader fallback so this test is hermetic
    with patch(
        "prismatic.shipped_plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=auth_loader.AuthResult(
            value=None, source="none", env_var="",
            hint="(test stub)", redaction="<missing>",
        ),
    ):
        run = MagicMock()
        result = step_register_stripe(
            domain="ezshare.systems", owner="me@x.com", run=run,
            publish_root=tmp_path,
        )
    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert "STRIPE_RESTRICTED_KEY" in result.output["missing_env"]
    assert "Set STRIPE_RESTRICTED_KEY" in result.output["hint"]


def test_step_register_stripe_persists_to_kpi_collections(
    tmp_path: Path, monkeypatch
) -> None:
    """When Stripe creds are present, validate(), and the per-site
    kpi-collections.json gets the external_sources.stripe block."""
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_test_x")
    monkeypatch.setenv("STRIPE_ACCOUNT_ID", "acct_test")

    # Mock StripeClient.from_env + validate
    fake_client = MagicMock()
    fake_client.account_id = "acct_test"
    fake_client.token_source = "STRIPE_RESTRICTED_KEY"
    fake_client.validate.return_value = {
        "available": [{"currency": "usd", "amount": 50000}],
    }

    # Pre-populate kpi-collections.json
    sites_root = tmp_path / "sites"
    sites_root.mkdir()
    (sites_root / "ezshare.kpi.json").write_text(json.dumps({
        "site_slug": "ezshare",
        "domain": "ezshare.systems",
        "version": 1,
    }))

    with patch(
        "prismatic.shipped_plugins.pwp.capabilities.provision_site.stripe_client.StripeClient.from_env",
        return_value=fake_client,
    ):
        run = MagicMock()
        result = step_register_stripe(
            domain="ezshare.systems", owner="me@x.com", run=run,
            publish_root=tmp_path,
            sites_root=sites_root,
        )

    assert result.status == "complete"
    assert result.output["key_type"] == "restricted"

    # The kpi-collections.json should now have external_sources.stripe
    saved = json.loads((sites_root / "ezshare.kpi.json").read_text())
    assert "external_sources" in saved
    assert "stripe" in saved["external_sources"]
    stripe_block = saved["external_sources"]["stripe"]
    assert stripe_block["account_id"] == "acct_test"
    assert stripe_block["key_type"] == "restricted"
    assert stripe_block["validated"] is True
    assert stripe_block["currency"] == "usd"
    assert stripe_block["available_cents"] == 50000


def test_step_register_stripe_creates_minimal_kpi_when_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """If kpi-collections.json doesn't exist yet (rare edge case), the
    step creates a minimal one with the stripe block."""
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_test_x")
    sites_root = tmp_path / "sites"
    sites_root.mkdir()

    fake_client = MagicMock()
    fake_client.account_id = None
    fake_client.token_source = "STRIPE_RESTRICTED_KEY"
    fake_client.validate.return_value = {"available": []}

    with patch(
        "prismatic.shipped_plugins.pwp.capabilities.provision_site.stripe_client.StripeClient.from_env",
        return_value=fake_client,
    ):
        run = MagicMock()
        result = step_register_stripe(
            domain="newsite.example.com", owner="me@x.com", run=run,
            publish_root=tmp_path,
            sites_root=sites_root,
        )

    assert result.status == "complete"
    saved = json.loads((sites_root / "newsite.kpi.json").read_text())
    assert saved["site_slug"] == "newsite"
    assert saved["domain"] == "newsite.example.com"
    assert "external_sources" in saved


def test_step_register_stripe_handles_invalid_creds(
    tmp_path: Path, monkeypatch
) -> None:
    """When the Stripe key is present but invalid (validate raises),
    the step must soft-fail with the StripeError details."""
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_test_bad")

    fake_client = MagicMock()
    fake_client.account_id = None
    fake_client.token_source = "STRIPE_RESTRICTED_KEY"
    fake_client.validate.side_effect = StripeError(
        "Invalid API Key", status=401, error_code="invalid_api_key",
        error_type="authentication_error",
    )

    with patch(
        "prismatic.shipped_plugins.pwp.capabilities.provision_site.stripe_client.StripeClient.from_env",
        return_value=fake_client,
    ):
        run = MagicMock()
        result = step_register_stripe(
            domain="x.com", owner="me@x.com", run=run,
            publish_root=tmp_path,
        )

    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert result.output["stripe_status"] == 401
    assert result.output["stripe_error_code"] == "invalid_api_key"
