"""Tests for the site_builder module.

Covers:
  - build_site_collection validates the slug and per-metric schema
  - write_site_collection writes JSON to <slug>.kpi.json in the target dir
  - the produced file passes the canonical validate() function
  - build_sites_from_inventory writes one file per inventory entry
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import plugins.pwp.capabilities.publish_kpi_tracker as kpi  # noqa: E402
from plugins.pwp.capabilities.publish_kpi_tracker import (  # noqa: E402
    site_builder as sb,
)


# Resolve relative to this test file so the test suite is portable.
SITES_DIR = Path(__file__).resolve().parent / "fixtures" / "sites_out"


@pytest.fixture(autouse=True)
def _clean_outdir():
    if SITES_DIR.exists():
        for p in SITES_DIR.iterdir():
            p.unlink()
    SITES_DIR.mkdir(parents=True, exist_ok=True)


def test_build_site_collection_minimal_validates():
    coll = sb.build_site_collection(
        slug="tmp-sku",
        domain="example.test",
        name="tmp-funnel",
        tracking_property="G-TEST123",
        metric_specs=[
            {"id": "click_total", "label": "Clicks", "source": "ga4",
             "event": "tmp_click", "front_of_card": True},
            {"id": "purchase_total", "label": "Purchases", "source": "stripe",
             "event": "checkout.session.completed",
             "filter": "metadata.funnel == tmp", "front_of_card": True},
            {"id": "conv_rate", "label": "Conversion", "source": "derived",
             "formula": "purchase_total / click_total", "format": "percent"},
        ],
    )
    assert coll["site_slug"] == "tmp-sku"
    assert "tmp-sku" in coll["metrics"]["click_total"]["id"] or "click_total" in coll["metrics"]
    errs = kpi.validate(coll)
    assert errs == [], f"validation errors: {errs}"


def test_build_site_collection_with_extends_prefixes_metric_keys():
    coll = sb.build_site_collection(
        slug="tmpchild",
        domain="child.test",
        name="tmpchild-funnel",
        tracking_property="G-TEST234",
        extends="tmp-parent",
        metric_specs=[
            {"id": "click_total", "label": "Clicks", "source": "ga4",
             "event": "tmp_click"},
        ],
    )
    assert coll["extends"] == "tmp-parent"
    # Site-builder does NOT prefix the parent slug onto the metric key. Inheritance
    # happens at the collection level (resolve_collection) on key collision, so the
    # metric key remains the bare id and must pass the regex `^[a-z0-9._*]+$`.
    assert "click_total" in coll["metrics"]
    errs = kpi.validate(coll)
    assert errs == []


def test_write_site_collection_writes_validated_file():
    target = sb.write_site_collection(
        SITES_DIR,
        slug="tmp-write",
        domain="example.test",
        name="tmp-write-funnel",
        tracking_property="G-TEST345",
        metric_specs=[
            {"id": "click_total", "label": "Clicks", "source": "ga4",
             "event": "tmp_click", "front_of_card": True},
        ],
    )
    assert target.exists()
    obj = json.loads(target.read_text())
    assert obj["site_slug"] == "tmp-write"
    errs = kpi.validate(obj)
    assert errs == []


def test_build_site_collection_rejects_invalid_slug():
    with pytest.raises(ValueError, match="invalid slug"):
        sb.build_site_collection(
            slug="Bad Slug!",
            domain="x.test",
            name="x",
            metric_specs=[{"id":"a","label":"A","source":"ga4"}],
        )


def test_build_site_collection_rejects_invalid_source():
    with pytest.raises(ValueError, match="source"):
        sb.build_site_collection(
            slug="ok-slug",
            domain="x.test",
            name="x",
            metric_specs=[{"id":"a","label":"A","source":"definitely-not"}],
        )


def test_build_sites_from_inventory_writes_one_file_per_site():
    inventory = [
        {"slug": "inv-1", "domain": "i1.test", "name": "i1-funnel", "tracking_property": "G-INV1"},
        {"slug": "inv-2", "domain": "i2.test", "name": "i2-funnel", "tracking_property": "G-INV2"},
    ]
    specs = {
        "inv-1": [{"id":"a","label":"A","source":"ga4","event":"a","front_of_card":True}],
        "inv-2": [{"id":"b","label":"B","source":"ga4","event":"b","front_of_card":True}],
    }
    paths = sb.build_sites_from_inventory(inventory, SITES_DIR, per_site_metric_specs=specs)
    assert sorted(p.name for p in paths) == ["inv-1.kpi.json", "inv-2.kpi.json"]
    for p in paths:
        errs = kpi.validate(json.loads(p.read_text()))
        assert errs == [], f"validation errors in {p.name}: {errs}"
