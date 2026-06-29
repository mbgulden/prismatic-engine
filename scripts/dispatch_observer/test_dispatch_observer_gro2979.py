"""Tests for GRO-2979 dispatch-closure fix.

Three protections:
  1. ``register_proc_for_observation`` + ``_observer_loop`` drain every
     spawned ``Popen`` back through ``TelemetryCollector.update_agent_run``.
     Regression: GRO-2051 re-dispatched 178 times because this was unwired.
  2. ``EventRouterDedup.is_over_dispatch_cap`` defends against retry storms
     (hard cap on dispatches per issue in a sliding window).
  3. ``EventRouterDedup.record_dispatch`` increments cleanly and survives
     back-to-back calls.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Test scaffolding: isolated DB per test
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_dedup_db(monkeypatch):
    """Redirect dedup to a tmp DB so tests don't touch prod state."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("PRISMATIC_STATE_DIR", os.path.dirname(tmp.name))
    # bust the singleton so the new path is picked up
    import prismatic.dedup as dedup_mod
    dedup_mod._global_dedup = None
    yield tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Tests: dedup cap (GRO-2979 §2 regression prevention)
# ---------------------------------------------------------------------------

def test_record_dispatch_increments(isolated_dedup_db):
    """Every dispatch increments exactly once; counts survive across calls."""
    from prismatic.dedup import EventRouterDedup

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    c1 = dedup.record_dispatch("GRO-TEST-1")
    c2 = dedup.record_dispatch("GRO-TEST-1")
    c3 = dedup.record_dispatch("GRO-TEST-1")
    assert (c1, c2, c3) == (1, 2, 3)
    assert dedup._count_dispatches("GRO-TEST-1") == 3


def test_count_dispatches_unknown_issue(isolated_dedup_db):
    """A never-dispatched issue returns 0, not a KeyError."""
    from prismatic.dedup import EventRouterDedup

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    assert dedup._count_dispatches("GRO-NEVER-SEEN") == 0


def test_dispatch_cap_false_under_threshold(isolated_dedup_db):
    """Below the cap → not stuck."""
    from prismatic.dedup import EventRouterDedup, MAX_DISPATCH_COUNT_PER_ISSUE

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    for _ in range(MAX_DISPATCH_COUNT_PER_ISSUE - 1):
        dedup.record_dispatch("GRO-TEST-2")
    assert dedup.is_over_dispatch_cap("GRO-TEST-2") is False


def test_dispatch_cap_true_at_threshold(isolated_dedup_db):
    """At-or-above the cap → stuck."""
    from prismatic.dedup import EventRouterDedup, MAX_DISPATCH_COUNT_PER_ISSUE

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    for _ in range(MAX_DISPATCH_COUNT_PER_ISSUE):
        dedup.record_dispatch("GRO-TEST-3")
    assert dedup.is_over_dispatch_cap("GRO-TEST-3") is True


def test_dispatch_cap_window_past(isolated_dedup_db, monkeypatch):
    """Cap only fires within the sliding window; old storms stop being stuck.

    Simulates "an issue that was spamming 6 months ago is now quiet" by
    back-dating last_dispatched_at beyond the window.
    """
    from prismatic.dedup import EventRouterDedup, MAX_DISPATCH_COUNT_PER_ISSUE

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    for _ in range(MAX_DISPATCH_COUNT_PER_ISSUE):
        dedup.record_dispatch("GRO-TEST-4")

    # Back-date last_dispatched_at to 49h ago
    conn = sqlite3.connect(isolated_dedup_db)
    conn.execute(
        "UPDATE dispatch_counts SET last_dispatched_at = ? WHERE issue_id = ?",
        (time.time() - 49 * 3600, "GRO-TEST-4"),
    )
    conn.commit()
    conn.close()

    assert dedup.is_over_dispatch_cap("GRO-TEST-4") is False


def test_dispatch_cap_env_override(isolated_dedup_db, monkeypatch):
    """Honors ``PRISMATIC_MAX_DISPATCH_PER_ISSUE`` env override."""
    monkeypatch.setenv("PRISMATIC_MAX_DISPATCH_PER_ISSUE", "3")
    # Reload module so the module-level constant picks up env
    import importlib
    import prismatic.dedup as dedup_mod
    importlib.reload(dedup_mod)

    from prismatic.dedup import EventRouterDedup

    dedup = EventRouterDedup(db_path=isolated_dedup_db)
    dedup.record_dispatch("GRO-TEST-5")
    dedup.record_dispatch("GRO-TEST-5")
    dedup.record_dispatch("GRO-TEST-5")
    assert dedup.is_over_dispatch_cap("GRO-TEST-5") is True


