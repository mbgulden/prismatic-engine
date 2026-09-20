"""Hardening tests for the Hypervisor immutable audit ledger.

Covers the failure modes the happy-path suite does not:
- Storage-layer append-only enforcement (UPDATE/DELETE rejected).
- Tamper detection: modified payloads and broken chain links are caught.
- Concurrent writers (threads and the cross-process path) keep one valid chain.
- Crash between INSERT and COMMIT leaves no partial entry.
- Invalid inputs fail closed with clear errors (checkpoint mode, limit,
  non-serializable payload, duplicate event ids, blank identifiers,
  non-dict payloads).
- The default DB path carries no hardcoded home directory (POSIX or Windows).
"""

import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from prismatic.hypervisor import ledger as ledger_mod
from prismatic.hypervisor.ledger import (
    HypervisorLedger,
    _compute_merkle_root,
    _get_default_ledger_db,
)


@pytest.fixture
def ledger(tmp_path):
    return HypervisorLedger(db_path=tmp_path / "ledger.db")


def _record(ledger, n=3, task_id="T-1", producer="bot", action="ACT"):
    entries = []
    for i in range(n):
        entries.append(
            ledger.record_event(
                task_id=task_id, producer=producer, action=action, payload={"i": i}
            )
        )
    return entries


def test_ledger_rejects_update_and_delete(ledger):
    """Append-only is enforced at the storage layer, not just by API convention."""
    entries = _record(ledger, n=2)

    with pytest.raises(sqlite3.Error, match="append-only"):
        conn = sqlite3.connect(str(ledger.db_path))
        try:
            conn.execute(
                "UPDATE ledger_events SET payload_json = '{}' WHERE id = ?",
                (entries[0].id,),
            )
        finally:
            conn.close()

    with pytest.raises(sqlite3.Error, match="append-only"):
        conn = sqlite3.connect(str(ledger.db_path))
        try:
            conn.execute("DELETE FROM ledger_events WHERE id = ?", (entries[0].id,))
        finally:
            conn.close()

    # The ledger is untouched and still verifies.
    assert ledger.verify_chain_integrity()["valid"] is True
    assert len(ledger.list_events(limit=10)) == 2


def test_verify_chain_detects_tampered_payload(ledger):
    """A modified entry payload breaks its hash and is detected."""
    entries = _record(ledger, n=3)

    # Simulate privileged tampering: drop the enforcement trigger, edit, re-check.
    conn = sqlite3.connect(str(ledger.db_path))
    try:
        conn.execute("DROP TRIGGER ledger_events_no_update;")
        conn.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE id = ?",
            ('{"i": 999}', entries[1].id),
        )
        conn.commit()
    finally:
        conn.close()

    result = ledger.verify_chain_integrity()
    assert result["valid"] is False
    assert result["broken_at_id"] == entries[1].id
    assert "hash mismatch" in result["error"]


def test_verify_chain_detects_broken_link(ledger):
    """A rewritten prev_hash is detected as a chain break at the right entry."""
    entries = _record(ledger, n=3)

    conn = sqlite3.connect(str(ledger.db_path))
    try:
        conn.execute("DROP TRIGGER ledger_events_no_update;")
        conn.execute(
            "UPDATE ledger_events SET prev_hash = ? WHERE id = ?",
            ("f" * 64, entries[2].id),
        )
        conn.commit()
    finally:
        conn.close()

    result = ledger.verify_chain_integrity()
    assert result["valid"] is False
    assert result["broken_at_id"] == entries[2].id
    assert "Hash chain break" in result["error"]


def test_concurrent_thread_writers_keep_single_valid_chain(tmp_path):
    """32 threads x 25 events: one chain, no forks, no lost links."""
    ledger = HypervisorLedger(db_path=tmp_path / "ledger.db")

    def write_batch(worker):
        for i in range(25):
            ledger.record_event(
                task_id=f"T-{worker}",
                producer="bot",
                action="ACT",
                payload={"worker": worker, "i": i},
            )

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(write_batch, range(32)))

    assert len(ledger.list_events(limit=1000)) == 800
    result = ledger.verify_chain_integrity()
    assert result["valid"] is True
    assert result["verified_entries"] == 800


