"""Dispatch robustness regression tests (stress-test findings, 2026-09-28).

Covers, against the real code paths:
  1. Stuck-comment dedup: the over-cap path notifies once per episode.
  2. AGY re-escalation guard: a failed label transition escalates once;
     later cycles complete the escalation without re-killing processes.
  3. EventRouterDedup thread-safety: the shared SQLite connection is
     usable from multiple threads (all methods serialized by the lock).
  4. Import-time env validation: garbage values fall back to defaults
     with a warning instead of killing the module import.

All DB state is isolated to tmp_path. No Linear calls (mocked).
"""
import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from prismatic import dispatcher
from prismatic.dispatcher import EventRouterDedup

CAP = EventRouterDedup.MAX_DISPATCH_COUNT_PER_ISSUE
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def dedup(tmp_path):
    d = EventRouterDedup(db_path=str(tmp_path / "robustness.db"))
    yield d
    d.close()


# ── 1. Stuck-comment dedup ─────────────────────────────────────────


def _patch_dispatch_once(monkeypatch, stuck_issue_id="ISS-STUCK"):
    """Mock the Linear/external surface of dispatch_once. Returns the
    add_comment call log."""
    comments = []
    monkeypatch.setattr(dispatcher, "setup_pipeline_issues", lambda *a, **k: [])
    monkeypatch.setattr(
        dispatcher, "get_issues_with_label",
        lambda label, max_issues=20: (
            [{"id": stuck_issue_id, "identifier": "TST-1", "title": "t",
              "labels": ["agent:kai", "dispatch:ready"]}]
            if label == "agent:kai" else []
        ),
    )
    monkeypatch.setattr(dispatcher, "is_dispatch_ready", lambda issue: True)
    monkeypatch.setattr(
        dispatcher, "filter_dispatchable_issues",
        lambda issues, agent_name: (issues, []),
    )
    monkeypatch.setattr(
        dispatcher, "add_comment",
        lambda issue_id, body: comments.append((issue_id, body)) or True,
    )
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", lambda **k: 0)
    monkeypatch.setattr(dispatcher, "recover_stalled_agy", lambda **k: None)
    monkeypatch.setattr(dispatcher, "detect_origin_completions",
                        lambda *a, **k: 0)
    monkeypatch.setattr(dispatcher, "route_dispatch_ready_issues",
                        lambda *a, **k: 0)
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks",
                        lambda *a, **k: 0)
    monkeypatch.setattr(dispatcher, "section_due", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "skip_budget_section",
                        lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "scan_cadence", lambda *a, **k: 1)
    monkeypatch.setattr(dispatcher, "poll_fallback_enabled", lambda: True)
    monkeypatch.setattr(dispatcher, "linear_broad_poll_allowed",
                        lambda **k: True)
    monkeypatch.setattr(dispatcher, "poll_max_calls_per_cycle", lambda: 50)
    monkeypatch.setattr(
        dispatcher,
        "get_linear_rate_limit_snapshot",
        lambda: {},  # noqa: PIE807 - mock must stay callable
    )
    monkeypatch.setattr(dispatcher, "report_lane_starvation",
                        lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "starvation_signal_for",
                        lambda name: f"{name}_queue_empty")
    monkeypatch.setattr(dispatcher, "_governor",
                        type("G", (), {"prune_stale": lambda self: 0})())
    return comments


def test_stuck_comment_posted_once_across_cycles(dedup, monkeypatch, tmp_path):
    comments = _patch_dispatch_once(monkeypatch)
    for _ in range(CAP):
        dedup.record_dispatch("ISS-STUCK")
    for _ in range(3):
        counts = dispatcher.dispatch_once(
            dedup, pipelines={"pipelines": {}})
        assert counts["stuck"] == 1
    assert len(comments) == 1, f"comment spam: {len(comments)} posts"
    assert "Auto-marked stuck" in comments[0][1]


def test_stuck_renotifies_after_dispatch_resets_flag(dedup):
    for _ in range(CAP):
        dedup.record_dispatch("ISS-RN")
    assert dedup.stuck_notified("ISS-RN") is False
    dedup.mark_stuck_notified("ISS-RN")
    assert dedup.stuck_notified("ISS-RN") is True
    # A dispatch going through opens a new episode: may re-notify.
    dedup.record_dispatch("ISS-RN")
    assert dedup.stuck_notified("ISS-RN") is False