# ---------------------------------------------------------------------------
# Tests: process observer (GRO-2979 §1 structural fix)
# ---------------------------------------------------------------------------

def _wait_for(predicate, timeout=8.0, interval=0.2):
    """Poll *predicate* until it returns truthy or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_observer_drains_completed_proc(monkeypatch):
    """A proc that exits 0 within the timeout window triggers a closure call.

    Uses an in-memory SQLite collector target to avoid the prod DB.
    """
    from prismatic.dispatcher import (
        _PENDING_PROCS,
        register_proc_for_observation,
        _ensure_observer_started,
    )

    # Track calls to get_collector() / update_agent_run()
    calls = []

    class FakeCollector:
        def update_agent_run(self, **kwargs):
            calls.append(kwargs)

    fake = FakeCollector()

    # Patch get_collector so the observer thread sees our fake
    from prismatic import dispatcher as disp
    monkeypatch.setattr(disp, "_HAS_IPC_BRIDGE", False, raising=False)
    import prismatic.telemetry as tel_mod
    monkeypatch.setattr(tel_mod, "get_collector", lambda: fake)

    # Drain previous pending state
    with disp._PENDING_LOCK:
        _PENDING_PROCS.clear()

    # Spawn a fast-exiting process
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_id = "test-observer-1"
    register_proc_for_observation(run_id, proc)
    _ensure_observer_started.__dict__.pop("_already", None)
    register_proc_for_observation(run_id, proc)  # re-arm watcher idempotently

    found = _wait_for(
        lambda: any(c.get("run_id") == run_id for c in calls),
        timeout=10.0,
    )
    # Also assert no calls for a non-existent run_id
    matching = [c for c in calls if c.get("run_id") == run_id]
    assert found, f"observer never wrote closure for {run_id}; calls={calls}"
    last = matching[-1]
    assert last["status"] == "completed"
    assert last["exit_code"] == 0


def test_observer_drains_failed_proc(monkeypatch):
    """A proc that exits non-zero is logged as 'failed' with the exit code."""
    from prismatic.dispatcher import (
        _PENDING_PROCS,
        register_proc_for_observation,
        _ensure_observer_started,
    )

    calls = []

    class FakeCollector:
        def update_agent_run(self, **kwargs):
            calls.append(kwargs)

    fake = FakeCollector()
    from prismatic import dispatcher as disp
    import prismatic.telemetry as tel_mod
    monkeypatch.setattr(tel_mod, "get_collector", lambda: fake)

    with disp._PENDING_LOCK:
        _PENDING_PROCS.clear()

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_id = "test-observer-2"
    register_proc_for_observation(run_id, proc)

    found = _wait_for(
        lambda: any(c.get("run_id") == run_id for c in calls),
        timeout=10.0,
    )
    matching = [c for c in calls if c.get("run_id") == run_id]
    assert found, f"observer never wrote closure for {run_id}; calls={calls}"
    last = matching[-1]
    assert last["status"] == "failed"
    assert last["exit_code"] == 7


def test_observer_idempotent_registration(monkeypatch):
    """Re-registering an already-observed run_id doesn't double-write."""
    from prismatic.dispatcher import _PENDING_PROCS, register_proc_for_observation

    calls = []

    class FakeCollector:
        def update_agent_run(self, **kwargs):
            calls.append(kwargs)

    fake = FakeCollector()
    from prismatic import dispatcher as disp
    import prismatic.telemetry as tel_mod
    monkeypatch.setattr(tel_mod, "get_collector", lambda: fake)

    with disp._PENDING_LOCK:
        _PENDING_PROCS.clear()

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_id = "test-observer-3"

    # Multiple registrations of the same run_id should not duplicate work
    register_proc_for_observation(run_id, proc)
    register_proc_for_observation(run_id, proc)
    register_proc_for_observation(run_id, proc)

    assert _wait_for(
        lambda: any(c.get("run_id") == run_id for c in calls),
        timeout=10.0,
    )

    # Wait a beat to allow any spurious duplicate, then assert exactly 1
    time.sleep(4.0)
    matching = [c for c in calls if c.get("run_id") == run_id]
    assert len(matching) == 1, (
        f"expected single closure write, got {len(matching)}: {matching}"
    )


def test_register_none_proc_is_noop():
    """register_proc_for_observation(None) is harmless."""
    from prismatic.dispatcher import register_proc_for_observation, _PENDING_PROCS

    register_proc_for_observation("test-noop", None)
    assert "test-noop" not in _PENDING_PROCS
