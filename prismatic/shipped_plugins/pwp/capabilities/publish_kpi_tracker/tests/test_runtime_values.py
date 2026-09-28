"""Tests for the runtime values pipeline.

Covers:
  - default_sites_dir() resolves to the canonical plugins/.../sites/ path
  - RuntimeValuesBuilder.build_site reads <slug>.runtime.json snapshots
  - Snapshots win over adapter defaults when both have a value
  - Derived metrics are computed AFTER all non-derived sources
  - Derived formulas use bare ids (e.g. purchase_total / booking_click)
  - Invalid formulas (unknown operands) return None
  - Internal/verifier adapters read from per-source files
  - build_all() walks every registered site deterministically
  - Snapshot subcommand via operator_cli produces correct templates
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.shipped_plugins.pwp.capabilities import publish_kpi_tracker as kpi
from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import runtime_values as rv


# Layout: <PWP_REPO>/prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/tests/
# so PWP_REPO = parents[5]. Use a temp dir for sites_dir so tests don't touch
# the canonical plugin tree.
HERE = Path(__file__).resolve()


def _make_site(sites_dir: Path, slug: str, runtime: dict | None = None,
               internal: dict | None = None, verifier: dict | None = None) -> None:
    """Write a minimal <slug>.kpi.json + optional sidecar files."""
    # Use a copy of the canonical hd-engine.kpi.json fixture (covers
    # both ga4/stripe/derived sources) so the pipeline has metrics to
    # dispatch against.
    fixture = HERE.parent / "fixtures" / "hd-engine.kpi.json"
    target = sites_dir / f"{slug}.kpi.json"
    shutil.copy(fixture, target)
    if runtime is not None:
        (sites_dir / f"{slug}.runtime.json").write_text(
            json.dumps(runtime, indent=2), encoding="utf-8"
        )
    if internal is not None:
        (sites_dir / f"{slug}.internal.json").write_text(
            json.dumps(internal, indent=2), encoding="utf-8"
        )
    if verifier is not None:
        (sites_dir / f"{slug}.verifier.json").write_text(
            json.dumps(verifier, indent=2), encoding="utf-8"
        )


@pytest.fixture
def sites_dir(tmp_path: Path):
    """Each test gets an isolated sites directory with one hd-engine site."""
    d = tmp_path / "sites"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={
        "funnel_top.free_chart_generated_total": 184,
        "funnel_sanctuary.sanctuary_purchase_total": 7,
        "funnel_buy_report.purchase_total": 21,
    })
    return d


# ── default_sites_dir ──────────────────────────────────────────────────────

def test_default_sites_dir_resolves_to_canonical():
    """The default sites_dir must end at plugins/pwp/.../publish_kpi_tracker/sites."""
    p = rv.default_sites_dir()
    assert p.as_posix().endswith("publish_kpi_tracker/sites"), f"unexpected path: {p}"
    assert p.is_dir(), f"sites dir does not exist: {p}"


def test_default_sites_dir_env_var_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PWP_REPO_ROOT", str(tmp_path))
    p = rv.default_sites_dir()
    assert p == (tmp_path / "plugins" / "pwp" / "capabilities" / "publish_kpi_tracker" / "sites")


# ── RuntimeValuesBuilder.build_site ────────────────────────────────────────

def test_build_site_reads_runtime_snapshot(sites_dir: Path):
    """The snapshot file is the canonical source of runtime values when no
    live credentials are configured."""
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=sites_dir)
    assert values["funnel_top.free_chart_generated_total"] == 184.0
    assert values["funnel_sanctuary.sanctuary_purchase_total"] == 7.0
    assert values["funnel_buy_report.purchase_total"] == 21.0


def test_build_site_returns_empty_for_missing_site(sites_dir: Path):
    builder = rv.RuntimeValuesBuilder(env={})
    assert builder.build_site("nonexistent", sites_dir=sites_dir) == {}


def test_build_site_handles_missing_snapshot(sites_dir: Path, tmp_path: Path):
    """Site exists but no <slug>.runtime.json → adapter falls back to None."""
    d = tmp_path / "no-snap"
    d.mkdir()
    _make_site(d, "hd-engine")  # no runtime.json
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=d)
    # Without live creds and no snapshot, all values are absent.
    assert values == {}


def test_build_site_internal_adapter_reads_sidecar(sites_dir: Path, tmp_path: Path):
    """The internal adapter reads from <slug>.internal.json."""
    d = tmp_path / "with-internal"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={
        "site_hygiene.sitemap_coverage_pct": 0.86,
    }, internal={
        "site_hygiene.sitemap_coverage_pct": 0.95,  # value the adapter returns
    })
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=d)
    # Snapshot wins (it's loaded first); internal adapter is skipped.
    assert values["site_hygiene.sitemap_coverage_pct"] == 0.86


def test_build_site_verifier_adapter_fills_when_no_snapshot(sites_dir: Path, tmp_path: Path):
    """Verifier adapter fills in values when the canonical snapshot doesn't
    have them."""
    d = tmp_path / "with-verifier"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={
        "funnel_top.free_chart_generated_total": 184,  # in snapshot
    }, verifier={
        "site_hygiene.sitemap_coverage_pct": 0.78,  # not in snapshot
    })
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=d)
    assert values["funnel_top.free_chart_generated_total"] == 184.0
    assert values["site_hygiene.sitemap_coverage_pct"] == 0.78


def test_build_site_skips_unknown_source(sites_dir: Path, tmp_path: Path):
    """If a metric has an unknown source, the adapter dispatch skips it."""
    d = tmp_path / "unknown-source"
    d.mkdir()
    _make_site(d, "hd-engine")
    # Patch a metric's source to something we don't handle.
    target = d / "hd-engine.kpi.json"
    coll = json.loads(target.read_text())
    coll["metrics"]["funnel_top.free_chart_generated_total"]["source"] = "no_such_source"
    target.write_text(json.dumps(coll, indent=2))
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=d)
    assert "funnel_top.free_chart_generated_total" not in values


# ── Derived metric computation ────────────────────────────────────────────

def test_compute_derived_simple_division():
    spec = {"id": "rate", "formula": "purchase_total / booking_click"}
    values = {"funnel_booking.purchase_total": 5.0,
              "funnel_booking.booking_click": 100.0}
    out = rv._compute_derived("funnel_booking.rate", spec, values)
    assert out == pytest.approx(0.05)


def test_compute_derived_uses_bare_id_not_full_key():
    """Operands are bare ids (the metric's `id`), not the dotted metric_key."""
    spec = {"id": "rate", "formula": "a + b"}
    values = {"funnel.a": 3.0, "funnel.b": 4.0}
    out = rv._compute_derived("funnel.rate", spec, values)
    assert out == pytest.approx(7.0)


def test_compute_derived_returns_none_for_unknown_operand():
    spec = {"id": "rate", "formula": "missing / booking_click"}
    values = {"funnel_booking.booking_click": 100.0}
    out = rv._compute_derived("funnel_booking.rate", spec, values)
    assert out is None


def test_compute_derived_rejects_unsafe_characters():
    spec = {"id": "rate", "formula": "a + __import__('os').system('echo pwn')"}
    values = {"a": 1.0}
    out = rv._compute_derived("rate", spec, values)
    assert out is None  # underscores in operand names are fine, but `__import__` is a builtin name; eval is sandboxed.


def test_compute_derived_handles_longest_first_substitution():
    """`booking_click` must not be partially substituted as `click`."""
    spec = {"id": "rate", "formula": "booking_click / click"}
    values = {"booking_click": 10.0, "click": 5.0}
    out = rv._compute_derived("rate", spec, values)
    assert out == pytest.approx(2.0)


def test_build_site_computes_derived_metrics(tmp_path: Path, monkeypatch):
    """End-to-end: derived metrics are computed AFTER non-derived sources."""
    d = tmp_path / "derived"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={
        "funnel_buy_report.purchase_total": 25,
        "funnel_top.free_chart_generated_total": 100,
    })
    # derived metric is NOT in snapshot; should be computed.
    coll_path = d / "hd-engine.kpi.json"
    coll = json.loads(coll_path.read_text())
    coll["metrics"]["funnel_buy_report.derived_rate"] = {
        "id": "derived_rate",
        "label": "Derived rate",
        "source": "derived",
        "formula": "purchase_total / free_chart_generated_total",
        "format": "percent",
    }
    coll_path.write_text(json.dumps(coll, indent=2))
    # Patch SITES_DIR so resolve_collection() reads from our tmp dir.
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner
    monkeypatch.setattr(inner, "SITES_DIR", d)
    builder = rv.RuntimeValuesBuilder(env={})
    values = builder.build_site("hd-engine", sites_dir=d)
    assert values["funnel_buy_report.derived_rate"] == pytest.approx(0.25)