def test_crash_between_insert_and_commit_leaves_no_partial_entry(ledger):
    """A crash after INSERT but before COMMIT must roll back: no torn entries."""
    before = _record(ledger, n=2)

    # Simulate the crash: raw connection, INSERT, then ROLLBACK (no COMMIT).
    conn = sqlite3.connect(str(ledger.db_path))
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """INSERT INTO ledger_events
               (event_id, task_id, producer, action, payload_json, timestamp, prev_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            ("evt_crash", "T-1", "bot", "ACT", "{}", 0.0, "x" * 64, "y" * 64),
        )
        conn.execute("ROLLBACK;")
    finally:
        conn.close()

    # The crashed write is gone; the chain still verifies end to end.
    events = ledger.list_events(limit=10)
    assert all(e.event_id != "evt_crash" for e in events)
    assert [e.id for e in events] == [before[1].id, before[0].id]
    assert ledger.verify_chain_integrity()["valid"] is True

    # And the ledger keeps working afterwards.
    e3 = ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    assert e3.prev_hash == before[1].entry_hash


def test_checkpoint_wal_rejects_invalid_mode(ledger):
    with pytest.raises(ValueError, match="Invalid WAL checkpoint mode"):
        ledger.checkpoint_wal(mode="'; DROP TABLE ledger_events; --")
    with pytest.raises(ValueError, match="Invalid WAL checkpoint mode"):
        ledger.checkpoint_wal(mode="banana")


def test_checkpoint_wal_accepts_valid_modes(ledger):
    _record(ledger, n=2)
    for mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE", "passive"):
        result = ledger.checkpoint_wal(mode=mode)
        assert set(result) == {"busy", "log_frames", "checkpointed"}


def test_record_event_rejects_non_serializable_payload(ledger):
    with pytest.raises(ValueError, match="JSON-serializable"):
        ledger.record_event(
            task_id="T-1", producer="bot", action="ACT", payload={"x": object()}
        )
    # Nothing was recorded.
    assert ledger.list_events(limit=10) == []


def test_list_events_rejects_invalid_limit(ledger):
    _record(ledger, n=2)
    with pytest.raises(ValueError, match="positive int"):
        ledger.list_events(limit=0)
    with pytest.raises(ValueError, match="positive int"):
        ledger.list_events(limit=-5)


def test_duplicate_event_id_fails_closed(ledger):
    ledger.record_event(task_id="T-1", producer="bot", action="ACT", event_id="evt_dup")
    with pytest.raises(ValueError, match="Duplicate ledger event_id"):
        ledger.record_event(
            task_id="T-1", producer="bot", action="ACT", event_id="evt_dup"
        )
    assert len(ledger.list_events(limit=10)) == 1


def test_default_db_path_has_no_hardcoded_home(monkeypatch):
    """The default path must never point at another user's home directory."""
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    path = _get_default_ledger_db()
    assert path.name == "hypervisor_ledger.db"
    assert "ubuntu" not in str(path)


def test_default_db_path_honors_prismatic_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    assert _get_default_ledger_db() == tmp_path / "hypervisor_ledger.db"


def test_merkle_root_and_verify_on_empty_ledger(ledger):
    root = ledger.get_merkle_root()
    assert root["entry_count"] == 0
    assert root["merkle_root"] == _compute_merkle_root([])
    assert len(root["merkle_root"]) == 64

    integrity = ledger.verify_chain_integrity()
    assert integrity["valid"] is True
    assert integrity["verified_entries"] == 0


def test_merkle_root_changes_with_each_event(ledger):
    roots = set()
    for _ in range(5):
        ledger.record_event(task_id="T-1", producer="bot", action="ACT")
        roots.add(ledger.get_merkle_root()["merkle_root"])
    assert len(roots) == 5


def test_ledger_survives_close_and_reopen(tmp_path):
    """Committed entries are durable across handles (WAL recovery)."""
    db_path = tmp_path / "ledger.db"
    entries = _record(HypervisorLedger(db_path=db_path), n=3)

    reopened = HypervisorLedger(db_path=db_path)
    assert reopened.verify_chain_integrity()["valid"] is True
    assert [e.event_id for e in reopened.list_events(limit=10)] == [
        e.event_id for e in reversed(entries)
    ]

    # Chain continues correctly after reopen.
    e4 = reopened.record_event(task_id="T-1", producer="bot", action="ACT")
    assert e4.prev_hash == entries[-1].entry_hash


