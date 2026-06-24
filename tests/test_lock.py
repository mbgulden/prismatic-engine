"""Tests for prismatic.lock — centralized file-locking registry.

GRO-2402 follow-up: prismatic.lock is used by:
- The gateway (reads the registry to show active locks)
- Agents (claim/release locks to prevent edit conflicts)

Until now, the CLI subcommands (cmd_lock, cmd_unlock, cmd_status, cmd_heartbeat)
were registered inside prismatic/lock.py but NEVER EXPOSED as `prismatic lock ...`
subcommands. They only worked via `python -m prismatic.lock`.

These tests cover:
1. The lock registry core operations (read/write JSON, prune stale)
2. The CLI commands (lock/unlock/status/heartbeat)
3. The full `prismatic lock ...` subcommand dispatch via prismatic.cli.run()
"""
from __future__ import annotations

import pytest

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.lock import (  # noqa: E402
    _read_locks,
    _write_locks,
    _prune_stale,
    cmd_lock,
    cmd_unlock,
    cmd_status,
    cmd_heartbeat,
    _duration_ms,
    LOCK_FILE,
)


# ── Fixture: isolate lock registry to tmp_path ──────────────────────
@pytest.fixture
def isolated_locks(tmp_path, monkeypatch):
    """Redirect LOCK_FILE to a tmp path so tests don't pollute real registry."""
    test_lock_file = tmp_path / "swarm_locks.json"
    monkeypatch.setattr("prismatic.lock.LOCK_FILE", test_lock_file)
    return test_lock_file


# ── Core registry operations ────────────────────────────────────────
def test_read_empty_locks(tmp_path, monkeypatch,
    isolated_locks):
    """No lock registry file → empty list (no error)."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    # _read_locks resolves to $HOME/.antigravity/swarm_locks.json
    locks = _read_locks()
    assert locks == []


def test_write_and_read_locks_round_trip(tmp_path, monkeypatch,
    isolated_locks):
    """Write 2 locks, read them back, verify shape."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    sample = [
        {"file": "foo.py", "agent": "fred", "acquired_at": time.time() * 1000},
        {"file": "bar.py", "agent": "kai", "acquired_at": time.time() * 1000},
    ]
    _write_locks(sample)
    locks = _read_locks()
    assert len(locks) == 2
    assert {l["file"] for l in locks} == {"foo.py", "bar.py"}
    assert {l["agent"] for l in locks} == {"fred", "kai"}


def test_write_creates_parent_dir(tmp_path, monkeypatch,
        isolated_locks):
    """Write to a path with no parent dir → auto-creates."""
    # Use a nested path that doesn't exist yet
    nested_lock = tmp_path / "subdir" / "more" / "swarm_locks.json"
    monkeypatch.setattr("prismatic.lock.LOCK_FILE", nested_lock)
    # Should not raise even though subdir/more doesn't exist
    _write_locks([{"filePath": "x", "agentId": "y", "timestamp": 0, "lastHeartbeat": 0}])
    assert nested_lock.parent.exists()


# ── Prune stale ──────────────────────────────────────────────────────
def test_prune_stale_removes_old_entries():
    """Locks older than stale_ttl (5 min default) → removed."""
    now_ms = int(time.time() * 1000)
    stale_ttl = 300_000  # matches STALE_TTL_MS
    locks = [
        {"filePath": "old.py", "agentId": "a",
         "timestamp": now_ms - 10 * 60_000,
         "lastHeartbeat": now_ms - 10 * 60_000},
        {"filePath": "fresh.py", "agentId": "b",
         "timestamp": now_ms - 10_000,
         "lastHeartbeat": now_ms - 10_000},
    ]
    pruned, removed = _prune_stale(locks)
    assert removed == 1
    assert [l["filePath"] for l in pruned] == ["fresh.py"]


def test_prune_stale_returns_zero_when_nothing_stale():
    now_ms = int(time.time() * 1000)
    locks = [{"filePath": "fresh.py", "agentId": "a",
              "timestamp": now_ms, "lastHeartbeat": now_ms}]
    pruned, removed = _prune_stale(locks)
    assert removed == 0
    assert len(pruned) == 1


# ── Duration formatting ──────────────────────────────────────────────
def test_duration_ms():
    result = _duration_ms(int(time.time() * 1000) - 500)
    assert "ms" in result or "s" in result  # graceful for any short duration


def test_duration_seconds():
    result = _duration_ms(int(time.time() * 1000) - 5000)
    assert "s" in result


def test_duration_minutes():
    result = _duration_ms(int(time.time() * 1000) - 120_000)
    assert "m" in result


def test_duration_hours():
    result = _duration_ms(int(time.time() * 1000) - 7_200_000)  # 2h
    assert "h" in result


def test_duration_with_suffix():
    result = _duration_ms(int(time.time() * 1000) - 5000, suffix=" ago")
    assert " ago" in result


