"""Tests for legacy_seo_registry — the v1→v2 registry adapter.

Covers:
  - v1 detection
  - v1→v2 adaptation
  - v2 passthrough (already-v2 registries are returned with explicit defaults)
  - default_metric_specs construction (dedup, source, format, front_of_card)
  - per-site passthrough of v1 fields
  - empty/invalid input
  - integration: load_registry() applied to a v1 file returns v2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.pwp.capabilities.publish_kpi_tracker.legacy_seo_registry import (
    V2_DEFAULT_CAPABILITY,
    _collect_v1_event_names,
    _is_v1,
    adapt_v1_to_v2,
    detect_and_adapt,
)


# ---------- fixtures ---------------------------------------------------------

V1_REGISTRY = {
    "version": 1,
    "sites": [
        {
            "slug": "active-oahu",
            "name": "Active Oahu Tours",
            "domain": "activeoahutours.com",
            "origin": "https://activeoahutours.com",
            "gsc_property": "sc-domain:activeoahutours.com",
            "sitemap_url": "https://activeoahutours.com/sitemap.xml",
            "site_dir_candidates": ["/work/active-oahu-tours-mirror/site"],
            "ga4_property_env": "AOT_GA4_PROPERTY_ID",
            "ga4_property_id": None,
            "gtm_container_id": None,
            "gtm_container_env": "AOT_GTM_CONTAINER_ID",
            "ga4_measurement_id": None,
            "ga4_measurement_env": "AOT_GA4_MEASUREMENT_ID",
            "expected_data_layer_events": [
                "booking_click",
                "booking_start",
                "begin_checkout",
                "purchase",
                "generate_lead",
            ],
            "booking_provider": "FareHarbor",
            "booking_revenue_notes": "GA4 revenue requires purchase events.",
        },
        {
            "slug": "site-b",
            "name": "Site B",
            "domain": "siteb.example.com",
            "gsc_property": "sc-domain:siteb.example.com",
            "expected_data_layer_events": ["page_view", "sign_up"],
            "expected_ga4_recommended_events": ["purchase"],
        },
    ],
}


V2_REGISTRY = {
    "version": 2,
    "pwp_kpi_capability": {"enabled": True, "operator": "ned"},
    "default_metric_specs": {
        "page_view": {"id": "page_view", "label": "Page View",
                      "source": "ga4", "event": "page_view",
                      "format": "number", "front_of_card": False},
    },
    "sites": [
        {
            "slug": "site-c",
            "name": "Site C",
            "domain": "sitec.example.com",
            "pwp_kpi_override": {"enabled": False},
            "pwp_kpi_metric_specs": {
                "page_view": {"id": "page_view", "label": "PV",
                              "source": "ga4", "format": "number"},
            },
        },
    ],
}


# ---------- detection --------------------------------------------------------

def test_detect_v1_with_legacy_fields():
    assert _is_v1(V1_REGISTRY) is True


def test_detect_v1_false_for_v2():
    assert _is_v1(V2_REGISTRY) is False


def test_detect_v1_false_for_empty_sites():
    assert _is_v1({"version": 1, "sites": []}) is False


def test_detect_v1_false_for_non_dict():
    assert _is_v1([]) is False  # type: ignore[arg-type]


# ---------- v1 → v2 adaptation ----------------------------------------------

def test_adapt_v1_emits_pwp_kpi_capability_block():
    v2 = adapt_v1_to_v2(V1_REGISTRY)
    assert "pwp_kpi_capability" in v2
    assert v2["pwp_kpi_capability"]["enabled"] is True
    assert v2["pwp_kpi_capability"]["operator"] == "ned"
    # The default shares dict should be present.
    assert v2["pwp_kpi_capability"]["shares"]["email_to_default"] == "mbgulden@gmail.com"


def test_adapt_v1_builds_default_metric_specs_from_events():
    v2 = adapt_v1_to_v2(V1_REGISTRY)
    specs = v2["default_metric_specs"]
    # Union of dataLayer + GA4-recommended across both sites, deduped.
    expected_event_names = {
        "booking_click", "booking_start", "begin_checkout",
        "purchase", "generate_lead", "page_view", "sign_up",
    }
    assert set(specs.keys()) == expected_event_names
    for mid, spec in specs.items():
        assert spec["id"] == mid
        assert spec["source"] == "ga4"
        assert spec["format"] == "number"
        assert spec["front_of_card"] is False
        assert spec["event"] == mid


def test_adapt_v1_preserves_v1_site_fields():
    v2 = adapt_v1_to_v2(V1_REGISTRY)
    sites = v2["sites"]
    assert len(sites) == 2
    aot = next(s for s in sites if s["slug"] == "active-oahu")
    # v1 fields should be preserved on each site.
    assert aot["gsc_property"] == "sc-domain:activeoahutours.com"
    assert aot["sitemap_url"] == "https://activeoahutours.com/sitemap.xml"
    assert aot["booking_provider"] == "FareHarbor"
    assert aot["ga4_property_env"] == "AOT_GA4_PROPERTY_ID"
    assert aot["ga4_measurement_env"] == "AOT_GA4_MEASUREMENT_ID"
    # The expected_data_layer_events field is also preserved.
    assert "booking_click" in aot["expected_data_layer_events"]


def test_adapt_v1_sets_pwp_kpi_override_enabled_true():
    v2 = adapt_v1_to_v2(V1_REGISTRY)
    for s in v2["sites"]:
        assert s["pwp_kpi_override"]["enabled"] is True


def test_adapt_v1_forces_ga4_measurement_id_to_null_gap5():
    """GAP-#5: env-var-only GA4 resolution.

    A v1 site entry that carries a static `ga4_measurement_id` literal
    must be forced to `None` after adaptation so the runtime always
    resolves the live GA4 property from `ga4_measurement_env`. This
    eliminates the tracking-property drift between shipped config and
    deployed loader — the static config used to lie about HDE's GA4 ID.
    """
    v1_with_literal = {
        "version": 1,
        "sites": [
            {
                "slug": "drifty",
                "name": "Drifty",
                "domain": "drifty.example",
                "ga4_measurement_id": "G-DRIFTED01",
                "ga4_measurement_env": "DRIFTY_GA4_MEAS_ID",
                "expected_data_layer_events": ["page_view"],
            }
        ],
    }
    v2 = adapt_v1_to_v2(v1_with_literal)
    site = v2["sites"][0]
    # The literal was forced to None — env-var is the only source of truth.
    assert site["ga4_measurement_id"] is None
    # The env-var name is preserved unchanged.
    assert site["ga4_measurement_env"] == "DRIFTY_GA4_MEAS_ID"


def test_adapt_v1_collects_unique_event_names():
    # Two sites with overlapping event lists should dedup.
    events = _collect_v1_event_names(V1_REGISTRY["sites"])
    assert events.count("page_view") == 1
    assert events.count("booking_click") == 1


# ---------- v2 passthrough --------------------------------------------------

def test_adapt_v2_passthrough_fills_default_capability():
    v2 = adapt_v1_to_v2(V2_REGISTRY)
    # The existing capability block should be preserved (operator "ned").
    assert v2["pwp_kpi_capability"]["operator"] == "ned"
    # default_metric_specs should be preserved.
    assert "page_view" in v2["default_metric_specs"]
    # Per-site pwp_kpi_override should be preserved verbatim.
    assert v2["sites"][0]["pwp_kpi_override"]["enabled"] is False


def test_adapt_v2_passthrough_fills_missing_defaults():
    # A v2-shaped registry without shares; the adapter should fill them in.
    v2 = adapt_v1_to_v2({"version": 2, "sites": [
        {"slug": "x", "name": "X", "domain": "x.example.com"},
    ]})
    cap = v2["pwp_kpi_capability"]
    assert cap["enabled"] is True
    assert cap["shares"]["email_to_default"] == "mbgulden@gmail.com"


# ---------- error / edge cases ---------------------------------------------

def test_adapt_rejects_non_dict():
    with pytest.raises(ValueError):
        adapt_v1_to_v2([])  # type: ignore[arg-type]


def test_detect_and_adapt_alias():
    assert detect_and_adapt(V1_REGISTRY) == adapt_v1_to_v2(V1_REGISTRY)


# ---------- integration: load_registry applies the adapter ------------------

def test_load_registry_adapts_v1_from_disk(tmp_path: Path, monkeypatch):
    """End-to-end: a v1 file on disk, loaded via load_registry(), returns v2."""
    from plugins.pwp.capabilities.publish_kpi_tracker import pwp_kpi_site_registry as reg

    f = tmp_path / "seo_sites.json"
    f.write_text(json.dumps(V1_REGISTRY), encoding="utf-8")
    loaded = reg.load_registry(f)
    # Adapter should have run.
    assert "pwp_kpi_capability" in loaded
    assert loaded["pwp_kpi_capability"]["enabled"] is True
    # Per-site v1 fields preserved.
    aot = next(s for s in loaded["sites"] if s["slug"] == "active-oahu")
    assert aot["booking_provider"] == "FareHarbor"
    # default_metric_specs populated.
    assert "booking_click" in loaded["default_metric_specs"]


def test_load_registry_real_path_uses_adapter(monkeypatch):
    """Smoke: load the real config/seo_sites.json and confirm v2 fields appear."""
    from plugins.pwp.capabilities.publish_kpi_tracker import pwp_kpi_site_registry as reg

    # The real registry is at <PWP_REPO>/config/seo_sites.json. We resolve it
    # relative to this file so the test doesn't depend on the absolute path
    # of the developer's machine. Layout:
    #   <PWP_REPO>/prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/tests/test_legacy_seo_registry.py
    # so PWP_REPO = parents[6] (the extra `tests/` level adds one).
    real = (Path(__file__).resolve().parents[6] / "config" / "seo_sites.json")
    if not real.exists():
        pytest.skip("real config/seo_sites.json not present in this environment")
    loaded = reg.load_registry(real)
    assert "pwp_kpi_capability" in loaded
    assert loaded["pwp_kpi_capability"]["enabled"] is True
    # Real registry has expected_data_layer_events on the site.
    aot = next((s for s in loaded["sites"] if s["slug"] == "active-oahu"), None)
    assert aot is not None
    assert aot["pwp_kpi_override"]["enabled"] is True
    # default_metric_specs should be populated from the dataLayer events.
    assert "booking_click" in loaded["default_metric_specs"]
    assert "purchase" in loaded["default_metric_specs"]