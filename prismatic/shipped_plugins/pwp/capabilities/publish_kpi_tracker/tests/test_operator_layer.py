"""Integration tests for the publish-kpi-tracker operator layer.

Covers:
  - build_dashboard writes 4 surfaces + a dashboard_data.json snapshot
  - build_site_collection validates a registered site
  - build_all_site_summaries picks a canonical headline per site
  - read_runtime_values returns {} when path is missing
  - publish_publish_kpi_dashboard is the public alias of build_dashboard

Run with:  pytest plugins/pwp/capabilities/publish_kpi_tracker/tests/test_operator_layer.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plugins.pwp.capabilities.publish_kpi_tracker import (
    build_all_site_summaries,
    build_dashboard,
    build_site_collection,
    list_sites,
    publish_publish_kpi_dashboard,
    read_runtime_values,
)


def test_sites_are_registered():
    sites = list_sites()
    assert "hd-engine" in sites, f"hd-engine missing: {sites}"
    assert "active-oahu" in sites, f"active-oahu missing: {sites}"


def test_build_all_site_summaries_returns_published_website_summaries():
    summaries = build_all_site_summaries(runtime_values={
        "hd-engine": {"funnel_top.free_chart_generated_total": 298},
        "active-oahu": {"funnel_booking.booking_click": 412},
    })
    by_slug = {s["slug"]: s for s in summaries}
    assert "hd-engine" in by_slug
    assert by_slug["hd-engine"]["metric_count"] >= 1
    assert by_slug["hd-engine"]["headline_metric_label"] is not None
    # Runtime overlay should propagate to headline_value.
    assert by_slug["hd-engine"]["headline_value"] == 298
    assert by_slug["active-oahu"]["headline_value"] == 412


def test_build_site_collection_validates():
    out = build_site_collection("hd-engine")
    assert out["site_slug"] == "hd-engine"
    assert "_runtime_overrides" in out

    with pytest.raises((ValueError, FileNotFoundError), match="(validation|missing)"):
        build_site_collection("nonexistent-site")


def test_read_runtime_values_returns_empty_for_missing_path(tmp_path):
    assert read_runtime_values(str(tmp_path / "absent.json")) == {}
    assert read_runtime_values(None) == {}


def test_read_runtime_values_loads_existing(tmp_path):
    target = tmp_path / "rt.json"
    target.write_text(json.dumps({"hd-engine": {"funnel_top.free_chart_generated_total": 9}}))
    assert read_runtime_values(str(target)) == {"hd-engine": {"funnel_top.free_chart_generated_total": 9}}


def test_build_dashboard_writes_surfaces_and_snapshot(tmp_path):
    runtime = {
        "hd-engine": {
            "funnel_top.free_chart_generated_total": 123,
            "funnel_sanctuary.sanctuary_purchase_total": 7,
        },
        "active-oahu": {
            "funnel_booking.booking_click": 200,
            "funnel_booking.booking_conversion_rate": 0.42,
        },
    }
    out = build_dashboard(
        publish_root=str(tmp_path),
        runtime_values=runtime,
        window="last7d",
    )
    # Manifest keys
    for k in ("sites", "window", "output_dir", "snapshot_path"):
        assert k in out, f"missing {k!r} in {out}"
    assert out["window"] == "last7d"
    assert out["sites"] == sorted(out["sites"])
    assert "hd-engine" in out["sites"] and "active-oahu" in out["sites"]
    # Files exist
    root = Path(out["output_dir"])
    for fname in ("index.html", "accordion.html", "pwp-publish-kpi.css", "dashboard_data.json"):
        assert (root / fname).exists(), f"missing {fname}"
    for slug in out["sites"]:
        assert (root / f"{slug}.html").exists(), f"missing {slug}.html"
    # Snapshot round-trips the runtime values.
    snap = json.loads((root / "dashboard_data.json").read_text())
    assert snap["window"] == "last7d"
    by_slug = {s["slug"]: s for s in snap["sites"]}
    assert by_slug["hd-engine"]["front_of_card"][0]["value"] == 123
    assert by_slug["active-oahu"]["front_of_card"][0]["value"] == 200
    # HTML actually references the runtime values.
    index_html = (root / "index.html").read_text()
    assert "412" in index_html or "0.42" in index_html or "200" in index_html, (
        "index.html didn't surface any front-of-card values"
    )


def test_publish_publish_kpi_dashboard_is_alias_of_build_dashboard(tmp_path):
    out = publish_publish_kpi_dashboard(
        publish_root=str(tmp_path),
        runtime_values={"hd-engine": {"funnel_top.free_chart_generated_total": 1}},
    )
    assert Path(out["output_dir"]) / "dashboard_data.json" in [Path(out["snapshot_path"])] or Path(out["snapshot_path"]).exists()
