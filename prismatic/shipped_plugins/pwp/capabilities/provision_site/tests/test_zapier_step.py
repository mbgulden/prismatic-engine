"""Tests for the Zapier webhook step (Phase 4.6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve()
SHIP_ROOT = HERE.parents[4]
if str(SHIP_ROOT) not in sys.path:
    sys.path.insert(0, str(SHIP_ROOT))

from plugins.pwp.capabilities.provision_site.steps.zapier import (  # noqa: E402
    _resolve_fareharbor_shortname,
    step_register_zapier_webhook,
)


def _run(domain: str, publish_root: Path, **kwargs):
    from plugins.pwp.capabilities.provision_site.types import ProvisionRun

    run = ProvisionRun(
        domain=domain, owner="me@example.com", started_at="2026-01-01T00:00:00+00:00"
    )
    return step_register_zapier_webhook(
        domain=domain,
        owner="me@example.com",
        run=run,
        publish_root=publish_root,
        **kwargs,
    )


# ── Shortname resolution ─────────────────────────────────────────────────
class TestShortnameResolution:
    def test_activeoahutours_default(self):
        assert _resolve_fareharbor_shortname({}, "active-oahu") == "activeoahutours"
        assert _resolve_fareharbor_shortname({}, "activeoahu") == "activeoahutours"
        assert (
            _resolve_fareharbor_shortname({}, "active-oahu-tours") == "activeoahutours"
        )

    def test_from_prior_outputs(self):
        prior = {"platform_detect": {"fareharbor_shortname": "demo-co"}}
        assert _resolve_fareharbor_shortname(prior, "ezshare") == "demo-co"

    def test_prior_outputs_shortname_alias(self):
        prior = {"platform_detect": {"shortname": "legacy-co"}}
        assert _resolve_fareharbor_shortname(prior, "ezshare") == "legacy-co"

    def test_empty_shortname_falls_through_to_slug(self):
        prior = {"platform_detect": {"fareharbor_shortname": ""}}
        assert _resolve_fareharbor_shortname(prior, "active-oahu") == "activeoahutours"

    def test_ezshare_falls_through_to_default(self):
        # No platform_detect; ezshare is not in the slug heuristic; default
        # to activeoahutours for Phase 4.6.
        assert _resolve_fareharbor_shortname({}, "ezshare") == "activeoahutours"


# ── Soft-fail when ZAPIER_WEBHOOK_URL is missing ────────────────────────
class TestMissingCredentials:
    def test_missing_webhook_url(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZAPIER_WEBHOOK_URL", raising=False)
        # Patch ZapierClient.from_env to simulate the "no creds" path
        # by raising the same ValueError the real from_env would raise.
        from plugins.pwp.capabilities.provision_site.zapier_client import ZapierClient

        with mock.patch.object(
            ZapierClient,
            "from_env",
            side_effect=ValueError(
                "Zapier webhook URL is not configured. Set ZAPIER_WEBHOOK_URL"
            ),
        ):
            result = _run("activeoahutours.com", tmp_path)
        assert result.status == "failed"
        assert result.output.get("_soft_failure") is True
        assert "ZAPIER_WEBHOOK_URL" in result.error
        assert "missing_env" in result.output
        assert "ZAPIER_WEBHOOK_URL" in result.output["missing_env"]

    def test_missing_webhook_url_via_env_only(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZAPIER_WEBHOOK_URL", raising=False)
        # The auth_loader's get_secret will go through env, profile.env, etc.
        # If none of those have it, from_env raises ValueError.
        result = _run("activeoahutours.com", tmp_path)
        assert result.status == "failed"
        assert result.output.get("_soft_failure") is True


# ── Webhook URL unreachable ─────────────────────────────────────────────
class TestWebhookUnreachable:
    def test_unreachable_webhook(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://unreachable.example.com/x")
        with mock.patch(
            "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_webhook"
        ) as mock_probe:
            mock_probe.return_value = mock.Mock(
                url="https://unreachable.example.com/x",
                reachable=False,
                status=404,
                content_type="",
                error_message="HTTP 404",
                to_dict=lambda: {
                    "url": "https://unreachable.example.com/x",
                    "reachable": False,
                    "status": 404,
                    "content_type": "",
                    "error_message": "HTTP 404",
                },
            )
            result = _run("activeoahutours.com", tmp_path)
        assert result.status == "failed"
        assert result.output.get("_soft_failure") is True
        assert "webhook" in result.output
        assert result.output["webhook"]["reachable"] is False


# ── Happy path: webhook reachable + FareHarbor resolves ─────────────────
class TestHappyPath:
    def test_complete_with_fareharbor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/abc")
        slug = "activeoahutours"

        # Make sure the sites dir exists & pre-seed a kpi file.
        sites_root = (
            Path(__file__).resolve().parents[3]
            / "pwp/capabilities/publish_kpi_tracker/sites"
        )
        sites_root.mkdir(parents=True, exist_ok=True)
        kpi_path = sites_root / f"{slug}.kpi.json"
        kpi_path.write_text(
            json.dumps({"site_slug": slug, "domain": "activeoahutours.com"}),
            encoding="utf-8",
        )

        try:
            with (
                mock.patch(
                    "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_webhook"
                ) as mock_probe,
                mock.patch(
                    "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_fareharbor"
                ) as mock_fh,
            ):
                mock_probe.return_value = mock.Mock(
                    url="https://hooks.zapier.com/abc",
                    reachable=True,
                    status=200,
                    content_type="application/json",
                    error_message="",
                    to_dict=lambda: {
                        "url": "https://hooks.zapier.com/abc",
                        "reachable": True,
                        "status": 200,
                        "content_type": "application/json",
                        "error_message": "",
                    },
                )
                mock_fh.return_value = mock.Mock(
                    shortname="activeoahutours",
                    name="Active Oahu Tours",
                    pk=252,
                    url="http://activeoahutours.com/",
                    currency="usd",
                    processors=["stripe"],
                    is_active=True,
                    to_dict=lambda: {
                        "shortname": "activeoahutours",
                        "name": "Active Oahu Tours",
                        "pk": 252,
                        "url": "http://activeoahutours.com/",
                        "currency": "usd",
                        "processors": ["stripe"],
                        "is_active": True,
                    },
                )

                result = _run("activeoahutours.com", tmp_path)
            assert result.status == "complete"
            assert result.output["fareharbor_shortname"] == "activeoahutours"
            assert result.output["zapier_block"]["webhook_reachable"] is True
            assert (
                result.output["zapier_block"]["fareharbor_company"]["shortname"]
                == "activeoahutours"
            )
            assert result.output["zapier_block"]["validated"] is True
            # The kpi file should now have external_sources.zapier.
            kpi = json.loads(kpi_path.read_text())
            assert "zapier" in kpi["external_sources"]
            assert (
                kpi["external_sources"]["zapier"]["webhook_url"]
                == "https://hooks.zapier.com/abc"
            )
        finally:
            # Clean up the test kpi file.
            if kpi_path.exists():
                kpi_path.unlink()

    def test_complete_no_fareharbor_data(self, tmp_path, monkeypatch):
        # When the FareHarbor probe fails (404, transport error), the
        # step still completes with the webhook URL recorded.
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/x")
        with (
            mock.patch(
                "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_webhook"
            ) as mock_probe,
            mock.patch(
                "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_fareharbor"
            ) as mock_fh,
        ):
            mock_probe.return_value = mock.Mock(
                url="https://hooks.zapier.com/x",
                reachable=True,
                status=200,
                content_type="application/json",
                error_message="",
                to_dict=lambda: {
                    "url": "https://hooks.zapier.com/x",
                    "reachable": True,
                    "status": 200,
                    "content_type": "application/json",
                    "error_message": "",
                },
            )
            from plugins.pwp.capabilities.provision_site.zapier_client import (
                FareHarborNotFoundError,
            )

            mock_fh.side_effect = FareHarborNotFoundError("not found", status=404)
            result = _run("activeoahutours.com", tmp_path)
        assert result.status == "complete"
        assert result.output["zapier_block"]["webhook_reachable"] is True
        assert result.output["zapier_block"]["fareharbor_company"] is None
        assert "not_found" in result.output["zapier_block"]["fareharbor_error"]

    def test_complete_with_transport_error_fareharbor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/x")
        with (
            mock.patch(
                "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_webhook"
            ) as mock_probe,
            mock.patch(
                "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_fareharbor"
            ) as mock_fh,
        ):
            mock_probe.return_value = mock.Mock(
                url="https://hooks.zapier.com/x",
                reachable=True,
                status=200,
                content_type="application/json",
                error_message="",
                to_dict=lambda: {
                    "url": "https://hooks.zapier.com/x",
                    "reachable": True,
                    "status": 200,
                    "content_type": "application/json",
                    "error_message": "",
                },
            )
            from plugins.pwp.capabilities.provision_site.zapier_client import (
                ZapierError,
            )

            mock_fh.side_effect = ZapierError("transport error", status=500)
            result = _run("activeoahutours.com", tmp_path)
        assert result.status == "complete"
        assert "transport_error" in result.output["zapier_block"]["fareharbor_error"]


# ── Step function discoverability ───────────────────────────────────────
class TestStepDiscovery:
    def test_step_name_in_STEP_NAMES(self):
        from plugins.pwp.capabilities.provision_site.orchestrator import STEP_NAMES

        assert "register_zapier_webhook" in STEP_NAMES

    def test_step_category_is_soft(self):
        from plugins.pwp.capabilities.provision_site.steps import STEP_CATEGORIES

        assert STEP_CATEGORIES.get("register_zapier_webhook") == "soft"

    def test_step_function_in_steps_module(self):
        from plugins.pwp.capabilities.provision_site import steps

        assert hasattr(steps, "step_register_zapier_webhook")
        assert callable(steps.step_register_zapier_webhook)


# ── Existing site kpi integration ───────────────────────────────────────
class TestExistingKpiFile:
    def test_updates_existing_kpi_without_breaking_other_sources(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/abc")
        slug = "activeoahutours"

        # Pre-seed a kpi with stripe already populated.
        sites_root = (
            Path(__file__).resolve().parents[3]
            / "pwp/capabilities/publish_kpi_tracker/sites"
        )
        sites_root.mkdir(parents=True, exist_ok=True)
        kpi_path = sites_root / f"{slug}.kpi.json"
        kpi_path.write_text(
            json.dumps(
                {
                    "site_slug": slug,
                    "domain": "activeoahutours.com",
                    "external_sources": {
                        "stripe": {"account_id": "acct_123", "validated": True},
                    },
                }
            ),
            encoding="utf-8",
        )

        try:
            with (
                mock.patch(
                    "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_webhook"
                ) as mock_probe,
                mock.patch(
                    "plugins.pwp.capabilities.provision_site.zapier_client.ZapierClient.probe_fareharbor"
                ) as mock_fh,
            ):
                mock_probe.return_value = mock.Mock(
                    url="https://hooks.zapier.com/abc",
                    reachable=True,
                    status=200,
                    content_type="text/html",
                    error_message="",
                    to_dict=lambda: {
                        "url": "https://hooks.zapier.com/abc",
                        "reachable": True,
                        "status": 200,
                        "content_type": "text/html",
                        "error_message": "",
                    },
                )
                mock_fh.return_value = mock.Mock(
                    shortname="activeoahutours",
                    name="Active",
                    pk=252,
                    url="x",
                    currency="usd",
                    processors=["stripe"],
                    is_active=True,
                    to_dict=lambda: {
                        "shortname": "activeoahutours",
                        "name": "Active",
                        "pk": 252,
                        "url": "x",
                        "currency": "usd",
                        "processors": ["stripe"],
                        "is_active": True,
                    },
                )
                result = _run("activeoahutours.com", tmp_path)
            assert result.status == "complete"
            # Stripe is preserved alongside Zapier.
            kpi = json.loads(kpi_path.read_text())
            assert "stripe" in kpi["external_sources"]
            assert "zapier" in kpi["external_sources"]
            assert kpi["external_sources"]["stripe"]["account_id"] == "acct_123"
        finally:
            if kpi_path.exists():
                kpi_path.unlink()
