"""Tests for the atomic dispatch-cap acquire + operator reset.

Covers ``prismatic.dispatcher.EventRouterDedup.acquire_dispatch_slot``
(cross-process atomic check-and-increment, GRO-2979) and
``reset_dispatch_cap`` / the ``reset-dispatch-cap`` CLI (operator recovery
after a cap episode).
"""

from __future__ import annotations

import os
import tempfile
from multiprocessing import Process
from types import SimpleNamespace

import pytest


@pytest.fixture
def cap_dedup(monkeypatch):
    """EventRouterDedup from prismatic.dispatcher on an isolated DB.

    Uses a tiny cap (3) via the class attribute so tests run fast.
    """
    from prismatic.dispatcher import EventRouterDedup

    monkeypatch.setattr(EventRouterDedup, "MAX_DISPATCH_COUNT_PER_ISSUE", 3)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    dedup = EventRouterDedup(db_path=tmp.name)
    yield dedup, tmp.name
    dedup.close()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def _drive_to_cap(dedup, issue_id, cap=3):
    for _ in range(cap):
        count, over = dedup.acquire_dispatch_slot(issue_id)
        assert over is False
    assert count == cap


def test_acquire_grants_slots_up_to_cap(cap_dedup):
    dedup, _ = cap_dedup
    assert dedup.acquire_dispatch_slot("GRO-A1") == (1, False)
    assert dedup.acquire_dispatch_slot("GRO-A1") == (2, False)
    assert dedup.acquire_dispatch_slot("GRO-A1") == (3, False)
    # At cap: denied, count frozen
    assert dedup.acquire_dispatch_slot("GRO-A1") == (3, True)
    assert dedup.acquire_dispatch_slot("GRO-A1") == (3, True)
    assert dedup._count_dispatches("GRO-A1") == 3


def test_acquire_marks_over_cap_for_advisory_check(cap_dedup):
    """After the cap trips, the advisory check agrees (stuck path fires)."""
    dedup, _ = cap_dedup
    _drive_to_cap(dedup, "GRO-A2")
    assert dedup.is_over_dispatch_cap("GRO-A2") is True


def test_acquire_clears_stuck_notified(cap_dedup):
    """A fresh acquire clears stuck_notified_at so a new storm re-notifies."""
    dedup, _ = cap_dedup
    _drive_to_cap(dedup, "GRO-A3")
    dedup.mark_stuck_notified("GRO-A3")
    assert dedup.stuck_notified("GRO-A3") is True
    # Reset, then one fresh dispatch must clear the notification flag.
    dedup.reset_dispatch_cap("GRO-A3")
    count, over = dedup.acquire_dispatch_slot("GRO-A3")
    assert (count, over) == (1, False)
    assert dedup.stuck_notified("GRO-A3") is False


def _hammer(db_path, issue_id, iters):
    from prismatic.dispatcher import EventRouterDedup

    d = EventRouterDedup(db_path=db_path)
    for _ in range(iters):
        _, over = d.acquire_dispatch_slot(issue_id)
        if over:
            break
    d.close()


def test_acquire_cross_process_no_overshoot(cap_dedup):
    """N processes racing acquire_dispatch_slot grant exactly `cap` slots."""
    dedup, db_path = cap_dedup
    procs = [Process(target=_hammer, args=(db_path, "GRO-A4", 50)) for _ in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join()
    # New connection to see the final state (each proc had its own).
    assert dedup._count_dispatches("GRO-A4") == 3


def test_reset_dispatch_cap_clears_episode(cap_dedup):
    dedup, _ = cap_dedup
    _drive_to_cap(dedup, "GRO-A5")
    assert dedup.is_over_dispatch_cap("GRO-A5") is True
    state = dedup.reset_dispatch_cap("GRO-A5")
    assert state["issue_id"] == "GRO-A5"
    assert state["reset"] is True
    assert state["before"]["count"] == 3
    # Episode cleared: dispatch may resume immediately.
    assert dedup.is_over_dispatch_cap("GRO-A5") is False
    assert dedup.acquire_dispatch_slot("GRO-A5") == (1, False)


def test_reset_dispatch_cap_unknown_issue(cap_dedup):
    dedup, _ = cap_dedup
    state = dedup.reset_dispatch_cap("GRO-NEVER")
    assert state["reset"] is True
    assert state["before"] is None


def test_reset_dispatch_cap_cli(cap_dedup, capsys):
    """The operator CLI prints before/after state and exits 0."""
    from prismatic.dispatcher import cmd_reset_dispatch_cap

    dedup, db_path = cap_dedup
    _drive_to_cap(dedup, "GRO-A6")
    args = SimpleNamespace(issue_id="GRO-A6", db=db_path, json=False)
    assert cmd_reset_dispatch_cap(args) == 0
    out = capsys.readouterr().out
    assert "GRO-A6" in out
    assert "count was:" in out
    assert dedup.is_over_dispatch_cap("GRO-A6") is False


def test_reset_dispatch_cap_cli_json(cap_dedup, capsys):
    import json

    from prismatic.dispatcher import cmd_reset_dispatch_cap

    _, db_path = cap_dedup
    args = SimpleNamespace(issue_id="GRO-NEVER", db=db_path, json=True)
    assert cmd_reset_dispatch_cap(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_id"] == "GRO-NEVER"
    assert payload["before"] is None
