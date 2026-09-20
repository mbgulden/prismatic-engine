from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SITE_REGISTRY = REPO_ROOT / "scripts" / "seo" / "site_registry.py"
_SPEC = importlib.util.spec_from_file_location("site_registry", _SITE_REGISTRY)
assert _SPEC is not None and _SPEC.loader is not None
site_registry = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = site_registry
_SPEC.loader.exec_module(site_registry)


def test_load_managed_sites_resolves_env_ga4_property(monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_GA4_PROPERTY_ID", "123456789")
    monkeypatch.setenv("PRISMATIC_GTM_CONTAINER_ID", "GTM-TEST123")
    monkeypatch.setenv("PRISMATIC_GA4_MEASUREMENT_ID", "G-TEST123")
    sites = site_registry.load_managed_sites(REPO_ROOT / "config" / "seo_sites.json")
    site = next(s for s in sites if s.slug == "prismatic-core")

    assert site.domain == "engine.prismatic.local"
    assert site.gsc_property == "sc-domain:engine.prismatic.local"
    assert site.effective_ga4_property_id == "123456789"
    assert site.effective_gtm_container_id == "GTM-TEST123"
    assert site.effective_ga4_measurement_id == "G-TEST123"
    assert "page_view" in site.expected_data_layer_events
    assert site.effective_sitemap_url == "https://engine.prismatic.local/sitemap.xml"


def test_scaffold_site_outputs_gsc_and_ga_setup_shape() -> None:
    data = site_registry.scaffold_site("example.com")

    assert data["slug"] == "example-com"
    assert data["origin"] == "https://example.com"
    assert data["gsc_property"] == "sc-domain:example.com"
    assert data["ga4_property_env"] == "EXAMPLE_COM_GA4_PROPERTY_ID"
    assert data["ga4_property_id"] is None
    assert data["gtm_container_env"] == "EXAMPLE_COM_GTM_CONTAINER_ID"
    assert data["gtm_container_id"] is None
    assert data["ga4_measurement_env"] == "EXAMPLE_COM_GA4_MEASUREMENT_ID"
    assert data["ga4_measurement_id"] is None
    assert "booking_click" in data["expected_data_layer_events"]


def test_site_registry_list_cli_uses_config(tmp_path: Path) -> None:
    cfg = tmp_path / "sites.json"
    cfg.write_text(json.dumps({"version": 1, "sites": [site_registry.scaffold_site("example.com")]}), encoding="utf-8")
    old = os.environ.get("PRISMATIC_SEO_SITES_CONFIG")
    os.environ["PRISMATIC_SEO_SITES_CONFIG"] = str(cfg)
    try:
        sites = site_registry.load_managed_sites()
    finally:
        if old is None:
            os.environ.pop("PRISMATIC_SEO_SITES_CONFIG", None)
        else:
            os.environ["PRISMATIC_SEO_SITES_CONFIG"] = old
    assert [site.slug for site in sites] == ["example-com"]


def test_setup_audit_detects_gtm_and_data_layer_fixture(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(
        """
        <!doctype html><html><head>
        <script>window.dataLayer=window.dataLayer||[];</script>
        <script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});})(window,document,'script','dataLayer','GTM-TEST123');</script>
        <script>window.dataLayer.push({event:'booking_click'});</script>
        <script>gtag('config','G-TEST123');</script>
        </head><body>
        <noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-TEST123"></iframe></noscript>
        </body></html>
        """,
        encoding="utf-8",
    )
    cfg = tmp_path / "sites.json"
    cfg.write_text(json.dumps({"version": 1, "sites": [{
        "slug": "fixture",
        "name": "Fixture Site",
        "domain": "fixture.example",
        "origin": "https://fixture.example",
        "gsc_property": "sc-domain:fixture.example",
        "sitemap_url": "https://fixture.example/sitemap.xml",
        "site_dir_candidates": [str(site_dir)],
        "gtm_container_id": "GTM-TEST123",
        "ga4_measurement_id": "G-TEST123",
        "expected_data_layer_events": ["booking_click", "purchase"],
        "ga4_property_id": None,
    }]}), encoding="utf-8")
    state = tmp_path / "state"
    env = {**os.environ, "PRISMATIC_SEO_SITES_CONFIG": str(cfg), "PRISMATIC_STATE_DIR": str(state)}
    result = subprocess.run(
        [sys.executable, "scripts/seo/managed_site_setup_audit.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode in (0, 1)
    report = json.loads((state / "seo" / "site-setup" / "latest_site_setup_audit.json").read_text())
    tag = report["sites"][0]["tag_layer"]
    assert tag["gtm_script_present"] is True
    assert tag["gtm_noscript_present"] is True
    assert tag["data_layer_present"] is True
    assert tag["ga4_measurement_present"] is True
    assert tag["observed_data_layer_events"] == ["booking_click"]
    assert tag["missing_data_layer_events"] == ["purchase"]
