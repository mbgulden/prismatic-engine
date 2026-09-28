"""Tests for the Zapier webhook client (Phase 4.6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

HERE = Path(__file__).resolve()
PKG_ROOT = HERE.parents[3]
SHIP_ROOT = HERE.parents[4]
if str(SHIP_ROOT) not in sys.path:
    sys.path.insert(0, str(SHIP_ROOT))

from prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client import (  # noqa: E402
    FareHarborCompany,
    FareHarborNotFoundError,
    WebhookProbe,
    ZapierClient,
    ZapierError,
)


# ── FareHarborCompany ────────────────────────────────────────────────────
class TestFareHarborCompany:
    def test_from_api_basic(self):
        data = {
            "company": {
                "shortname": "activeoahutours",
                "name": "Active Oahu Tours",
                "pk": 252,
                "url": "http://activeoahutours.com/",
                "processor_currency": "usd",
                "enabled_processor_types": ["stripe"],
                "deactivation_status": "active",
            }
        }
        c = FareHarborCompany.from_api(data)
        assert c.shortname == "activeoahutours"
        assert c.name == "Active Oahu Tours"
        assert c.pk == 252
        assert c.url == "http://activeoahutours.com/"
        assert c.currency == "usd"
        assert c.processors == ["stripe"]
        assert c.is_active is True

    def test_from_api_deactivated(self):
        data = {
            "company": {
                "shortname": "demo",
                "name": "Demo Co",
                "deactivation_status": "deactivated",
            }
        }
        c = FareHarborCompany.from_api(data)
        assert c.is_active is False

    def test_from_api_missing_company_key(self):
        c = FareHarborCompany.from_api({})
        assert c.shortname == ""
        assert c.is_active is True  # default

    def test_to_dict_round_trip(self):
        c = FareHarborCompany(
            shortname="demo",
            name="Demo Co",
            pk=42,
            url="https://example.com",
            currency="usd",
            processors=["stripe"],
            is_active=True,
        )
        d = c.to_dict()
        assert d["shortname"] == "demo"
        assert d["pk"] == 42
        assert d["processors"] == ["stripe"]


# ── WebhookProbe ─────────────────────────────────────────────────────────
class TestWebhookProbe:
    def test_to_dict(self):
        p = WebhookProbe(
            url="https://hooks.zapier.com/x",
            reachable=True,
            status=200,
            content_type="application/json",
        )
        d = p.to_dict()
        assert d["url"] == "https://hooks.zapier.com/x"
        assert d["reachable"] is True
        assert d["status"] == 200
        assert d["content_type"] == "application/json"
        assert d["error_message"] == ""


# ── ZapierClient.from_env ────────────────────────────────────────────────
class TestZapierClientFromEnv:
    def test_raises_when_no_webhook_url(self, monkeypatch):
        monkeypatch.delenv("ZAPIER_WEBHOOK_URL", raising=False)
        with pytest.raises(ValueError) as excinfo:
            ZapierClient.from_env()
        msg = str(excinfo.value)
        assert "ZAPIER_WEBHOOK_URL" in msg
        assert "Zapier webhook URL" in msg

    def test_uses_provided_url(self):
        c = ZapierClient.from_env(webhook_url="https://hooks.zapier.com/abc")
        assert c.webhook_url == "https://hooks.zapier.com/abc"

    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/from-env")
        c = ZapierClient.from_env()
        assert c.webhook_url == "https://hooks.zapier.com/from-env"
        # token_source is recorded (env: <which env var>).
        assert "env" in c.webhook_token_source

    def test_custom_fareharbor_base(self):
        c = ZapierClient.from_env(
            webhook_url="https://x",
            fareharbor_base="https://staging.fareharbor.com/api/v1/companies",
        )
        assert c.fareharbor_base == "https://staging.fareharbor.com/api/v1/companies"


# ── ZapierClient.probe_webhook ───────────────────────────────────────────
class TestProbeWebhook:
    def test_reachable_on_200(self):
        c = ZapierClient(webhook_url="https://httpbin.org/status/200")
        # We don't actually hit the network in tests by default — mock.
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": "{}",
            }
            probe = c.probe_webhook()
            assert probe.reachable is True
            assert probe.status == 200

    def test_405_falls_back_to_get(self):
        c = ZapierClient(webhook_url="https://example.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.side_effect = [
                {"status": 405, "headers": {}, "body": ""},
                {"status": 200, "headers": {"Content-Type": "text/html"}, "body": ""},
            ]
            probe = c.probe_webhook()
            assert probe.reachable is True
            assert probe.status == 200
            # HEAD was called first, then GET.
            assert mock_http.call_count == 2

    def test_unreachable_on_404(self):
        c = ZapierClient(webhook_url="https://example.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 404,
                "headers": {},
                "body": "",
            }
            probe = c.probe_webhook()
            assert probe.reachable is False
            assert probe.status == 404
            assert probe.error_message == "HTTP 404"

    def test_unreachable_on_transport_error(self):
        c = ZapierClient(webhook_url="https://unreachable.example.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.side_effect = ZapierError("connection refused")
            probe = c.probe_webhook()
            assert probe.reachable is False
            assert probe.status == 0
            assert "connection refused" in probe.error_message


# ── ZapierClient.probe_fareharbor ────────────────────────────────────────
class TestProbeFareHarbor:
    def test_resolves_activeoahutours(self):
        c = ZapierClient(webhook_url="https://x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "company": {
                            "shortname": "activeoahutours",
                            "name": "Active Oahu Tours",
                            "pk": 252,
                            "url": "http://activeoahutours.com/",
                            "processor_currency": "usd",
                            "enabled_processor_types": ["stripe"],
                            "deactivation_status": "active",
                        }
                    }
                ),
            }
            company = c.probe_fareharbor("activeoahutours")
            assert company.shortname == "activeoahutours"
            assert company.name == "Active Oahu Tours"
            assert company.pk == 252
            assert company.is_active is True

    def test_404_raises_not_found(self):
        c = ZapierClient(webhook_url="https://x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {"status": 404, "headers": {}, "body": ""}
            with pytest.raises(FareHarborNotFoundError) as excinfo:
                c.probe_fareharbor("nonexistent-co")
            assert excinfo.value.status == 404
            assert "nonexistent-co" in str(excinfo.value)

    def test_empty_shortname_raises(self):
        c = ZapierClient(webhook_url="https://x")
        with pytest.raises(FareHarborNotFoundError) as excinfo:
            c.probe_fareharbor("")
        assert "empty" in str(excinfo.value)

    def test_5xx_raises_zapier_error(self):
        c = ZapierClient(webhook_url="https://x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 500,
                "headers": {},
                "body": "internal error",
            }
            with pytest.raises(ZapierError) as excinfo:
                c.probe_fareharbor("activeoahutours")
            assert excinfo.value.status == 500

    def test_non_json_response_raises(self):
        c = ZapierClient(webhook_url="https://x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 200,
                "headers": {},
                "body": "not json",
            }
            with pytest.raises(ZapierError) as excinfo:
                c.probe_fareharbor("activeoahutours")
            assert "non-JSON" in str(excinfo.value)

    def test_200_with_missing_shortname_raises(self):
        c = ZapierClient(webhook_url="https://x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 200,
                "headers": {},
                "body": json.dumps({"company": {}}),
            }
            with pytest.raises(FareHarborNotFoundError) as excinfo:
                c.probe_fareharbor("activeoahutours")
            assert "did not contain a valid company.shortname" in str(excinfo.value)


# ── ZapierClient.validate ────────────────────────────────────────────────
class TestValidate:
    def test_validate_no_shortname(self):
        c = ZapierClient(webhook_url="https://hooks.zapier.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.return_value = {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": "{}",
            }
            val = c.validate()
            assert val.webhook.reachable is True
            assert val.fareharbor is None
            # Head was called once (no fall-back).
            assert mock_http.call_count == 1

    def test_validate_with_shortname(self):
        c = ZapierClient(webhook_url="https://hooks.zapier.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.side_effect = [
                {
                    "status": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": "",
                },
                {
                    "status": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(
                        {
                            "company": {
                                "shortname": "activeoahutours",
                                "name": "Active Oahu Tours",
                                "pk": 252,
                                "processor_currency": "usd",
                                "enabled_processor_types": ["stripe"],
                                "deactivation_status": "active",
                            }
                        }
                    ),
                },
            ]
            val = c.validate(fareharbor_shortname="activeoahutours")
            assert val.webhook.reachable is True
            assert val.fareharbor is not None
            assert val.fareharbor.shortname == "activeoahutours"

    def test_validate_fareharbor_404_reraises(self):
        c = ZapierClient(webhook_url="https://hooks.zapier.com/x")
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.zapier_client._http_request"
        ) as mock_http:
            mock_http.side_effect = [
                {"status": 200, "headers": {}, "body": ""},
                {"status": 404, "headers": {}, "body": ""},
            ]
            with pytest.raises(FareHarborNotFoundError):
                c.validate(fareharbor_shortname="nope")


# ── Live test (skipped if no network) ────────────────────────────────────
class TestLiveFareHarbor:
    """Optional: real network call to fareharbor.com.

    Skipped when the sandbox blocks network access. Mark with
    `pytest.mark.network` so CI can exclude it.
    """

    @pytest.mark.network
    def test_live_activeoahutours(self):
        c = ZapierClient(webhook_url="https://example.com/nonexistent")
        company = c.probe_fareharbor("activeoahutours")
        assert company.shortname == "activeoahutours"
        assert company.is_active is True
