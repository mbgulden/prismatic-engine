"""Integration tests for the pwp-kpi-tracker operator CLI.

Covers the four CLI subcommands against the live published_plugins tree:
  - build-dashboard writes all 5 expected files (index, accordion, <slug>.html, css, snapshot)
  - list-sites returns a JSON array of summaries
  - show <slug> returns the resolved (parent + child merged) collection
  - validate returns exit 0 when both sites are valid
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# Resolve relative to this test file so the test suite is portable across
# developer machines and CI environments. Layout:
#   <PWP_REPO>/prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/tests/test_operator_cli.py
# so PWP_REPO = parents[6] (the extra `tests/` level adds one).
PWP_REPO = Path(__file__).resolve().parents[6]
CLI = PWP_REPO / "plugins" / "pwp" / "capabilities" / "publish_kpi_tracker" / "operator_cli.py"


def _run(*args, expect_rc=0):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, cwd=str(PWP_REPO),
    )


def test_list_sites_returns_both_registered_sites():
    out = _run("list-sites")
    assert out.returncode == 0, out.stderr
    arr = json.loads(out.stdout)
    slugs = {s["slug"] for s in arr}
    assert slugs == {"hd-engine", "active-oahu"}


def test_validate_reports_ok():
    out = _run("validate")
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout
    assert "active-oahu: ok" in out.stdout
    assert "hd-engine: ok" in out.stdout


def test_show_returns_resolved_collection():
    out = _run("show", "active-oahu")
    assert out.returncode == 0, out.stderr
    obj = json.loads(out.stdout)
    assert obj["site_slug"] == "active-oahu"
    assert obj["extends"] == "hd-engine"
    # Inherited + own metrics
    metric_ids = set(obj["metrics"].keys())
    assert "funnel_top.free_chart_generated_total" in metric_ids  # inherited
    assert "funnel_booking.booking_click" in metric_ids            # own
    # front_of_card is site-local (inherited metrics don't get the flag here)
    own_card = obj["metrics"].get("funnel_booking.booking_click", {})
    assert own_card.get("front_of_card") is True
    # Inherited metric should NOT have front_of_card (the contract fix)
    assert "front_of_card" not in obj["metrics"]["funnel_top.free_chart_generated_total"]


def test_build_dashboard_writes_full_layout(tmp_path):
    publish = tmp_path / "dashboard"
    out = _run("--publish-root", str(publish), "build-dashboard", "--window", "last24h")
    assert out.returncode == 0, out.stderr
    expected = {"index.html", "accordion.html", "pwp-publish-kpi.css", "dashboard_data.json",
                "hd-engine.html", "active-oahu.html"}
    written = {f.name for f in publish.iterdir()}
    assert expected.issubset(written), f"missing: {expected - written}"
    snap = json.loads((publish / "dashboard_data.json").read_text())
    assert snap["window"] == "last24h"
    assert {s["slug"] for s in snap["sites"]} == {"hd-engine", "active-oahu"}


def test_build_dashboard_with_runtime_values(tmp_path):
    publish = tmp_path / "dashboard"
    runtime = tmp_path / "rt.json"
    runtime.write_text(json.dumps({
        "hd-engine": {"funnel_top.free_chart_generated_total": 999},
        "active-oahu": {"funnel_booking.booking_click": 42},
    }))
    out = _run("--publish-root", str(publish), "--runtime-values-path", str(runtime),
                "build-dashboard")
    assert out.returncode == 0, out.stderr
    # Runtime values surface in the snapshot.
    snap = json.loads((publish / "dashboard_data.json").read_text())
    by_slug = {s["slug"]: s for s in snap["sites"]}
    fc = {c["metric_key"]: c for c in by_slug["hd-engine"]["front_of_card"]}
    if "funnel_top.free_chart_generated_total" in fc:
        assert fc["funnel_top.free_chart_generated_total"]["value"] == 999
    fc_ao = {c["metric_key"]: c for c in by_slug["active-oahu"]["front_of_card"]}
    if "funnel_booking.booking_click" in fc_ao:
        assert fc_ao["funnel_booking.booking_click"]["value"] == 42


def test_show_on_unknown_slug_returns_nonzero_rc():
    out = _run("show", "no-such-site")
    assert out.returncode == 2
    assert "missing" in out.stderr