# ── build_all ─────────────────────────────────────────────────────────────

def test_build_all_walks_every_site(tmp_path: Path):
    """build_all returns one row per registered site."""
    d = tmp_path / "two-sites"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={"funnel_top.free_chart_generated_total": 1})
    _make_site(d, "active-oahu", runtime={"funnel_booking.booking_click": 2})
    # Patch kpi.SITES_DIR to point at our tmp_path so list_sites() finds them.
    # We can't easily monkeypatch the module-level SITES_DIR across both the
    # kpi module and the runtime_values module, so we work via the explicit
    # sites_dir parameter.
    # The 'list_sites' function reads from kpi.SITES_DIR; if that doesn't
    # include our test fixtures, build_all won't see them. The proper way is
    # to monkeypatch.
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner
    monkey = pytest.MonkeyPatch()
    monkey.setattr(inner, "SITES_DIR", d)
    try:
        builder = rv.RuntimeValuesBuilder(env={})
        out = builder.build_all(sites_dir=d)
        assert "hd-engine" in out
        assert "active-oahu" in out
        assert out["hd-engine"]["funnel_top.free_chart_generated_total"] == 1.0
        assert out["active-oahu"]["funnel_booking.booking_click"] == 2.0
    finally:
        monkey.undo()


def test_build_all_is_deterministic(tmp_path: Path):
    """Re-running build_all with the same inputs produces identical output."""
    d = tmp_path / "deterministic"
    d.mkdir()
    _make_site(d, "hd-engine", runtime={"funnel_top.free_chart_generated_total": 99})
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner
    monkey = pytest.MonkeyPatch()
    monkey.setattr(inner, "SITES_DIR", d)
    try:
        builder = rv.RuntimeValuesBuilder(env={})
        out_a = builder.build_all(sites_dir=d)
        out_b = builder.build_all(sites_dir=d)
        assert out_a == out_b
    finally:
        monkey.undo()


