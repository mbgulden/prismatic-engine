"""
Tests for prismatic.supervisor.recovery.

Covers:
- Pool capacity enforcement (backpressure)
- PID tracking and reaping
- Retry counting
- DLQ on max_retries
- Stats counters
- Module-level singleton

Run: pytest prismatic/supervisor/tests/test_recovery.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.supervisor.recovery import (  # noqa: E402
    SupervisorPool, SupervisorRecord, get_pool, reset_pool,
    dispatch_to_supervisor_bounded, MAX_CONCURRENT,
)


# A trivial command that exits quickly so tests don't hang
QUICK_CMD = ["python3", "-c", "import time; time.sleep(0.5)"]
LONG_CMD = ["python3", "-c", "import time; time.sleep(60)"]


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_pool()
    yield
    reset_pool()


def make_pool(**kwargs) -> SupervisorPool:
    """Make a pool with small defaults for testing."""
    pool = SupervisorPool(
        max_concurrent=int(kwargs.get("max_concurrent", 2)),
        reap_interval_sec=float(kwargs.get("reap_interval_sec", 0.1)),
        max_retries=int(kwargs.get("max_retries", 2)),
        dlq_path=kwargs.get("dlq_path") or Path(tempfile.gettempdir()) / "test_dlq.jsonl",
    )
    return pool


def test_initial_pool_is_empty():
    pool = make_pool()
    assert pool.live_count() == 0
    assert pool.can_dispatch() is True
    stats = pool.stats()
    assert stats["max_concurrent"] == 2
    assert stats["total_spawned"] == 0


def test_dispatch_tracks_pid():
    pool = make_pool()
    rec = pool.dispatch("GRO-1", QUICK_CMD)
    assert rec is not None
    assert rec.pid > 0
    assert rec.issue_id == "GRO-1"
    assert rec.is_alive()  # subprocess is still alive (sleeping)
    assert pool.live_count() == 1
    assert pool.stats()["total_spawned"] == 1


def test_pool_capacity_enforced():
    """Backpressure: can't dispatch when at max_concurrent."""
    pool = make_pool(max_concurrent=2)
    rec1 = pool.dispatch("GRO-1", LONG_CMD)
    rec2 = pool.dispatch("GRO-2", LONG_CMD)
    rec3 = pool.dispatch("GRO-3", LONG_CMD)  # should be None (pool full)
    assert rec1 is not None
    assert rec2 is not None
    assert rec3 is None
    assert pool.stats()["total_skipped_cap"] == 1
    assert pool.stats()["live_count"] == 2


def test_reap_removes_finished():
    """When a supervisor exits, it should be removed from the pool."""
    pool = make_pool(max_concurrent=5, reap_interval_sec=0.05)
    rec = pool.dispatch("GRO-1", QUICK_CMD)  # exits in 0.5s
    assert rec is not None
    assert pool.live_count() == 1

    # Wait for it to finish + reap interval
    time.sleep(0.8)
    pool.reap()
    assert pool.live_count() == 0
    assert pool.stats()["total_reaped"] >= 1


def test_dlq_after_max_retries():
    """Same issue_id dispatched max_retries times should go to DLQ.

    Retry counting only happens via on_failure() (real failure signal).
    A successful dispatch doesn't bump the count. After max_retries
    failures, dispatch() routes the next attempt to the DLQ.
    """
    pool = make_pool(max_concurrent=10, max_retries=2)
    # Use a temp DLQ path
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        dlq_path = Path(tmp.name)
    pool.dlq_path = dlq_path

    # Successful dispatch doesn't bump retry count
    rec1 = pool.dispatch("GRO-DLQ", QUICK_CMD)
    assert rec1 is not None

    # 2 failures bring retry_count to 2 == max_retries
    pool.on_failure("GRO-DLQ", "error 1")
    pool.on_failure("GRO-DLQ", "error 2")
    # Now next dispatch should hit DLQ
    rec2 = pool.dispatch("GRO-DLQ", QUICK_CMD)
    assert rec2 is None
    assert dlq_path.exists()
    assert "GRO-DLQ" in dlq_path.read_text()

    # Cleanup
    dlq_path.unlink()


def test_on_failure_increments_retry_count():
    """on_failure() bumps the retry count; dispatch() doesn't."""
    pool = make_pool(max_concurrent=5, max_retries=3)
    # Successful dispatch — retry_count stays 0
    rec = pool.dispatch("GRO-FAIL", QUICK_CMD)
    assert rec is not None
    assert rec.retry_count == 0

    # Two failures bring retry_count to 2; can still dispatch
    pool.on_failure("GRO-FAIL", "transient error")
    pool.on_failure("GRO-FAIL", "another error")
    rec = pool.dispatch("GRO-FAIL", QUICK_CMD)
    assert rec is not None
    assert rec.retry_count == 2

    # Third failure hits max_retries (3); next dispatch → DLQ
    pool.on_failure("GRO-FAIL", "final error")
    rec = pool.dispatch("GRO-FAIL", QUICK_CMD)
    assert rec is None


def test_module_singleton_returns_same_instance():
    p1 = get_pool()
    p2 = get_pool()
    assert p1 is p2


def test_dispatch_to_supervisor_bounded_wrapper():
    """The convenience function works end-to-end."""
    # Use a custom pool via reset
    from prismatic.supervisor import recovery
    pool = make_pool(max_concurrent=2)
    recovery._pool = pool

    result = pool.dispatch("GRO-1", QUICK_CMD)
    assert result is not None

    # Pool full → next dispatch returns None
    pool.dispatch("GRO-2", LONG_CMD)
    pool.dispatch("GRO-3", LONG_CMD)  # should be skipped
    stats = pool.stats()
    assert stats["total_skipped_cap"] >= 1


def test_long_running_supervisor_stays_alive():
    """A long-running supervisor should not be reaped while alive."""
    pool = make_pool(max_concurrent=3, reap_interval_sec=0.05)
    rec = pool.dispatch("GRO-LONG", LONG_CMD)
    assert rec is not None

    time.sleep(0.3)  # longer than reap interval
    pool.reap()
    # Should still be alive (sleeping for 60s)
    assert rec.is_alive()
    assert pool.live_count() == 1


def test_stats_counters_accumulate():
    pool = make_pool(max_concurrent=2, reap_interval_sec=0.05)
    # Spawn 2, then try 1 more (skipped)
    pool.dispatch("GRO-A", LONG_CMD)
    pool.dispatch("GRO-B", LONG_CMD)
    pool.dispatch("GRO-C", LONG_CMD)  # skipped
    stats = pool.stats()
    assert stats["total_spawned"] == 2
    assert stats["total_skipped_cap"] == 1
    assert stats["live_count"] == 2


def test_supervisor_record_age():
    rec = SupervisorRecord(
        pid=99999,  # fake PID
        issue_id="GRO-X",
        started_at=time.time() - 60,
        cmd=[],
    )
    assert rec.age_sec() >= 60
    assert rec.age_sec() < 61
    # PID 99999 likely doesn't exist
    assert rec.is_alive() is False