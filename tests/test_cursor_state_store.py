"""Tests for the CursorStateStore durability primitive (rescued from #373).

Covers: canonical path validation, CursorPreState snapshots (ABSENT/PRESENT,
symlink/permission/hardlink rejection), atomic writes (0600, temp cleanup,
symlink/permission refusal), lock acquire/release, and every safe_rollback
CursorOutcome.
"""

from __future__ import annotations

import multiprocessing
import os
import stat
import time
from pathlib import Path

import pytest

from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer

CursorLock = consumer.CursorLock
CursorOutcome = consumer.CursorOutcome
CursorStateStore = consumer.CursorStateStore


@pytest.fixture()
def state_path(tmp_path):
    # resolve() first: _validate_state_path_strict rejects symlinked parents
    # and noncanonical aliases, and tmp roots can be symlinked on some hosts.
    return str(tmp_path.resolve() / "cursor.json")


def _mode(path: str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_outcome_enum_members():
    assert {m.name for m in CursorOutcome} == {
        "NO_MUTATION_FAIL_CLOSED",
        "EXACT_ROLLBACK_COMPLETE",
        "CONTENDER_STATE_PRESERVED",
        "RECOVERY_REQUIRED_BACKUPS_RETAINED",
        "SUCCESS",
    }


def test_constructor_rejects_symlink_and_directory(state_path, tmp_path):
    link = str(tmp_path.resolve() / "link.json")
    os.symlink("/nonexistent", link)
    with pytest.raises(ValueError, match="symlink"):
        CursorStateStore(link)
    with pytest.raises(ValueError, match="[Rr]egular file"):
        CursorStateStore(str(tmp_path.resolve()))
    store = CursorStateStore(state_path)
    assert os.path.isabs(store.state_file_path)


def test_write_atomic_creates_restrictive_file(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b'{"last_rowid": 42}\n')
    assert Path(state_path).read_bytes() == b'{"last_rowid": 42}\n'
    assert _mode(state_path) == 0o600
    leftovers = [p for p in Path(state_path).parent.iterdir() if ".dispatch_cursor_tmp_" in p.name]
    assert leftovers == []


def test_write_atomic_replaces_existing(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    store.write_atomic(b"v2")
    assert Path(state_path).read_bytes() == b"v2"
    assert _mode(state_path) == 0o600


def test_write_atomic_refuses_symlink_target(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    os.remove(state_path)
    os.symlink("/etc/hostname", state_path)
    try:
        with pytest.raises(ValueError, match="symlink"):
            store.write_atomic(b"v2")
    finally:
        os.remove(state_path)


def test_write_atomic_refuses_unsafe_permissions(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    os.chmod(state_path, 0o644)
    with pytest.raises(ValueError, match="[Uu]nsafe.*permissions"):
        store.write_atomic(b"v2")
    assert Path(state_path).read_bytes() == b"v1"


def test_snapshot_absent(state_path):
    store = CursorStateStore(state_path)
    pre = store.snapshot_prestate()
    assert pre.kind == "ABSENT"
    assert pre.bytes is None


def test_snapshot_present_records_identity(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"hello")
    pre = store.snapshot_prestate()
    assert pre.kind == "PRESENT"
    assert pre.bytes == b"hello"
    assert pre.size == 5
    st = os.lstat(state_path)
    assert (pre.dev, pre.ino, pre.mode, pre.uid, pre.nlink) == (
        st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink,
    )


def test_snapshot_rejects_symlink(state_path, tmp_path):
    real = str(tmp_path.resolve() / "real.json")
    Path(real).write_bytes(b"x")
    os.chmod(real, 0o600)
    link = str(tmp_path.resolve() / "alias.json")
    os.symlink(real, link)
    with pytest.raises(ValueError, match="symlink"):
        CursorStateStore(link)


def test_snapshot_rejects_hardlink(state_path, tmp_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    os.link(state_path, str(tmp_path.resolve() / "hard.json"))
    try:
        with pytest.raises(ValueError, match="[Hh]ard link"):
            store.snapshot_prestate()
    finally:
        os.remove(str(tmp_path.resolve() / "hard.json"))


def test_snapshot_rejects_unsafe_permissions(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    os.chmod(state_path, 0o640)
    with pytest.raises(ValueError, match="[Uu]nsafe permissions"):
        store.snapshot_prestate()


def test_lock_acquire_release_cycle(state_path):
    store = CursorStateStore(state_path)
    store.acquire_lock()
    assert Path(state_path + ".lock").exists()
    store.release_lock()
    # re-acquire after release works
    store.acquire_lock()
    store.release_lock()


def test_safe_rollback_exact_complete_removes_when_prestate_absent(state_path):
    store = CursorStateStore(state_path)
    store.snapshot_prestate()  # ABSENT
    store.write_atomic(b"v2")
    outcome = store.safe_rollback(b"v2")
    assert outcome is CursorOutcome.EXACT_ROLLBACK_COMPLETE
    assert not os.path.exists(state_path)


def test_safe_rollback_exact_complete_restores_prior_bytes(state_path):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    store.snapshot_prestate()  # PRESENT v1
    store.write_atomic(b"v2")
    outcome = store.safe_rollback(b"v2")
    assert outcome is CursorOutcome.EXACT_ROLLBACK_COMPLETE
    assert Path(state_path).read_bytes() == b"v1"


def test_safe_rollback_contender_state_preserved(state_path):
    store = CursorStateStore(state_path)
    store.snapshot_prestate()
    store.write_atomic(b"v2")
    # a contender wrote after us; rollback must not clobber it
    Path(state_path).write_bytes(b"v3")
    os.chmod(state_path, 0o600)
    outcome = store.safe_rollback(b"v2")
    assert outcome is CursorOutcome.CONTENDER_STATE_PRESERVED
    assert Path(state_path).read_bytes() == b"v3"


def _hold_lock_forever(path, ready):
    from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer

    lock = consumer.CursorLock(path)
    lock.acquire()
    ready.set()
    time.sleep(60)


def test_safe_rollback_no_mutation_when_lock_contended(state_path):
    store = CursorStateStore(state_path)
    store.snapshot_prestate()
    store.write_atomic(b"v2")
    ready = multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold_lock_forever, args=(state_path, ready))
    proc.start()
    try:
        assert ready.wait(timeout=15), "child never acquired the lock"
        outcome = store.safe_rollback(b"v2")
        assert outcome is CursorOutcome.NO_MUTATION_FAIL_CLOSED
        assert Path(state_path).read_bytes() == b"v2"
    finally:
        proc.terminate()
        proc.join(timeout=10)


def test_safe_rollback_recovery_when_snapshot_fails(state_path, monkeypatch):
    store = CursorStateStore(state_path)
    store.write_atomic(b"v1")
    store.snapshot_prestate()
    store.write_atomic(b"v2")

    def boom():
        raise RuntimeError("simulated snapshot corruption")

    monkeypatch.setattr(store, "snapshot_prestate", boom)
    outcome = store.safe_rollback(b"v2")
    assert outcome is CursorOutcome.RECOVERY_REQUIRED_BACKUPS_RETAINED
    # failed rollback leaves the written bytes in place for manual recovery
    assert Path(state_path).read_bytes() == b"v2"
