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

import plugins.pwp.capabilities.publish_kpi_tracker as kpi_mod
import pytest
from plugins.pwp.capabilities.publish_kpi_tracker import (
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


# ── New: per-site-row layout + value formatting ─────────────────────────────
def test_render_index_uses_per_site_row_layout(monkeypatch):
    """The multi-site index must render one section.site-row per site,
    each containing a card-grid. This is the smallest visual unit that
    closes the loop (per-site rows replace the old summary table)."""
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {} for s in slugs})
    html = render_index(agg)
    n_rows = html.count('class="pwp-kpi-site-row"')
    assert n_rows == len(slugs), f"expected {len(slugs)} per-site rows, got {n_rows}"
    # Each row should contain a card grid (even if empty).
    n_grids = html.count('class="pwp-kpi-card-grid"')
    assert n_grids == len(slugs)


def test_render_index_card_grid_renders_em_dash_for_missing_values(monkeypatch):
    """When runtime_values is empty, each card shows a `—` placeholder
    instead of collapsing the cell to an empty span. The grid stays
    visible."""
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {} for s in slugs})
    html = render_index(agg)
    # No front-of-card metric should ever silently collapse. Either we
    # render a `<div class="pwp-kpi-card-value">—</div>` placeholder, or
    # the per-site row explicitly says "No front-of-card metrics
    # registered."
    n_em_dash_placeholders = html.count('<div class="pwp-kpi-card-value">—</div>')
    n_no_metrics_msg = html.count("No front-of-card metrics registered")
    assert n_em_dash_placeholders + n_no_metrics_msg > 0, (
        "expected at least one `—` placeholder or a no-metrics note"
    )


def test_render_index_is_self_rendering_and_deterministic(monkeypatch):
    """Same aggregated data must always produce byte-identical HTML."""
    _patch_sites(monkeypatch)
    slugs = list_sites()
    agg = aggregate(runtime_values={s: {"fake": 42} for s in slugs})
    # Phase 4.2: render_index injects a CSRF nonce into the modal HTML.
    # Tests pass a stable token so the rest of the HTML stays byte-identical.
    html_a = render_index(agg, csrf_token="test-csrf-stable")
    html_b = render_index(agg, csrf_token="test-csrf-stable")
    assert html_a == html_b, "render_index is not deterministic"


def test_render_index_with_runtime_values_shows_real_numbers(monkeypatch):
    """When runtime_values are provided, the cards show formatted values,
    not placeholders."""
    _patch_sites(monkeypatch)
    # Find a front-of-card metric for hd-engine to test against.
    flat = resolve_collection("hd-engine")
    foc_metric_id = next(
        (mid for mid, m in flat["metrics"].items() if m.get("front_of_card")),
        None,
    )
    if foc_metric_id is None:
        pytest.skip("hd-engine fixture has no front_of_card metrics")
    agg = aggregate(runtime_values={"hd-engine": {foc_metric_id: 1234}})
    html = render_index(agg)
    # The value should appear in some form (1234 → "1,234" or "1234")
    assert "1,234" in html or "1234" in html


def test_format_value_percent_multiplies_by_100():
    """The percent format multiplies by 100 because metrics store fractions.
    0.0638 (fraction) → 6.38% (display). This is the contract used by the
    canonical active-oahu.kpi.json formula `booking_complete / booking_click`.
    """
    from plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
        _format_value,
    )

    assert _format_value(0.0638, "percent") == "6.38%"
    assert _format_value(0.0, "percent") == "0.00%"
    assert _format_value(1.0, "percent") == "100.00%"
    # Edge: very small fraction.
    assert _format_value(0.000123, "percent") == "0.01%"


def test_format_value_percent_is_audit_safe():
    """The format is self-consistent: round-trip 0.0638 → "6.38%" parses
    back to 6.38, which divided by 100 gives the original 0.0638.
    A wrong implementation would either fail this round-trip or
    produce a number that's off by 100x."""
    from plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
        _format_value,
    )

    raw = 0.0638
    rendered = _format_value(raw, "percent")
    # Rendered must contain "%", must NOT contain "0.06%" (the old bug).
    assert "%" in rendered
    assert "0.06%" not in rendered, f"old bug regressed: {rendered!r}"
    # Parse the displayed percent back to a number and confirm it's
    # within 0.01 of raw * 100.
    numeric = float(rendered.rstrip("%"))
    assert abs(numeric - raw * 100) < 0.01


def test_format_value_handles_missing():
    from plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
        _format_value,
    )

    assert _format_value(None) == "—"
    assert _format_value(None, "percent") == "—"
    assert _format_value(None, "currency") == "—"


def test_format_value_renders_deterministic_outputs():
    from plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
        _format_value,
    )

    # Same input -> same output (deterministic).
    a = _format_value(1234, "number")
    b = _format_value(1234, "number")
    assert a == b
    # Percent formatting: stored as fraction, multiplied by 100 for display.
    # 0.0638 (fraction) → "6.38%" (display). NOT "0.06%".
    assert _format_value(0.0638, "percent") == "6.38%"
    assert _format_value(0.1234, "percent") == "12.34%"
    assert _format_value(1.0, "percent") == "100.00%"
    assert _format_value(0.5, "percent") == "50.00%"
    # Currency formatting.
    assert _format_value(1234.5, "currency") == "$1,234.50"
    # Duration formatting.
    assert _format_value(45, "duration") == "45s"


def test_format_value_falls_back_to_str_on_typeerror():
    from plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
        _format_value,
    )

    # Strings pass through.
    assert _format_value("abc") == "abc"

    # Garbage that can't be coerced falls back to str(value).
    class Weird:
        def __repr__(self):
            return "<weird>"

    assert _format_value(Weird()) == "<weird>"