# ── Snapshot subcommand via operator_cli ───────────────────────────────────

def test_snapshot_subcommand_writes_template(tmp_path: Path, monkeypatch):
    """`operator_cli snapshot` writes <slug>.runtime.json with null placeholders."""
    # Use the canonical sites dir but write to tmp_path via --sites-dir.
    cli = Path(__file__).resolve().parents[1] / "operator_cli.py"
    out_dir = tmp_path / "snapshot-out"
    # We have to provide a kpi.json file too so list_sites() sees it. Easiest:
    # copy one of the canonical sites into tmp_path and monkeypatch SITES_DIR.
    src = Path(__file__).resolve().parents[1] / "sites" / "hd-engine.kpi.json"
    dst_sites = tmp_path / "sites"
    dst_sites.mkdir()
    shutil.copy(src, dst_sites / "hd-engine.kpi.json")
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner
    monkeypatch.setattr(inner, "SITES_DIR", dst_sites)
    # Run the CLI
    result = subprocess.run(
        [sys.executable, str(cli), "snapshot", "--sites-dir", str(out_dir)],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[6]),
    )
    assert result.returncode == 0, result.stderr
    # Verify the file
    target = out_dir / "hd-engine.runtime.json"
    assert target.exists(), f"snapshot file not written: {target}"
    body = json.loads(target.read_text())
    assert body  # non-empty
    assert all(v is None for v in body.values()), \
        f"expected null placeholders, got {body}"


# ── Snapshot safety guard ─────────────────────────────────────────────────

def test_snapshot_skips_existing_files_without_force(tmp_path: Path, monkeypatch):
    """Without --force, existing <slug>.runtime.json is preserved verbatim."""
    dst_sites = tmp_path / "sites"
    dst_sites.mkdir()
    shutil.copy(
        Path(__file__).resolve().parents[1] / "sites" / "hd-engine.kpi.json",
        dst_sites / "hd-engine.kpi.json",
    )
    out_dir = tmp_path / "snapshot-out"
    out_dir.mkdir()
    sentinel = "{\"existing\": 1}\n"
    (out_dir / "hd-engine.runtime.json").write_text(sentinel, encoding="utf-8")
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as inner
    monkeypatch.setattr(inner, "SITES_DIR", dst_sites)
    cli = Path(__file__).resolve().parents[1] / "operator_cli.py"
    result = subprocess.run(
        [sys.executable, str(cli), "snapshot", "--sites-dir", str(out_dir)],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[6]),
    )
    assert result.returncode == 0
    assert (out_dir / "hd-engine.runtime.json").read_text() == sentinel


# ── Live adapter stubs ────────────────────────────────────────────────────

def test_ga4_adapter_returns_none_without_credentials():
    out = rv._query_ga4(
        "any", {"event": "x", "source": "ga4"}, {"tracking_property": "G-X"}, env={}
    )
    assert out is None


def test_stripe_adapter_returns_none_without_credentials():
    out = rv._query_stripe(
        "any", {"event": "checkout.session.completed", "source": "stripe"}, {}, env={}
    )
    assert out is None


def test_gsc_adapter_returns_none_without_credentials():
    out = rv._query_gsc(
        "any", {"filter": "sc-domain:x.com", "source": "gsc"}, {}, env={}
    )
    assert out is None


def test_telegram_adapter_returns_none_without_url():
    out = rv._query_telegram("any", {"source": "telegram"}, {}, env={})
    assert out is None


def test_internal_adapter_returns_none_without_sidecar(tmp_path: Path):
    out = rv._query_internal("any", {"source": "internal"}, {}, env={
        "_sites_dir": str(tmp_path), "_slug": "x",
    })
    assert out is None


def test_verifier_adapter_returns_none_without_sidecar(tmp_path: Path):
    out = rv._query_verifier("any", {"source": "verifier"}, {}, env={
        "_sites_dir": str(tmp_path), "_slug": "x",
    })
    assert out is None


def test_internal_adapter_reads_sidecar(tmp_path: Path):
    d = tmp_path / "sites-internal"
    d.mkdir()
    (d / "x.internal.json").write_text('{"any_metric": 42}', encoding="utf-8")
    out = rv._query_internal("any_metric", {"source": "internal"}, {}, env={
        "_sites_dir": str(d), "_slug": "x",
    })
    assert out == 42.0