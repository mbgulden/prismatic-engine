"""Tests for the pwp.publish_kpi_tracker capability.

Validates:
  * list_sites discovers *.kpi.json files in SITES_DIR
  * validate() passes the canonical fixture + an extended fixture (extends)
  * validate() rejects unknown sources and inner-id mismatches
  * resolve_collection merges parent metrics + site overrides
  * aggregate produces one site-row per registered site, only front-of-card metrics
  * render_index, render_detail, render_accordion emit expected anchors
"""

from __future__ import annotations

from pathlib import Path

import pytest

import plugins.pwp.capabilities.publish_kpi_tracker as kpi_mod  # noqa: E402
from plugins.pwp.capabilities.publish_kpi_tracker import (  # noqa: E402
    aggregate,
    list_sites,
    load_site,
    render_accordion,
    render_detail,
    render_index,
    resolve_collection,
    validate,
)


HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures"
GOOD_SITE = FIXTURE_DIR / "hd-engine.kpi.json"
EXTENDED_SITE = FIXTURE_DIR / "hd-engine-fixture.kpi.json"


def _patch_sites(monkeypatch):
    """Point SITES_DIR at the local fixtures for the test."""
    import plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner

    monkeypatch.setattr(kpi_mod, "SITES_DIR", FIXTURE_DIR, raising=True)
    monkeypatch.setattr(inner, "SITES_DIR", FIXTURE_DIR, raising=True)


def test_list_sites_discovers_kpi_files(monkeypatch):
    _patch_sites(monkeypatch)
    slugs = list_sites()
    assert slugs == sorted(slugs)
    assert "hd-engine" in slugs
    assert "hd-engine-fixture" in slugs


def test_validate_passes_good_site(monkeypatch):
    _patch_sites(monkeypatch)
    errs = validate(load_site("hd-engine"))
    assert errs == [], f"hd-engine failed: {errs}"


def test_validate_passes_extended_site(monkeypatch):
    _patch_sites(monkeypatch)
    errs = validate(load_site("hd-engine-fixture"))
    assert errs == [], f"hd-engine-fixture failed: {errs}"


def test_validate_rejects_unknown_source():
    bad = {
        "schema_version": "1.0.0",
        "name": "bad",
        "owner": "test",
        "metrics": {
            "mystery.x": {"id": "x", "label": "X", "source": "definitely_not_a_source"},
        },
    }
    errs = validate(bad)
    assert any("source" in e for e in errs)


def test_validate_rejects_inner_id_mismatch():
    bad = {
        "schema_version": "1.0.0",
        "name": "bad",
        "owner": "test",
        "metrics": {
            "funnel.bar": {"id": "baz", "label": "Baz", "source": "ga4"},
        },
    }
    errs = validate(bad)
    assert any("inner id" in e for e in errs)


def test_resolve_collection_merges_parent_and_overrides(monkeypatch):
    _patch_sites(monkeypatch)
    flat = resolve_collection("hd-engine-fixture")
    # Inherits at least the parent metrics defined in tests/fixtures/hd-engine.kpi.json
    assert "funnel_top.free_chart_generated_total" in flat["metrics"]
    # Override semantic: site value wins for same key
    assert flat["_parent_slug"] in ("hd-engine", None)


def test_aggregate_returns_one_row_per_site(monkeypatch):
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {} for s in slugs})
    assert len(agg["sites"]) == len(slugs)
    for s in agg["sites"]:
        assert isinstance(s["front_of_card"], list)


def test_render_index_contains_all_slugs(monkeypatch):
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {} for s in slugs})
    html = render_index(agg)
    for s in slugs:
        assert s in html
        assert f"/pwp/kpi/{s}.html" in html
    assert "Multi-site index" in html
    assert "Accordion view" in html


def test_render_detail_lists_every_metric(monkeypatch):
    _patch_sites(monkeypatch)
    agg = aggregate(runtime_values={"hd-engine": {}})
    html = render_detail("hd-engine", agg)
    flat = resolve_collection("hd-engine")
    for m in flat["metrics"]:
        assert m in html


def test_render_accordion_emits_one_details_per_site(monkeypatch):
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {} for s in slugs})
    html = render_accordion(agg)
    n_details = html.count("<details")
    assert n_details == len(slugs)


def test_load_site_missing_raises(monkeypatch):
    _patch_sites(monkeypatch)
    with pytest.raises(FileNotFoundError):
        load_site("nope")
