"""Dispatch-cap (GRO-2979) regression tests for the dispatcher's LOCAL
EventRouterDedup class (prismatic/dispatcher.py).

Background: the dispatcher's local ``EventRouterDedup`` was missing
``record_dispatch`` / ``is_over_dispatch_cap`` / ``_count_dispatches`` /
``MAX_DISPATCH_*`` — all called by the dispatcher's own cap-check and
counter blocks. Every call raised AttributeError, swallowed by except
handlers, so the cap never capped anything. These tests fail on the
pre-fix class (AttributeError) and pass after the port.

Importing prismatic.dispatcher is heavy; the module-level import below
is deliberate so fail-first runs exercise the real import path.
"""
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from prismatic.dispatcher import EventRouterDedup

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def dedup(tmp_path):
    d = EventRouterDedup(db_path=str(tmp_path / "dispatch_cap_test.db"))
    yield d
    d.close()


# ── Core behavior ─────────────────────────────────────────────────────


def test_record_dispatch_increments_and_counts(dedup):
    assert dedup.record_dispatch("ISS-1") == 1
    assert dedup.record_dispatch("ISS-1") == 2
    assert dedup.record_dispatch("ISS-1") == 3
    assert dedup._count_dispatches("ISS-1") == 3
    # A different issue is tracked independently.
    assert dedup._count_dispatches("ISS-2") == 0


def test_is_over_dispatch_cap_below_cap(dedup):
    dedup.MAX_DISPATCH_COUNT_PER_ISSUE = 3
    dedup.record_dispatch("ISS-1")
    dedup.record_dispatch("ISS-1")
    assert dedup.is_over_dispatch_cap("ISS-1") is False


def test_is_over_dispatch_cap_at_and_above_cap(dedup):
    dedup.MAX_DISPATCH_COUNT_PER_ISSUE = 3
    for _ in range(3):
        dedup.record_dispatch("ISS-1")
    assert dedup.is_over_dispatch_cap("ISS-1") is True
    dedup.record_dispatch("ISS-1")
    assert dedup.is_over_dispatch_cap("ISS-1") is True


def test_is_over_dispatch_cap_unknown_issue(dedup):
    assert dedup.is_over_dispatch_cap("NEVER-SEEN") is False


def test_window_expiry_resets_cap(dedup, tmp_path):
    """Dispatches older than the window no longer trip the cap."""
    dedup.MAX_DISPATCH_COUNT_PER_ISSUE = 2
    dedup.MAX_DISPATCH_WINDOW_HOURS = 48
    dedup.record_dispatch("ISS-1")
    dedup.record_dispatch("ISS-1")
    assert dedup.is_over_dispatch_cap("ISS-1") is True

    # Age the last dispatch beyond the window.
    old = time.time() - (49 * 3600)
    conn = sqlite3.connect(dedup._db_path)
    conn.execute(
        "UPDATE dispatch_counts SET last_dispatched_at = ? WHERE issue_id = ?",
        (old, "ISS-1"),
    )
    conn.commit()
    conn.close()
    assert dedup.is_over_dispatch_cap("ISS-1") is False


# ── Policy parity with prismatic.dedup ──────────────────────────────────


def test_defaults_match_canonical_dedup():
    from prismatic.dedup import (
        MAX_DISPATCH_COUNT_PER_ISSUE as CANON_COUNT,
        MAX_DISPATCH_WINDOW_HOURS as CANON_HOURS,
    )

    assert EventRouterDedup.MAX_DISPATCH_COUNT_PER_ISSUE == CANON_COUNT == 20
    assert EventRouterDedup.MAX_DISPATCH_WINDOW_HOURS == CANON_HOURS == 48


def test_env_overrides():
    """Env vars move the caps, same names/defaults as prismatic.dedup."""
    code = (
        "import sys; sys.path.insert(0, '.');"
        "from prismatic.dispatcher import EventRouterDedup;"
        "print(EventRouterDedup.MAX_DISPATCH_COUNT_PER_ISSUE);"
        "print(EventRouterDedup.MAX_DISPATCH_WINDOW_HOURS)"
    )
    env = dict(
        os.environ,
        PRISMATIC_MAX_DISPATCH_PER_ISSUE="3",
        PRISMATIC_MAX_DISPATCH_WINDOW_HOURS="12",
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["3", "12"]


# ── Call-site contract: every name the dispatcher uses must exist ───────


def test_call_site_attributes_exist(dedup):
    """dispatcher.py:4675-4692 and :4919 touch all of these; any missing
    one raises AttributeError and is swallowed by an except handler,
    silently disabling the cap. Fails on the pre-fix class."""
    assert isinstance(dedup.MAX_DISPATCH_COUNT_PER_ISSUE, int)
    assert isinstance(dedup.MAX_DISPATCH_WINDOW_HOURS, int)
    assert callable(dedup.is_over_dispatch_cap)
    assert callable(dedup._count_dispatches)
    assert callable(dedup.record_dispatch)


def _run_cap_check_block(dedup, issue_id, agent_name="agy"):
    """Mirror of the dispatcher's GRO-2979 cap-check block (~:4670)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            over_cap = dedup.is_over_dispatch_cap(issue_id)
            if isinstance(over_cap, bool) and over_cap:
                stuck_count = dedup._count_dispatches(issue_id)
                print(
                    f"[dispatcher] STUCK {agent_name}: {stuck_count} in "
                    f"{dedup.MAX_DISPATCH_WINDOW_HOURS}h "
                    f"(cap={dedup.MAX_DISPATCH_COUNT_PER_ISSUE})"
                )
        except Exception as exc:
            print(f"[dispatcher] dispatch-cap check failed: {exc}")
    return buf.getvalue()


def _run_counter_block(dedup, issue_id):
    """Mirror of the dispatcher's GRO-2979 counter block (~:4919)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            dedup.record_dispatch(issue_id)
        except Exception as exc:
            print(f"[dispatcher] record_dispatch failed: {exc}")
    return buf.getvalue()


def test_cap_check_block_reports_stuck(dedup):
    """At cap, the block flags the issue stuck instead of failing."""
    dedup.MAX_DISPATCH_COUNT_PER_ISSUE = 2
    for _ in range(2):
        dedup.record_dispatch("ISS-9")
    out = _run_cap_check_block(dedup, "ISS-9")
    assert "dispatch-cap check failed" not in out
    assert "STUCK agy" in out


def test_cap_check_block_passes_below_cap(dedup):
    dedup.MAX_DISPATCH_COUNT_PER_ISSUE = 5
    dedup.record_dispatch("ISS-8")
    out = _run_cap_check_block(dedup, "ISS-8")
    assert "dispatch-cap check failed" not in out
    assert "STUCK" not in out


def test_counter_block_increments_without_error(dedup):
    out = _run_counter_block(dedup, "ISS-7")
    assert "record_dispatch failed" not in out
    assert dedup._count_dispatches("ISS-7") == 1
