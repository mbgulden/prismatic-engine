"""Tests for prismatic.stale_lock_watcher — cron-friendly dead-lock cleanup.

GRO-2402 follow-up: stale_lock_watcher.py is a small cron-friendly script
that prunes stale locks from the centralized lock registry. Until now it
had zero tests. A bug here could either silently leave dead locks or
aggressively remove live ones.

Tests cover:
- No lock registry → exit 0, "nothing to watch" message
- Corrupt registry → exit 1, graceful warning
- Non-list registry → exit 1
- All healthy → exit 0
- Mixed (some stale) → exit 2, prunes stale, keeps healthy
- All stale → exit 2, empties registry
- TTL boundary (just expired vs just fresh)
- Atomic write (uses .tmp + os.replace)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

import prismatic.stale_lock_watcher as watcher
from prismatic.stale_lock_watcher import main, STALE_TTL_MS


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Redirect PRISMATIC_HOME to tmp_path for the test."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    monkeypatch.setattr(watcher, "LOCK_FILE", tmp_path / ".antigravity" / "swarm_locks.json")
    return tmp_path / ".antigravity"


def _write_locks(path: Path, locks: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(locks))


# ── No lock registry ─────────────────────────────────────────────────
def test_no_lock_registry_returns_zero(lock_dir, capsys):
    """No lock file → exit 0, helpful message."""
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "nothing to watch" in captured.out.lower() or "no lock" in captured.out.lower()


# ── Corrupt / invalid registry ───────────────────────────────────────
def test_corrupt_json_returns_1(tmp_path, monkeypatch,
    lock_dir, capsys):
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    lock_file = tmp_path / ".antigravity" / "swarm_locks.json"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("not json {{{")
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "unreadable" in captured.out.lower() or "corrupt" in captured.out.lower()


def test_non_list_registry_returns_1(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Registry exists but is a dict instead of list → handled gracefully."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    lock_file = tmp_path / ".antigravity" / "swarm_locks.json"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({"not": "a list"}))
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "list" in captured.out.lower()


# ── All healthy ──────────────────────────────────────────────────────
def test_all_healthy_returns_zero(tmp_path, monkeypatch,
    lock_dir, capsys):
    """No stale locks → exit 0, positive message."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "foo.py", "agentId": "fred",
         "timestamp": now_ms, "lastHeartbeat": now_ms},
        {"filePath": "bar.py", "agentId": "kai",
         "timestamp": now_ms, "lastHeartbeat": now_ms},
    ])
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "healthy" in captured.out.lower() or "all" in captured.out.lower()


# ── Mixed: some stale ───────────────────────────────────────────────
def test_mixed_prunes_only_stale(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Fresh locks kept, stale locks removed."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    stale_age_ms = STALE_TTL_MS + 60_000  # 1 min past stale threshold
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "fresh.py", "agentId": "fred",
         "timestamp": now_ms, "lastHeartbeat": now_ms},
        {"filePath": "stale.py", "agentId": "kai",
         "timestamp": now_ms - stale_age_ms,
         "lastHeartbeat": now_ms - stale_age_ms},
    ])
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2  # pruned → cron sees action taken
    assert "pruned" in captured.out.lower()

    # Verify the registry now contains only the fresh lock
    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    assert len(remaining) == 1
    assert remaining[0]["filePath"] == "fresh.py"


def test_mixed_keeps_multiple_fresh(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Multiple fresh locks all preserved."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": f"file_{i}.py", "agentId": f"agent_{i}",
         "timestamp": now_ms, "lastHeartbeat": now_ms}
        for i in range(5)
    ])
    main()
    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    assert len(remaining) == 5


# ── All stale ────────────────────────────────────────────────────────
def test_all_stale_empties_registry(tmp_path, monkeypatch,
    lock_dir, capsys):
    """All locks stale → registry becomes empty list."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "old1.py", "agentId": "a",
         "timestamp": very_old, "lastHeartbeat": very_old},
        {"filePath": "old2.py", "agentId": "b",
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2
    assert "pruned 2" in captured.out.lower()

    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    assert remaining == []


# ── TTL boundary ─────────────────────────────────────────────────────
def test_just_under_ttl_is_kept(tmp_path, monkeypatch,
    lock_dir):
    """Lock just under stale TTL → kept."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    just_fresh = now_ms - (STALE_TTL_MS - 1_000)  # 1 sec under threshold
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "edge.py", "agentId": "fred",
         "timestamp": just_fresh, "lastHeartbeat": just_fresh},
    ])
    main()
    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    assert len(remaining) == 1
    assert remaining[0]["filePath"] == "edge.py"


