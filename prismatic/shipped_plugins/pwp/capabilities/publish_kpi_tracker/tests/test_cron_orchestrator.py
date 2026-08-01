"""Tests for cron_orchestrator — GAP-#7 unified cron orchestration.

The orchestrator walks the registry, dispatches the per-site launcher
for each site whose `delivery_cadence` matches the requested kind, and
loads per-site env vars from each site's `share_targets` block.

These tests cover:
  - cadence matching (string, list, missing)
  - share-targets env-var loading
  - disabled-site skipping
  - resolve_collection failure handling
  - manifest shape
  - error paths (unknown kind, missing launcher)
  - subprocess invocation (using a stub launcher)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from plugins.pwp.capabilities.publish_kpi_tracker import (
    cron_orchestrator as orch,
)
from plugins.pwp.capabilities.publish_kpi_tracker import (
    publish_kpi_tracker as kpi_mod,
)

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


def _patch_sites(monkeypatch, sites_dir: Path) -> None:
    """Patch both kpi_mod.SITES_DIR and inner module SITES_DIR."""
    inner = sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker"]
    monkeypatch.setattr(kpi_mod, "SITES_DIR", sites_dir, raising=True)
    monkeypatch.setattr(inner, "SITES_DIR", sites_dir, raising=True)


def _write_site(sites_dir: Path, slug: str, coll: dict) -> Path:
    target = sites_dir / f"{slug}.kpi.json"
    target.write_text(json.dumps(coll, indent=2), encoding="utf-8")
    return target


def _make_collection(slug: str, *, cadence, share_targets: dict) -> dict:
    return {
        "schema_version": "1.0",
        "site": slug,
        "site_slug": slug,
        "name": slug.title(),
        "owner": "ned",
        "domain": f"{slug}.example",
        "tracking_property": "G-TEST000000",
        "extends": None,
        "delivery_cadence": cadence,
        "share_targets": share_targets,
        "metrics": {
            "m1": {"id": "m1", "label": "M1", "source": "ga4", "event": "m1",
                   "format": "number", "front_of_card": True},
        },
        "front_of_card": ["m1"],
    }


def test_cadence_matches_string() -> None:
    flat = {"delivery_cadence": "daily"}
    assert orch._cadence_matches(flat, "daily") is True
    assert orch._cadence_matches(flat, "weekly") is False


def test_cadence_matches_list() -> None:
    flat = {"delivery_cadence": ["daily", "weekly"]}
    assert orch._cadence_matches(flat, "daily") is True
    assert orch._cadence_matches(flat, "weekly") is True
    assert orch._cadence_matches(flat, "monthly") is False


def test_cadence_matches_missing_opts_in_for_all() -> None:
    """Backward compat: sites without delivery_cadence are dispatched for any kind."""
    assert orch._cadence_matches({}, "daily") is True
    assert orch._cadence_matches({}, "monthly") is True


def test_cadence_matches_dict_shape() -> None:
    """`delivery_cadence` can also be a dict keyed by kind, e.g.
    `{"daily": {...}, "weekly": {...}}`. Used by the canonical
    active-oahu.kpi.json and hd-engine.kpi.json curated files."""
    flat = {"delivery_cadence": {"daily": {"kind": "daily"}, "weekly": {"kind": "weekly"}}}
    assert orch._cadence_matches(flat, "daily") is True
    assert orch._cadence_matches(flat, "weekly") is True
    assert orch._cadence_matches(flat, "monthly") is False


def test_resolve_share_targets_env_loads_from_environment(monkeypatch) -> None:
    """Only env vars that exist in os.environ are added to the dispatch env."""
    flat = {
        "share_targets": {
            "google_sheet_id_env": "AOT_KPI_SHEET_ID",
            "credential_file_env": "AOT_KPI_SA_JSON",
            "email_to_env": "AOT_KPI_EMAIL_TO",
        }
    }
    monkeypatch.setenv("AOT_KPI_SHEET_ID", "sheet-123")
    monkeypatch.setenv("AOT_KPI_SA_JSON", "/tmp/sa.json")
    # AOT_KPI_EMAIL_TO intentionally unset
    env = orch._resolve_share_targets_env("active-oahu", flat)
    assert env["AOT_KPI_SHEET_ID"] == "sheet-123"
    assert env["AOT_KPI_SA_JSON"] == "/tmp/sa.json"
    assert "AOT_KPI_EMAIL_TO" not in env


def test_run_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown cron kind"):
        orch.run(kind="hourly")


def test_run_missing_launcher_raises(tmp_path: Path, monkeypatch) -> None:
    """If neither PWP_KPI_CRON_LAUNCHER nor HDE_KPI_REPO_ROOT is set,
    the orchestrator raises FileNotFoundError pointing to the env vars."""
    monkeypatch.delenv("PWP_KPI_CRON_LAUNCHER", raising=False)
    monkeypatch.delenv("HDE_KPI_REPO_ROOT", raising=False)
    with pytest.raises(FileNotFoundError, match="PWP_KPI_CRON_LAUNCHER"):
        orch._resolve_launcher()


def test_run_walks_registry_and_dispatches_via_stub(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: stub launcher emits a marker JSON; orchestrator captures it.

    The stub launcher is a tiny inline script that writes
    `{"slug": "$PWP_KPI_SLUG", "kind": "$PWP_KPI_KIND"}` to stdout and
    exits 0. The orchestrator captures stdout and reports status:
    dispatched for each site that matches the cadence.
    """
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    _patch_sites(monkeypatch, sites_dir)
    _write_site(sites_dir, "active-oahu",
                _make_collection("active-oahu", cadence=["daily", "weekly"],
                                 share_targets={"google_sheet_id_env": "AOT_KPI_SHEET_ID"}))
    _write_site(sites_dir, "hd-engine",
                _make_collection("hd-engine", cadence="monthly",
                                 share_targets={"google_sheet_id_env": "HDE_KPI_SHEET_ID"}))
    _write_site(sites_dir, "disabled",
                _make_collection("disabled", cadence="daily",
                                 share_targets={}))
    # Patch iter_sites to yield all three sites regardless of the
    # fixture registry's content (the fixture only has active-oahu and
    # disabled-site). This isolates the orchestrator test from the
    # registry fixture's content.
    def _fake_iter_sites(registry):
        yield {"slug": "active-oahu", "name": "Active Oahu",
               "domain": "activeoahutours.com",
               "pwp_kpi_override": {"enabled": True},
               "ga4_measurement_env": "AOT_KPI_MEAS_ID"}
        yield {"slug": "hd-engine", "name": "HDE",
               "domain": "humandesignengine.com",
               "pwp_kpi_override": {"enabled": True},
               "ga4_measurement_env": "HDE_KPI_MEAS_ID"}
        yield {"slug": "disabled", "name": "Disabled",
               "domain": "disabled.example",
               "pwp_kpi_override": {"enabled": False},
               "ga4_measurement_env": "DISABLED_MEAS_ID"}
    monkeypatch.setattr(
        "plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator.iter_sites",
        _fake_iter_sites,
    )
    monkeypatch.setenv("AOT_KPI_SHEET_ID", "sheet-aot")
    monkeypatch.setenv("HDE_KPI_SHEET_ID", "sheet-hde")
    monkeypatch.setattr(
        "plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator.site_override_enabled",
        lambda reg, site: site.get("pwp_kpi_override", {}).get("enabled", True),
    )

    # Stub launcher: a tiny inline Python script that prints the slug
    # it was called with and exits 0.
    stub_launcher = tmp_path / "stub_launcher.py"
    stub_launcher.write_text(
        "import json, os\n"
        "print(json.dumps({'slug': os.environ.get('PWP_KPI_SLUG'),\n"
        "                  'kind': os.environ.get('PWP_KPI_KIND')}))",
        encoding="utf-8",
    )

    manifest = orch.run(
        kind="daily",
        publish_root=tmp_path / "runs",
        launcher=stub_launcher,
    )

    statuses = {s["slug"]: s["status"] for s in manifest["sites"]}
    assert statuses.get("active-oahu") == "dispatched"
    assert statuses.get("hd-engine") == "skipped (cadence)"  # monthly only
    assert statuses.get("disabled") == "skipped (disabled)"
    # Active-oahu was dispatched; the stub emitted its slug in stdout.
    aot = next(s for s in manifest["sites"] if s["slug"] == "active-oahu")
    assert aot["cadence_matched"] is True
    assert "AOT_KPI_SHEET_ID" in aot["env_overrides"]
    assert "stdout_tail" in aot


