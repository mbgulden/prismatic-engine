"""Regression test for journal.py _last_sync handling.

The bug: project-registry.json had `_last_sync` as an ISO timestamp string
(rather than the dict shape the code assumed). extract_golden_thread_summary
crashed with "'str' object has no attribute 'get'", causing the Hermes daily
journal snapshot cron to fail every hour.

Fix: defensive type check — dict → show stats, string → show timestamp, skip stats.

Reference: commit af37265c (cherry-picked from Ned's feature/journal-sync-string-handling).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from prismatic.journal import extract_golden_thread_summary, JournalConfig


def _make_config(registry_content: dict) -> JournalConfig:
    """Build a JournalConfig pointing at a temp project-registry.json."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(registry_content, tmp)
    tmp.flush()
    tmp.close()
    cfg = MagicMock(spec=JournalConfig)
    cfg.project_registry = Path(tmp.name)
    return cfg


def test_last_sync_as_dict_shows_stats():
    """When _last_sync is a dict, show Linear/GitHub stats."""
    reg = {
        "_last_sync": {
            "linear_in_progress": 3,
            "linear_in_review": 2,
            "linear_todo": 5,
            "github_prs_open": 4,
            "github_issues_open": 7,
        },
        "ventures": {},
        "standalone_projects": {},
    }
    cfg = _make_config(reg)
    out = extract_golden_thread_summary(cfg)
    assert "Linear:" in out
    assert "3 In Progress" in out
    assert "2 In Review" in out
    assert "5 Todo" in out
    assert "GitHub:" in out
    assert "4 open PRs" in out
    assert "7 issues" in out
    print("PASS: _last_sync as dict → shows stats")


def test_last_sync_as_string_shows_timestamp():
    """When _last_sync is an ISO timestamp string (the bug case), show it without crashing."""
    reg = {
        "_last_sync": "2026-06-29T01:00:00+00:00",  # the actual broken shape
        "ventures": {},
        "standalone_projects": {},
    }
    cfg = _make_config(reg)
    out = extract_golden_thread_summary(cfg)
    # Should NOT have crashed (the bug)
    assert "Last sync" in out or "2026-06-29T01:00:00" in out, (
        f"expected timestamp in output, got: {out!r}"
    )
    # Should NOT contain the stats line that would require a dict
    assert "Linear:" not in out, f"should skip Linear stats when sync is string: {out!r}"
    print("PASS: _last_sync as string → shows timestamp, skips stats")


def test_no_last_sync_field():
    """When _last_sync is missing entirely, don't crash."""
    reg = {"ventures": {}, "standalone_projects": {}}
    cfg = _make_config(reg)
    out = extract_golden_thread_summary(cfg)
    # Should produce some output but no crash
    assert "Golden Thread" in out
    print("PASS: missing _last_sync → no crash")


def test_unreadable_registry_returns_warning():
    """When project-registry.json doesn't exist, return warning."""
    cfg = MagicMock(spec=JournalConfig)
    cfg.project_registry = Path("/tmp/definitely-does-not-exist-12345.json")
    out = extract_golden_thread_summary(cfg)
    assert "not found" in out or "⚠️" in out
    print("PASS: missing registry file → returns warning")