def test_just_over_ttl_is_pruned(tmp_path, monkeypatch,
    lock_dir):
    """Lock just over stale TTL → pruned."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    just_stale = now_ms - (STALE_TTL_MS + 1_000)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "edge.py", "agentId": "fred",
         "timestamp": just_stale, "lastHeartbeat": just_stale},
    ])
    main()
    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    assert remaining == []


# ── Fallback to timestamp when lastHeartbeat missing ──────────────────
def test_falls_back_to_timestamp_when_no_lastHeartbeat(tmp_path, monkeypatch,
    lock_dir):
    """Lock without lastHeartbeat → uses timestamp as fallback."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "no_hb.py", "agentId": "fred",
         "timestamp": very_old},  # no lastHeartbeat
    ])
    main()
    remaining = json.loads((tmp_path / ".antigravity" / "swarm_locks.json").read_text())
    # Old timestamp → pruned
    assert remaining == []


# ── Missing required fields handled gracefully ───────────────────────
def test_lock_with_no_timestamp_or_hb_defaults_to_zero_age(tmp_path, monkeypatch,
    lock_dir):
    """Lock with neither timestamp nor lastHeartbeat → last_hb defaults to 0 → stale → pruned."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "old.py", "agentId": "fred"},  # no timestamp at all
    ])
    rc = main()
    assert rc == 2  # pruned


def test_lock_with_missing_file_path_displays_question_mark(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Stale lock with missing filePath → displays '?' (no crash)."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"agentId": "fred",  # no filePath
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    rc = main()
    assert rc == 2
    captured = capsys.readouterr()
    # Should still complete (with '?' for missing filePath)
    assert "?" in captured.out


def test_lock_with_missing_agent_id_displays_question_mark(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Stale lock with missing agentId → displays '?' (no crash)."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    _write_locks(tmp_path / ".antigravity" / "swarm_locks.json", [
        {"filePath": "foo.py",  # no agentId
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    rc = main()
    assert rc == 2
    captured = capsys.readouterr()
    assert "?" in captured.out


# ── Atomic write behavior ────────────────────────────────────────────
def test_no_tmp_file_left_after_success(tmp_path, monkeypatch,
    lock_dir):
    """After pruning, no leftover .tmp file."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    lock_file = tmp_path / ".antigravity" / "swarm_locks.json"
    _write_locks(lock_file, [
        {"filePath": "old.py", "agentId": "a",
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    main()
    # os.replace is atomic — the .tmp should not remain
    tmp_file = lock_file.with_suffix(".tmp")
    assert not tmp_file.exists()


# ── Constants ────────────────────────────────────────────────────────
def test_stale_ttl_is_5_minutes():
    """GRO-2402 invariant: TTL is 5 minutes (300_000 ms)."""
    assert STALE_TTL_MS == 300_000


# ── Exit code semantics (the comment says exit 2 = pruned) ────────────
def test_exit_2_only_when_pruned(tmp_path, monkeypatch,
    lock_dir):
    """Exit code 2 is specifically used when pruning happened (cron visibility)."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)
    lock_file = tmp_path / ".antigravity" / "swarm_locks.json"
    # Case 1: all healthy → exit 0
    _write_locks(lock_file, [
        {"filePath": "fresh.py", "agentId": "a",
         "timestamp": now_ms, "lastHeartbeat": now_ms},
    ])
    assert main() == 0
    # Case 2: stale exists → exit 2
    _write_locks(lock_file, [
        {"filePath": "stale.py", "agentId": "a",
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    assert main() == 2
    # Case 3: no file → exit 0
    lock_file.unlink()
    assert main() == 0


# ── Integration: simulates cron invocation ──────────────────────────
def test_full_cron_simulation(tmp_path, monkeypatch,
    lock_dir, capsys):
    """Simulates the cron calling main() multiple times."""
    monkeypatch.setattr(watcher, "PRISMATIC_HOME", tmp_path)
    now_ms = int(time.time() * 1000)
    very_old = now_ms - (STALE_TTL_MS * 2)

    # First run: register a fresh lock
    lock_file = tmp_path / ".antigravity" / "swarm_locks.json"
    _write_locks(lock_file, [
        {"filePath": "foo.py", "agentId": "fred",
         "timestamp": now_ms, "lastHeartbeat": now_ms},
    ])
    assert main() == 0  # healthy

    # Simulate time passing: lock becomes stale
    _write_locks(lock_file, [
        {"filePath": "foo.py", "agentId": "fred",
         "timestamp": very_old, "lastHeartbeat": very_old},
    ])
    assert main() == 2  # pruned

    # Verify the file is now empty
    remaining = json.loads(lock_file.read_text())
    assert remaining == []