def test_get_hypervisor_ledger_singleton(tmp_path, monkeypatch):
    """The global accessor returns one shared instance."""
    import prismatic.hypervisor.ledger as ledger_mod

    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ledger_mod, "_GLOBAL_LEDGER", None)
    try:
        first = ledger_mod.get_hypervisor_ledger()
        second = ledger_mod.get_hypervisor_ledger()
        assert first is second
        assert first.db_path == tmp_path / "hypervisor_ledger.db"
    finally:
        monkeypatch.setattr(ledger_mod, "_GLOBAL_LEDGER", None)


def test_ledger_entry_to_dict_round_trip(ledger):
    entry = ledger.record_event(
        task_id="T-9", producer="bot", action="ACT", payload={"k": "v"}
    )
    d = entry.to_dict()
    assert d["event_id"] == entry.event_id
    assert d["task_id"] == "T-9"
    assert d["payload"] == {"k": "v"}
    assert d["prev_hash"] == "0" * 64
    assert len(d["entry_hash"]) == 64


def test_list_events_producer_filter(ledger):
    _record(ledger, n=2, producer="alice")
    _record(ledger, n=3, producer="bob")
    assert len(ledger.list_events(limit=10, producer="alice")) == 2
    assert len(ledger.list_events(limit=10, producer="bob")) == 3
    assert ledger.list_events(limit=10, producer="nobody") == []


def test_event_id_collision_retries_and_succeeds(ledger, monkeypatch):
    """An auto-generated event_id collision regenerates instead of failing."""
    fixed_time = 1_700_000_000.0
    monkeypatch.setattr("prismatic.hypervisor.ledger.time.time", lambda: fixed_time)
    # First two urandom draws collide, the third is fresh.
    draws = [b"\x00" * 4, b"\x00" * 4, b"\x01" * 4]
    monkeypatch.setattr("os.urandom", lambda n: draws.pop(0))

    e1 = ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    e2 = ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    assert e1.event_id != e2.event_id
    assert ledger.verify_chain_integrity()["valid"] is True


def test_event_id_collision_exhaustion_raises(ledger, monkeypatch):
    """Persistent collisions fail closed instead of looping forever."""
    fixed_time = 1_700_000_000.0
    monkeypatch.setattr("prismatic.hypervisor.ledger.time.time", lambda: fixed_time)
    monkeypatch.setattr("os.urandom", lambda n: b"\x00" * 4)

    ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    with pytest.raises(RuntimeError, match="event_id retries"):
        ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    # The failed attempts left no partial rows behind.
    assert len(ledger.list_events(limit=10)) == 1
    assert ledger.verify_chain_integrity()["valid"] is True


def test_auto_checkpoint_fires_every_hundred_events(ledger):
    for _ in range(250):
        ledger.record_event(task_id="T-1", producer="bot", action="ACT")
    # Reset at 100 and 200 -> 50 events since the last checkpoint.
    assert ledger._events_since_checkpoint == 50
    assert ledger.verify_chain_integrity()["valid"] is True


def test_connection_failure_closes_cleanly(tmp_path, monkeypatch):
    import prismatic.hypervisor.ledger as ledger_mod

    def boom(*args, **kwargs):
        raise sqlite3.Error("simulated connect failure")

    monkeypatch.setattr(ledger_mod.sqlite3, "connect", boom)
    ledger = HypervisorLedger.__new__(HypervisorLedger)
    ledger.db_path = tmp_path / "x.db"
    with pytest.raises(sqlite3.Error, match="simulated connect failure"):
        ledger._get_connection()


def test_default_db_path_falls_back_to_cwd_when_homeless(monkeypatch, tmp_path):
    """Even without any HOME, the default never points at another user's tree."""
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(
        "os.path.expanduser", lambda p: (_ for _ in ()).throw(RuntimeError("no home"))
    )
    monkeypatch.chdir(tmp_path)
    path = _get_default_ledger_db()
    assert path == tmp_path / ".prismatic" / "db" / "hypervisor_ledger.db"


