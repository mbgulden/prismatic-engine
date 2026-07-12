from __future__ import annotations

import importlib.util
import json
import os
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
    monkeypatch.setenv("AOT_GA4_PROPERTY_ID", "123456789")
    sites = site_registry.load_managed_sites(REPO_ROOT / "config" / "seo_sites.json")
    active = next(site for site in sites if site.slug == "active-oahu")

    assert active.domain == "activeoahutours.com"
    assert active.gsc_property == "sc-domain:activeoahutours.com"
    assert active.effective_ga4_property_id == "123456789"
    assert active.effective_sitemap_url == "https://activeoahutours.com/sitemap.xml"


def test_scaffold_site_outputs_gsc_and_ga_setup_shape() -> None:
    data = site_registry.scaffold_site("example.com")

    assert data["slug"] == "example-com"
    assert data["origin"] == "https://example.com"
    assert data["gsc_property"] == "sc-domain:example.com"
    assert data["ga4_property_env"] == "EXAMPLE_COM_GA4_PROPERTY_ID"
    assert data["ga4_property_id"] is None


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