def test_run_handles_resolve_collection_error(tmp_path: Path, monkeypatch) -> None:
    """If a site's <slug>.kpi.json is missing or invalid, the
    orchestrator records an error status instead of crashing."""
    sites_dir = tmp_path / "sites_empty"
    sites_dir.mkdir()
    _patch_sites(monkeypatch, sites_dir)
    # No sites written — resolve_collection will raise FileNotFoundError.
    def _fake_iter_sites(registry):
        yield {"slug": "missing", "name": "Missing",
               "domain": "missing.example",
               "pwp_kpi_override": {"enabled": True},
               "ga4_measurement_env": "MISSING_MEAS_ID"}
    monkeypatch.setattr(
        "plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator.iter_sites",
        _fake_iter_sites,
    )
    monkeypatch.setattr(
        "plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator.site_override_enabled",
        lambda reg, site: True,
    )
    stub_launcher = tmp_path / "stub_launcher.py"
    stub_launcher.write_text("print('noop')", encoding="utf-8")
    manifest = orch.run(
        kind="daily",
        publish_root=tmp_path / "runs",
        launcher=stub_launcher,
    )
    statuses = {s["slug"]: s["status"] for s in manifest["sites"]}
    assert statuses.get("missing") == "error"


def test_run_coerces_string_publish_root(tmp_path: Path, monkeypatch) -> None:
    """The orchestrator's `publish_root` parameter is documented as
    `Optional[Path]` but should accept a `str` too — when called from
    the CLI's argparse with a string argument, `publish_root.mkdir()`
    would otherwise crash with `AttributeError: 'str' object has no
    attribute 'mkdir'`. Regression test for the dispatch string/Path bug.
    """
    monkeypatch.setattr(
        "plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator.iter_sites",
        lambda reg: iter([]),
    )
    publish_root_str = str(tmp_path / "string-publish-root")
    manifest = orch.run(
        kind="daily",
        publish_root=publish_root_str,  # pass a string, not a Path
        launcher=tmp_path / "stub_launcher.py",
    )
    # The string was coerced to Path; publish_root in the manifest
    # must be the canonical Path-as-string.
    assert manifest["publish_root"] == publish_root_str
    assert (tmp_path / "string-publish-root").is_dir()