def test_pragma_failure_closes_connection(tmp_path, monkeypatch):
    import prismatic.hypervisor.ledger as ledger_mod

    real_connect = ledger_mod.sqlite3.connect
    real_conn = real_connect(str(tmp_path / "x.db"))

    class PragmaBoomProxy:
        """Wraps a real connection but fails PRAGMA setup statements."""

        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def execute(self, sql, *args, **kwargs):
            if "PRAGMA" in sql:
                raise sqlite3.Error("simulated pragma failure")
            return self._conn.execute(sql, *args, **kwargs)

        def close(self):
            return self._conn.close()

        def __setattr__(self, name, value):
            setattr(self._conn, name, value)

    monkeypatch.setattr(
        ledger_mod.sqlite3, "connect", lambda *a, **k: PragmaBoomProxy(real_conn)
    )
    ledger = HypervisorLedger.__new__(HypervisorLedger)
    ledger.db_path = tmp_path / "x.db"
    with pytest.raises(sqlite3.Error, match="simulated pragma failure"):
        ledger._get_connection()


# ── Input validation, portable default path, cross-process contention ──


@pytest.mark.parametrize("field", ["task_id", "producer", "action"])
@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_record_event_rejects_blank_identifiers(ledger, field, bad):
    """Blank or non-string identifiers fail fast instead of polluting the trail."""
    kwargs = {"task_id": "t", "producer": "p", "action": "a", field: bad}
    with pytest.raises((ValueError, TypeError)):
        ledger.record_event(**kwargs)


def test_record_event_rejects_blank_event_id(ledger):
    with pytest.raises(ValueError, match="event_id"):
        ledger.record_event("t", "p", "a", event_id="  ")


@pytest.mark.parametrize("bad", ["nope", ["x"], 42])
def test_record_event_rejects_non_dict_payload(ledger, bad):
    with pytest.raises(TypeError, match="payload"):
        ledger.record_event("t", "p", "a", payload=bad)


def test_record_event_none_payload_becomes_empty_dict(ledger):
    entry = ledger.record_event("t", "p", "a", payload=None)
    assert entry.payload == {}


def test_default_db_path_uses_platform_tempdir_on_windows(tmp_path, monkeypatch):
    """No hardcoded C:/temp: the Windows default resolves via tempfile.gettempdir().

    (Patching os.name is not viable: pathlib.Path dispatches on os.name
    dynamically, so faking "nt" breaks every Path() on Linux. The Windows
    branch logic is therefore factored into its own helper, tested directly.)
    """
    import tempfile as tempfile_mod

    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.setattr(tempfile_mod, "gettempdir", lambda: str(tmp_path))
    assert (
        ledger_mod._windows_default_ledger_db()
        == tmp_path / "prismatic_db" / "hypervisor_ledger.db"
    )
    assert "C:/temp" not in Path(ledger_mod.__file__).read_text()


def _cross_process_worker(db_path: str, worker_id: int, count: int) -> None:
    """Top-level worker so multiprocessing 'spawn' can pickle it."""
    from prismatic.hypervisor.ledger import HypervisorLedger

    ledger = HypervisorLedger(db_path=db_path)
    for i in range(count):
        ledger.record_event(
            task_id=f"xp-{worker_id}",
            producer=f"worker-{worker_id}",
            action="cross_process_write",
            payload={"i": i},
        )


def test_cross_process_concurrent_appends(tmp_path):
    """Separate OS processes appending to one DB file keep a single valid chain.

    Exercises the BEGIN IMMEDIATE + WAL path across real process boundaries,
    not just threads sharing one connection pool.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = str(tmp_path / "shared.db")
    # Warm the DB in the parent so children contend only on appends.
    HypervisorLedger(db_path=db_path).record_event("t0", "parent", "init")

    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    old_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = env["PYTHONPATH"]
    try:
        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(target=_cross_process_worker, args=(db_path, wid, 25))
            for wid in range(4)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=180)
            assert p.exitcode == 0
    finally:
        if old_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old_pythonpath

    ledger = HypervisorLedger(db_path=db_path)
    assert len(ledger.list_events(limit=1000)) == 1 + 4 * 25
    assert ledger.verify_chain_integrity()["valid"] is True