# ── CLI commands (cmd_*) — these directly modify the registry ───────
def test_cmd_lock_acquires_lock(tmp_path, monkeypatch,
    isolated_locks, capsys):
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    rc = cmd_lock("foo.py", "fred")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Locked" in out
    assert "fred" in out
    # Lock was actually written
    locks = _read_locks()
    assert len(locks) == 1
    assert locks[0]["filePath"] == "foo.py"
    assert locks[0]["agentId"] == "fred"


def test_cmd_lock_rejects_duplicate(tmp_path, monkeypatch,
    isolated_locks, capsys):
    """Same agent re-locking → refresh heartbeat (not error)."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    rc1 = cmd_lock("foo.py", "fred")
    rc2 = cmd_lock("foo.py", "fred")
    assert rc1 == 0
    assert rc2 == 0  # idempotent refresh
    locks = _read_locks()
    assert len(locks) == 1


def test_cmd_lock_rejects_other_agent(tmp_path, monkeypatch,
    isolated_locks, capsys):
    """Different agent can't lock a held file → returns 1."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    cmd_lock("foo.py", "fred")
    capsys.readouterr()
    rc = cmd_lock("foo.py", "kai")
    assert rc == 1
    locks = _read_locks()
    assert locks[0]["agentId"] == "fred"  # unchanged


def test_cmd_unlock_releases_lock(tmp_path, monkeypatch,
    isolated_locks, capsys):
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    cmd_lock("foo.py", "fred")
    capsys.readouterr()
    rc = cmd_unlock("foo.py", "fred")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Unlocked" in out or "Not locked" in out
    locks = _read_locks()
    assert all(l["filePath"] != "foo.py" for l in locks)


def test_cmd_unlock_returns_0_when_not_locked(tmp_path, monkeypatch,
    isolated_locks, capsys):
    """Unlock on an unlocked file → returns 0 with 'Not locked' message."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    rc = cmd_unlock("never_locked.py", "fred")
    assert rc == 0


def test_cmd_status_shows_locks(tmp_path, monkeypatch,
    isolated_locks, capsys):
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    cmd_lock("foo.py", "fred")
    cmd_lock("bar.py", "kai")
    capsys.readouterr()  # clear prior output
    rc = cmd_status()
    assert rc == 0
    out = capsys.readouterr().out
    assert "fred" in out
    assert "kai" in out


def test_cmd_status_empty(tmp_path, monkeypatch,
    isolated_locks, capsys):
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    rc = cmd_status()
    assert rc == 0
    out = capsys.readouterr().out
    assert "No active locks" in out


def test_cmd_heartbeat_refreshes(tmp_path, monkeypatch,
    isolated_locks, capsys):
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    cmd_lock("foo.py", "fred")
    locks_before = _read_locks()
    original_hb = locks_before[0]["lastHeartbeat"]
    time.sleep(0.05)
    rc = cmd_heartbeat("foo.py", "fred")
    assert rc == 0
    locks_after = _read_locks()
    assert len(locks_after) == 1
    assert locks_after[0]["lastHeartbeat"] >= original_hb


# ── Full CLI dispatch via prismatic.cli.run() — GRO-2402 fix ────────
def test_cli_lock_subcommand_visible(capsys):
    """`prismatic --help` lists the 'lock' subcommand."""
    from prismatic.cli import run
    try:
        run(["--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "lock" in out.lower()


def test_cli_lock_status_dispatches(tmp_path, monkeypatch,
        isolated_locks):
    """`prismatic lock status` routes to lock.cmd_status."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    from prismatic.cli import run
    rc = run(["lock", "status"])
    assert rc == 0


def test_cli_lock_lock_unlock_full_cycle(tmp_path, monkeypatch,
    isolated_locks):
    """Full lock/unlock cycle via the outer CLI."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    from prismatic.cli import run

    # Lock
    rc = run(["lock", "lock", "foo.py", "fred"])
    assert rc == 0
    # Status should show it
    locks = _read_locks()
    assert any(l["filePath"] == "foo.py" and l["agentId"] == "fred" for l in locks)
    # Unlock
    rc = run(["lock", "unlock", "foo.py", "fred"])
    assert rc == 0
    # Status should be empty
    locks = _read_locks()
    assert not any(l["filePath"] == "foo.py" for l in locks)


def test_cli_lock_heartbeat_via_outer_cli(tmp_path, monkeypatch,
    isolated_locks):
    """`prismatic lock heartbeat ...` dispatches correctly."""
    isolated_locks  # fixture redirects LOCK_FILE to tmp_path
    from prismatic.cli import run

    rc = run(["lock", "lock", "foo.py", "fred"])
    assert rc == 0
    rc = run(["lock", "heartbeat", "foo.py", "fred"])
    assert rc == 0
    # Still locked
    locks = _read_locks()
    assert any(l["filePath"] == "foo.py" for l in locks)


def test_cli_lock_help_shows_subcommands(capsys):
    """`prismatic lock --help` shows the nested subcommand list."""
    from prismatic.cli import run
    try:
        run(["lock", "--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "lock" in out.lower()
    assert "unlock" in out.lower()
    assert "status" in out.lower()
    assert "heartbeat" in out.lower()