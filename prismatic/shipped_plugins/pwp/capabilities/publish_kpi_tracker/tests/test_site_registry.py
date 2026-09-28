"""Tests for the site-registry loader + migration operator.

Covers:
  - load_registry reads + parses config/seo_sites.json
  - validate_registry_shape rejects bad slugs, missing name/domain, etc.
  - iter_sites resolves ga4_measurement_id from env-var when literal is missing
  - iter_metric_specs_for_site merges default + per-site specs (override semantics)
  - run() emits one *.kpi.json per enabled site, with dry_run support
  - generated files validate through the canonical kpi.validate() function
  - disabled sites are skipped
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from prismatic.shipped_plugins.pwp.capabilities import publish_kpi_tracker as kpi  # noqa: E402
from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import (  # noqa: E402
    operator_migrate as mig,
    pwp_kpi_site_registry as reg,
)
from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.site_builder import (  # noqa: E402
    write_site_collection,
)


FIXTURES = Path(
    __file__
).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    # The test registry expects this env name to be set so the active-oahu site can
    # resolve its tracking_property. The disabled-site has its value inline.
    monkeypatch.setenv("TEST_AOT_MEAS_ID", "G-TESTAOAO01")
    monkeypatch.delenv("PWP_DEFAULT_GA4_MEASUREMENT_ID", raising=False)


def test_load_registry_reads_and_parses():
    doc = reg.load_registry(FIXTURES / "test_registry.json")
    assert doc["version"] == 1
    slugs = [s["slug"] for s in doc["sites"]]
    assert slugs == ["active-oahu", "disabled-site"]


def test_validate_registry_shape_rejects_bad_slug():
    bad = {"version": 1, "sites": [{"slug": "Bad Slug", "name": "x", "domain": "x"}]}
    errs = reg.validate_registry_shape(bad, reg.load_schema(FIXTURES / "test_registry.json"))
    assert any("bad slug" in e for e in errs)


def test_validate_registry_shape_rejects_duplicate_slug():
    bad = {"version": 1, "sites": [
        {"slug": "dup", "name": "x", "domain": "x.example"},
        {"slug": "dup", "name": "y", "domain": "y.example"},
    ]}
    errs = reg.validate_registry_shape(bad, reg.load_schema(FIXTURES / "test_registry.json"))
    assert any("duplicate slug" in e for e in errs)


def test_iter_sites_resolves_tracking_property_from_env(monkeypatch):
    """GAP-#5: env-var-only GA4 resolution. Every site's tracking_property
    must come from its `ga4_measurement_env` env-var. The static
    `ga4_measurement_id` literal is ignored (forced to None by the v1→v2
    adapter)."""
    monkeypatch.setenv("TEST_AOT_MEAS_ID", "G-TESTAOAO01")
    monkeypatch.setenv("TEST_DISABLED_MEAS_ID", "G-DISABLED01")
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites = list(reg.iter_sites(reg_doc))
    by_slug = {s["slug"]: s for s in sites}
    assert by_slug["active-oahu"]["_tracking_property_resolved"] == "G-TESTAOAO01"
    assert by_slug["active-oahu"]["_tracking_property_source"] == "env"
    assert by_slug["disabled-site"]["_tracking_property_resolved"] == "G-DISABLED01"
    assert by_slug["disabled-site"]["_tracking_property_source"] == "env"


def test_iter_metric_specs_for_site_merges_defaults_and_overrides():
    reg_doc = {
        "version": 1,
        "default_metric_specs": {
            "a": {"id": "a", "label": "A", "source": "ga4", "event": "a"},
            "b": {"id": "b", "label": "B", "source": "ga4", "event": "b"},
        },
        "sites": [
            {"slug": "s1", "name": "S1", "domain": "s1.x",
             "pwp_kpi_metric_specs": {
                "b": {"id": "b", "label": "B (override)", "source": "ga4", "event": "b"},
                "c": {"id": "c", "label": "C", "source": "ga4", "event": "c"},
            }}
        ],
    }
    site = reg_doc["sites"][0]
    specs = reg.iter_metric_specs_for_site(reg_doc, site)
    by_id = {m["id"]: m for m in specs}
    assert by_id["a"]["label"] == "A"                      # default kept
    assert by_id["b"]["label"] == "B (override)"           # site override wins
    assert by_id["c"]["label"] == "C"                      # site-only


def test_site_override_enabled_respects_explicit_disable():
    reg_doc = {"version": 1, "sites": [
        {"slug": "a", "name": "A", "domain": "a.x", "pwp_kpi_override": {"enabled": True}},
        {"slug": "b", "name": "B", "domain": "b.x", "pwp_kpi_override": {"enabled": False}},
        {"slug": "c", "name": "C", "domain": "c.x"},  # no override, defaults to True
    ]}
    assert reg.site_override_enabled(reg_doc, reg_doc["sites"][0]) is True
    assert reg.site_override_enabled(reg_doc, reg_doc["sites"][1]) is False
    assert reg.site_override_enabled(reg_doc, reg_doc["sites"][2]) is True


def test_run_dry_run_does_not_write_but_validates():
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites_dir = FIXTURES / "out_sites"
    if sites_dir.exists():
        for p in sites_dir.iterdir():
            p.unlink()
    try:
        manifest = mig.run(dry_run=True, registry_path=FIXTURES / "test_registry.json",
                           sites_dir=sites_dir)
        # disabled site is skipped
        statuses = {s["slug"]: s.get("status") for s in manifest["sites"]}
        assert statuses["active-oahu"] == "dry_run"
        assert statuses["disabled-site"] == "skipped (disabled)"
        # nothing was written
        assert list(sites_dir.iterdir()) == []
        # In-memory collections still validated
        assert manifest["validation_errors"] == []
    finally:
        if sites_dir.exists():
            for p in sites_dir.iterdir():
                p.unlink()


def test_run_writes_files_for_enabled_sites():
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites_dir = FIXTURES / "out_sites"
    if sites_dir.exists():
        for p in sites_dir.iterdir():
            p.unlink()
    try:
        manifest = mig.run(dry_run=False, registry_path=FIXTURES / "test_registry.json",
                           sites_dir=sites_dir)
        written = {p.name for p in sites_dir.iterdir()}
        assert written == {"active-oahu.kpi.json"}  # disabled-site skipped
        # The active-oahu.kpi.json file passes the canonical validator.
        coll = json.loads((sites_dir / "active-oahu.kpi.json").read_text())
        errs = kpi.validate(coll)
        assert errs == [], f"validation errors: {errs}"
        # tracking_property resolved from env
        assert coll["tracking_property"] == "G-TESTAOAO01"
        # expected_data_layer_events is at the top level (globally_required + site.*)
        assert "booking_click" in coll.get("expected_data_layer_events", [])
        assert coll.get("site", {}).get("expected_dataLayer_events") == ["booking_click", "booking_complete"]
        # The default metric spec sitemap_coverage_pct is inherited via default_metric_specs
        # and merged in via iter_metric_specs_for_site.
        assert "sitemap_coverage_pct" in coll["metrics"]
    finally:
        if sites_dir.exists():
            for p in sites_dir.iterdir():
                p.unlink()


def test_run_idempotent_overwrites_with_same_content():
    """Re-running with the same registry leaves the file unchanged (modulo whitespace)."""
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites_dir = FIXTURES / "out_sites"
    if sites_dir.exists():
        for p in sites_dir.iterdir():
            p.unlink()
    try:
        mig.run(dry_run=False, registry_path=FIXTURES / "test_registry.json",
                sites_dir=sites_dir)
        first = (sites_dir / "active-oahu.kpi.json").read_text()
        mig.run(dry_run=False, registry_path=FIXTURES / "test_registry.json",
                sites_dir=sites_dir)
        second = (sites_dir / "active-oahu.kpi.json").read_text()
        assert first == second, "second run produced different content"
    finally:
        if sites_dir.exists():
            for p in sites_dir.iterdir():
                p.unlink()


def test_run_skips_existing_files_without_force(tmp_path: Path):
    """The operator must NOT overwrite a curated per-site *.kpi.json
    unless --force is passed. The status should report `skipped (exists)`
    and the file bytes must be unchanged."""
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    target = sites_dir / "active-oahu.kpi.json"
    sentinel = "CURATED_CONTENT_MARKER_DO_NOT_OVERWRITE\n"
    target.write_text(sentinel, encoding="utf-8")

    manifest = mig.run(
        dry_run=False,
        registry_path=FIXTURES / "test_registry.json",
        sites_dir=sites_dir,
    )
    statuses = {s["slug"]: s["status"] for s in manifest["sites"]}
    assert statuses.get("active-oahu") == "skipped (exists)"
    # The on-disk bytes are exactly what we wrote — no clobber.
    assert target.read_text(encoding="utf-8") == sentinel


def test_run_force_overwrites_existing_files(tmp_path: Path):
    """With force=True, the operator overwrites the existing file with
    the freshly-built collection."""
    reg_doc = reg.load_registry(FIXTURES / "test_registry.json")
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    target = sites_dir / "active-oahu.kpi.json"
    target.write_text("OLD\n", encoding="utf-8")

    manifest = mig.run(
        dry_run=False,
        registry_path=FIXTURES / "test_registry.json",
        sites_dir=sites_dir,
        force=True,
    )
    statuses = {s["slug"]: s["status"] for s in manifest["sites"]}
    assert statuses.get("active-oahu") == "written"
    # The on-disk bytes now contain JSON, not the OLD marker.
    body = target.read_text(encoding="utf-8")
    assert "OLD" not in body
    assert "schema_version" in body


def test_pwp_repo_resolves_via_symlink_path():
    """The PWP_REPO must resolve to the real repo root even when the
    module is loaded via the plugins/ symlink (parents[5] would be one
    level too high; the walk-to-seo_sites.json method is robust)."""
    # If we got here without raising, the module-level REPO_ROOT / PWP_REPO
    # resolution succeeded. Verify the path actually contains the registry.
    assert (reg.REPO_ROOT / "config" / "seo_sites.json").is_file()
    assert (mig.PWP_REPO / "config" / "seo_sites.json").is_file()
    assert reg.REPO_ROOT == mig.PWP_REPO


# ── New: --merge mode for incremental curation ─────────────────────────────
def test_run_merge_adds_registry_metrics_to_existing_file(tmp_path: Path):
    """--merge adds registry metrics not already in the curated file,
    without overwriting curated entries."""
    # Build a curated file with one custom metric.
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    curated = {
        "schema_version": "1.0",
        "name": "test-fixture",
        "owner": "ned",
        "site_slug": "active-oahu",
        "domain": "activeoahutours.com",
        "tracking_property": "G-CURATED",
        "metrics": {
            "funnel_booking.curated_metric": {
                "id": "curated_metric",
                "label": "Curated Metric",
                "source": "ga4",
                "event": "curated_event",
                "format": "number",
                "front_of_card": True,
            },
        },
    }
    (sites_dir / "active-oahu.kpi.json").write_text(
        json.dumps(curated, indent=2), encoding="utf-8"
    )
    # Run merge against the test_registry.json fixture. The registry's
    # default_metric_specs has metric specs that aren't in the curated
    # file; the curated metric must survive untouched.
    manifest = mig.run(
        dry_run=False,
        registry_path=FIXTURES / "test_registry.json",
        sites_dir=sites_dir,
        merge=True,
    )
    statuses = {s["slug"]: s for s in manifest["sites"]}
    site_status = statuses.get("active-oahu", {})
    assert site_status.get("status") == "merged", site_status
    added = site_status.get("added_metrics", [])
    assert len(added) > 0, "expected registry metrics to be added"

    # The curated metric must still be present.
    body = json.loads((sites_dir / "active-oahu.kpi.json").read_text())
    assert "funnel_booking.curated_metric" in body["metrics"]
    assert body["metrics"]["funnel_booking.curated_metric"]["label"] == "Curated Metric"
    # tracking_property is a top-level field that the curator owns;
    # merge mode does NOT touch it.
    assert body["tracking_property"] == "G-CURATED"


def test_run_merge_does_not_overwrite_curated_metric():
    """Even if the registry and curated file both have the same metric_key,
    the curated version wins."""
    from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.operator_migrate import (
        _merge_into_existing,
    )
    existing = {
        "metrics": {
            "funnel_booking.booking_click": {
                "id": "booking_click",
                "label": "Curated Label",
                "source": "ga4",
            }
        }
    }
    registry_coll = {
        "metrics": {
            "funnel_booking.booking_click": {
                "id": "booking_click",
                "label": "Registry Label",
                "source": "ga4",
            },
            "funnel_booking.new_event": {
                "id": "new_event",
                "label": "New Event",
                "source": "ga4",
            },
        }
    }
    # Use a tmp file because _merge_into_existing takes a Path.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(existing, f)
        tmp_path = f.name
    try:
        merged, added = _merge_into_existing(Path(tmp_path), registry_coll)
        assert merged["metrics"]["funnel_booking.booking_click"]["label"] == "Curated Label"
        assert "funnel_booking.new_event" in merged["metrics"]
        assert added == ["funnel_booking.new_event"]
    finally:
        Path(tmp_path).unlink()


def test_run_merge_dry_run_does_not_write():
    """--merge --dry-run reports would-be changes without writing."""
    sites_dir = tmp_path = __import__("pathlib").Path("/tmp") / "kpi-merge-dry-run"
    sites_dir.mkdir(exist_ok=True)
    # Use a curated file that has one metric the registry doesn't have.
    curated = {
        "schema_version": "1.0",
        "name": "test-fixture",
        "owner": "ned",
        "site_slug": "active-oahu",
        "domain": "activeoahutours.com",
        "tracking_property": "G-CURATED",
        "metrics": {
            "funnel_booking.curated_only": {
                "id": "curated_only",
                "label": "Curated Only",
                "source": "ga4",
                "event": "curated_only",
            }
        },
    }
    (sites_dir / "active-oahu.kpi.json").write_text(
        json.dumps(curated, indent=2), encoding="utf-8"
    )
    try:
        before = (sites_dir / "active-oahu.kpi.json").read_text()
        manifest = mig.run(
            dry_run=True,
            registry_path=FIXTURES / "test_registry.json",
            sites_dir=sites_dir,
            merge=True,
        )
        statuses = {s["slug"]: s for s in manifest["sites"]}
        site_status = statuses.get("active-oahu", {})
        assert site_status.get("status") == "dry_run_merge"
        # File must not be modified.
        after = (sites_dir / "active-oahu.kpi.json").read_text()
        assert before == after
    finally:
        # Clean up.
        if sites_dir.exists():
            import shutil
            shutil.rmtree(sites_dir)