def test_stuck_notified_migrates_legacy_table(tmp_path):
    db = str(tmp_path / "legacy.db")
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE dispatch_counts (
            issue_id TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            last_dispatched_at REAL,
            first_dispatched_at REAL
        )"""
    )
    con.execute("INSERT INTO dispatch_counts VALUES ('ISS-L', 20, 1.0, 1.0)")
    con.commit()
    con.close()
    d = EventRouterDedup(db_path=db)
    try:
        assert d.stuck_notified("ISS-L") is False  # ALTER ran transparently
        d.mark_stuck_notified("ISS-L")
        assert d.stuck_notified("ISS-L") is True
        assert d._count_dispatches("ISS-L") == 20  # data preserved
    finally:
        d.close()


# ── 2. AGY re-escalation guard ─────────────────────────────────────


def _patch_recovery(monkeypatch, tmp_path, *, fail_times=0):
    db = str(tmp_path / "agy_stall.db")
    monkeypatch.setattr(dispatcher, "DEFAULT_DB_PATH", db)
    issues = [{"id": "ISS-AGY", "identifier": "TEST-9",
               "title": "stuck agy work", "labels": ["agent:agy"]}]
    monkeypatch.setattr(
        dispatcher, "get_issues_with_label",
        lambda label, max_issues=20: list(issues)
        if label == "agent:agy" else [],
    )
    transitions = []
    attempts = {"n": 0}

    def fake_transition(issue_id, remove_label=None, add_label=None):
        transitions.append((issue_id, remove_label, add_label))
        attempts["n"] += 1
        if attempts["n"] <= fail_times:
            raise RuntimeError("linear label write failed")
        issues.clear()  # success: the issue leaves the agy lane

    monkeypatch.setattr(dispatcher, "transition_label", fake_transition)
    comments = []
    monkeypatch.setattr(dispatcher, "add_comment",
                        lambda issue_id, body: comments.append(body) or True)
    cleanups = []
    monkeypatch.setattr(
        dispatcher, "cleanup_stale_agy",
        lambda max_age_minutes=5: cleanups.append(max_age_minutes) or 0)
    launched = []
    monkeypatch.setattr(dispatcher, "AGENT_LAUNCHERS",
                        {"fred": lambda iid, **kw: launched.append(iid)})

    class FakeModeSwitch:
        def request_approval(self, *a, **kw):
            return True

    monkeypatch.setattr(dispatcher, "mode_switch", FakeModeSwitch())
    return db, transitions, comments, launched, cleanups


def _tracker_row(db, issue_id="ISS-AGY"):
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT cycle_count, escalated FROM agy_stall_tracker "
        "WHERE issue_id=?", (issue_id,)).fetchone()
    con.close()
    return row


def test_recovery_failed_transition_then_recovers(monkeypatch, tmp_path):
    """Transition fails on the first escalation attempt, succeeds on the
    retry: exactly one kill-all, escalation completes on the retry."""
    db, transitions, comments, launched, cleanups = _patch_recovery(
        monkeypatch, tmp_path, fail_times=1)
    dispatcher.recover_stalled_agy(max_retries=2)  # cycle 1: counting
    dispatcher.recover_stalled_agy(max_retries=2)  # cycle 2: full attempt
    dispatcher.recover_stalled_agy(max_retries=2)  # cycle 3: retry-only path
    assert cleanups == [0], f"kill-all ran more than once: {cleanups}"
    assert len(transitions) == 2  # attempt + retry
    assert launched == ["ISS-AGY"]  # escalation completed on retry
    assert any("stalled" in c for c in comments)
    row = _tracker_row(db)
    assert row[1] == 1  # escalated flag preserved


def test_recovery_persistent_failure_never_restorms(monkeypatch, tmp_path):
    """Transition fails every cycle: kill-all runs exactly once, later
    cycles only retry the transition (no relaunches)."""
    db, transitions, _comments, launched, cleanups = _patch_recovery(
        monkeypatch, tmp_path, fail_times=99)
    for _ in range(4):
        dispatcher.recover_stalled_agy(max_retries=2)
    assert cleanups == [0], f"kill-all storm: {cleanups}"
    assert len(transitions) == 3  # cycle-2 attempt + 2 retries
    assert launched == []
    row = _tracker_row(db)
    assert row == (4, 1), f"expected (cycle_count=4, escalated=1), got {row}"


def test_recovery_prunes_stale_rows_when_not_truncated(monkeypatch, tmp_path):
    db, *_rest = _patch_recovery(monkeypatch, tmp_path)
    dispatcher.recover_stalled_agy(max_retries=2)  # creates the row
    assert _tracker_row(db) is not None
    # Issue leaves the agy lane (label cleared externally).
    monkeypatch.setattr(
        dispatcher, "get_issues_with_label", lambda label, max_issues=20: [])
    dispatcher.recover_stalled_agy(max_retries=2)
    assert _tracker_row(db) is None  # fresh episode next time


def test_recovery_keeps_rows_when_fetch_truncated(monkeypatch, tmp_path):
    db, *_ = _patch_recovery(monkeypatch, tmp_path)
    dispatcher.recover_stalled_agy(max_retries=2)
    assert _tracker_row(db) is not None
    full_page = [{"id": f"ISS-{i}", "identifier": f"T-{i}", "title": "t",
                  "labels": ["agent:agy"]} for i in range(20)]
    monkeypatch.setattr(
        dispatcher, "get_issues_with_label",
        lambda label, max_issues=20: list(full_page)
        if label == "agent:agy" else [],
    )
    dispatcher.recover_stalled_agy(max_retries=2)
    # Truncated page: cannot know the full set, so no pruning.
    assert _tracker_row(db) is not None


# ── 3. Thread-safety ───────────────────────────────────────────────


def test_concurrent_record_dispatch_exact_count(dedup):
    errors = []
    n_threads, per_thread = 8, 25

    def worker():
        try:
            for _ in range(per_thread):
                dedup.record_dispatch("ISS-T")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread errors: {errors[:2]}"
    assert dedup._count_dispatches("ISS-T") == n_threads * per_thread


def test_concurrent_legacy_methods_thread_safe(dedup):
    """The older, previously-unlocked methods are safe under threads too."""
    errors = []

    def worker(n):
        try:
            for i in range(30):
                dedup.mark_processed(f"ISS-{n}", "agent:kai", f"cycle-{i}")
                dedup.is_processed(f"ISS-{n}", "agent:kai", f"cycle-{i}")
                dedup.snapshot_labels(f"ISS-{n}", ["agent:kai"], f"cycle-{i}")
                dedup.had_label(f"ISS-{n}", "agent:kai")
                dedup.get_cycle_count(f"ISS-{n}", "agent:kai")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread errors: {errors[:2]}"
    assert dedup.get_cycle_count("ISS-0", "agent:kai") == 30


# ── 4. Env validation ──────────────────────────────────────────────


def _import_with_env(env_overrides):
    env = dict(os.environ)
    env.update(env_overrides)
    probe = (
        f"import sys; sys.path.insert(0, {REPO_ROOT!r});"
        "import prismatic.dispatcher as d, prismatic.dedup as dd;"
        "print('CAP=', d.EventRouterDedup.MAX_DISPATCH_COUNT_PER_ISSUE);"
        "print('DEDUP_CAP=', dd.MAX_DISPATCH_COUNT_PER_ISSUE);"
        "print('RECOVER=', d.MAX_CYCLES_BEFORE_RECOVER)"
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, env=env, timeout=180, check=False,
    )


def test_env_garbage_falls_back_to_defaults():
    proc = _import_with_env({
        "PRISMATIC_MAX_DISPATCH_PER_ISSUE": "abc",
        "PRISMATIC_MAX_CYCLES_BEFORE_RECOVER": "xyz",
    })
    assert proc.returncode == 0, proc.stderr
    assert "WARNING" in proc.stdout or "WARNING" in proc.stderr
    assert "CAP= 20" in proc.stdout
    assert "DEDUP_CAP= 20" in proc.stdout
    assert "RECOVER= 6" in proc.stdout


def test_cap_boundaries_still_hold(dedup):
    for _ in range(CAP - 1):
        dedup.record_dispatch("ISS-B")
    assert dedup.is_over_dispatch_cap("ISS-B") is False
    dedup.record_dispatch("ISS-B")
    assert dedup.is_over_dispatch_cap("ISS-B") is True
