"""Tests for the pending_changes dashboard panel."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.pwp.capabilities.publish_kpi_tracker.pending_changes import (
    PendingChange,
    render_pending_changes_html,
    scan_provision_state,
)


def _write_state(directory: Path, name: str, state: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def test_scan_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert scan_provision_state(tmp_path) == []


def test_scan_missing_dir_returns_empty(tmp_path: Path) -> None:
    """A directory that doesn't exist must NOT raise — it returns []. This
    keeps the dashboard render path safe even when no provisioning has
    ever been run."""
    nonexistent = tmp_path / "never-created"
    assert scan_provision_state(nonexistent) == []


def test_scan_detects_soft_failed_step(tmp_path: Path) -> None:
    """A complete run with one _soft_failure=True step surfaces as a
    'soft_failed_step' PendingChange."""
    _write_state(tmp_path, "ezshare.systems.json", {
        "domain": "ezshare.systems",
        "overall_status": "complete",
        "steps": [
            {"name": "platform_detect", "status": "complete",
             "output": {"platform": "vercel"}},
            {"name": "verify_domain", "status": "complete",
             "output": {"challenge_token": "abc"}},
            {"name": "cloudflare_zone", "status": "skipped",
             "output": {"reason": "platform=vercel"}},
            {"name": "vercel_project", "status": "failed",
             "error": "VERCEL_TOKEN env var is not set",
             "output": {"_soft_failure": True}},
            {"name": "ga4_property", "status": "failed",
             "error": "GOOGLE_SA_JSON env var is not set",
             "output": {"_soft_failure": True}},
            {"name": "migrate_kpi", "status": "complete", "output": {}},
        ],
    })
    changes = scan_provision_state(tmp_path)
    kinds = [c.kind for c in changes]
    assert "soft_failed_step" in kinds
    assert len([c for c in changes if c.kind == "soft_failed_step"]) == 2
    # The detail should mention VERCEL_TOKEN and GOOGLE_SA_JSON.
    details = " ".join(c.detail for c in changes)
    assert "VERCEL_TOKEN" in details or "GOOGLE_SA_JSON" in details


def test_scan_detects_failed_run(tmp_path: Path) -> None:
    """A run with overall_status='failed' surfaces as a 'failed_run'."""
    _write_state(tmp_path, "broken.example.com.json", {
        "domain": "broken.example.com",
        "overall_status": "failed",
        "steps": [
            {"name": "verify_domain", "status": "failed",
             "error": "TXT record not found at _pwp-verify.broken.example.com"},
        ],
    })
    changes = scan_provision_state(tmp_path)
    assert len(changes) == 1
    assert changes[0].kind == "failed_run"
    assert "TXT record not found" in changes[0].detail


def test_scan_detects_unmerged_run(tmp_path: Path, tmp_path_factory=None) -> None:
    """A complete run whose domain isn't in the canonical sites.json
    surfaces as an 'unmerged_run'."""
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    _write_state(state_dir, "newsite.example.com.json", {
        "domain": "newsite.example.com",
        "overall_status": "complete",
        "steps": [
            {"name": "verify_domain", "status": "complete", "output": {}},
            {"name": "migrate_kpi", "status": "complete", "output": {}},
        ],
    })
    canonical = tmp_path / "sites.json"
    canonical.write_text(json.dumps({
        "version": 1,
        "sites": [
            {"domain": "existing.example.com"},
            # newsite.example.com is NOT here yet
        ],
    }), encoding="utf-8")
    changes = scan_provision_state(state_dir, canonical_sites_path=canonical)
    assert len(changes) == 1
    assert changes[0].kind == "unmerged_run"
    assert changes[0].domain == "newsite.example.com"
    assert "migrate --merge" in changes[0].next_action


def test_scan_skips_unmerged_when_in_canonical(tmp_path: Path) -> None:
    """If the domain IS in canonical sites.json, don't flag it as unmerged."""
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    _write_state(state_dir, "merged.example.com.json", {
        "domain": "merged.example.com",
        "overall_status": "complete",
        "steps": [{"name": "migrate_kpi", "status": "complete", "output": {}}],
    })
    canonical = tmp_path / "sites.json"
    canonical.write_text(json.dumps({
        "version": 1,
        "sites": [{"domain": "merged.example.com"}],
    }), encoding="utf-8")
    changes = scan_provision_state(state_dir, canonical_sites_path=canonical)
    assert changes == []


def test_render_html_includes_pending_changes_panel() -> None:
    """When there are pending changes, the panel HTML must contain the
    kind, summary, and next_action text."""
    change = PendingChange(
        domain="example.com",
        kind="soft_failed_step",
        summary="ga4_property soft-failed",
        detail="GOOGLE_SA_JSON env var is not set",
        next_action="Set GOOGLE_SA_JSON and re-run --steps ga4_property",
    )
    html = render_pending_changes_html([change])
    assert "Pending Changes" in html
    assert "example.com" in html
    assert "soft_failed_step" in html
    assert "GOOGLE_SA_JSON" in html
    assert "ga4_property" in html


def test_render_html_empty_when_no_changes() -> None:
    """Empty list must produce empty HTML (so the dashboard doesn't show
    an empty 'Pending Changes' header)."""
    assert render_pending_changes_html([]) == ""


def test_to_dict_roundtrip() -> None:
    """PendingChange.to_dict must produce a JSON-serializable dict."""
    change = PendingChange(
        domain="a.com",
        kind="soft_failed_step",
        summary="x",
        detail="y",
        next_action="z",
    )
    d = change.to_dict()
    # Must be JSON-serializable
    json.dumps(d)
    assert d["domain"] == "a.com"
    assert d["kind"] == "soft_failed_step"
