"""Phase 5 durability tests: backup/restore for the shared Review Factory DB.

Covers the operator-triggered backup/restore loop:

  1. backup() produces a valid, restorable snapshot (VACUUM INTO).
  2. backup -> mutate -> restore round-trips job state exactly.
  3. restore() is fail-closed: rejects non-SQLite files and SQLite DBs
     missing the factory tables.
  4. restore() keeps a pre-restore safety copy of the live DB.
  5. The queue keeps working after a restore on the same handle
     (what the daemon needs after an operator restore).
  6. prune_backups() retention keeps the newest N only.
"""

import sqlite3
import time
from pathlib import Path

import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.durability import (
    DurabilityError,
    backup_database,
    default_backup_dir,
    list_backups,
    prune_backups,
    restore_database,
    verify_backup,
)
from prismatic.review_factory.models import ReviewJobState
from prismatic.review_factory.queue import ReviewQueue


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "prismatic-state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    return state


@pytest.fixture()
def db(isolated_state):
    d = ReviewFactoryDB()
    d.ensure_tables()
    yield d
    d.close()


def _enqueue(queue, tag):
    return queue.enqueue_completed_work(
        completed_work_id=f"agy-cw-durability-{tag}",
        task_id=f"DUR-{tag}",
        repository="mbgulden/prismatic-engine",
        base_commit="0" * 40,
        candidate_commit="1" * 40,
        changed_paths=["prismatic/review_factory/durability.py"],
    )


class TestBackup:
    def test_backup_creates_valid_snapshot(self, db, isolated_state):
        q = ReviewQueue(db=db)
        job_id = _enqueue(q, "snap")
        out = backup_database()
        assert out.exists()
        assert out.parent == default_backup_dir()
        assert out.name.startswith("rf-backup-")
        info = verify_backup(out)
        assert info["ok"], info["error"]
        assert "review_jobs" in info["tables"]
        # Snapshot contains the job even though the live DB keeps changing.
        snap = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        row = snap.execute(
            "SELECT review_job_id FROM review_jobs WHERE review_job_id = ?",
            (job_id,),
        ).fetchone()
        snap.close()
        assert row is not None
        q.close()

    def test_backup_label_and_custom_dir(self, db, tmp_path):
        dest = tmp_path / "my-backups"
        out = backup_database(dest_dir=dest, label="pretest")
        assert out.parent == dest
        assert "pretest" in out.name

    def test_backup_missing_db_raises(self, isolated_state, monkeypatch, tmp_path):
        # Point at a state dir whose DB was never created.
        monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "empty"))
        with pytest.raises(FileNotFoundError):
            backup_database()


class TestRestore:
    def test_roundtrip_restores_job_state(self, db):
        q = ReviewQueue(db=db)
        job_id = _enqueue(q, "roundtrip")
        out = backup_database()
        # Mutate the live DB after the snapshot: move the job out of QUEUED.
        db.update_review_job_state(job_id, ReviewJobState.VERIFYING)
        assert db.get_review_job(job_id).state == ReviewJobState.VERIFYING.value
        report = restore_database(db, out)
        assert report["live_db"] == str(db.db_path)
        assert Path(report["safety_copy"]).exists()
        restored = db.get_review_job(job_id)
        assert restored.state == ReviewJobState.QUEUED.value
        q.close()

    def test_restore_rejects_non_sqlite(self, db, tmp_path):
        junk = tmp_path / "junk.db"
        junk.write_text("this is not a database")
        with pytest.raises(DurabilityError):
            restore_database(db, junk)

    def test_restore_rejects_db_missing_factory_tables(self, db, tmp_path):
        other = tmp_path / "other.db"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(DurabilityError):
            restore_database(db, other)

    def test_restore_missing_backup_raises(self, db, tmp_path):
        with pytest.raises(DurabilityError):
            restore_database(db, tmp_path / "nope.db")

    def test_restore_preserves_source_backup(self, db):
        q = ReviewQueue(db=db)
        _enqueue(q, "keepme")
        out = backup_database()
        restore_database(db, out)
        assert out.exists()  # restore copies; the backup is not consumed
        assert verify_backup(out)["ok"]
        q.close()

    def test_restore_keeps_safety_copy_of_live_db(self, db):
        q = ReviewQueue(db=db)
        _enqueue(q, "safety")
        out = backup_database()
        _enqueue(q, "after-snapshot")  # only in live DB, not in backup
        report = restore_database(db, out)
        safety = Path(report["safety_copy"])
        assert safety.exists()
        snap = sqlite3.connect(f"file:{safety}?mode=ro", uri=True)
        count = snap.execute("SELECT COUNT(*) FROM review_jobs").fetchone()[0]
        snap.close()
        assert count == 2  # safety copy has the pre-restore live state
        assert db.total_jobs() == 1  # live DB is back to the snapshot
        q.close()

    def test_queue_works_after_restore(self, db):
        """The daemon's queue handle keeps working post-restore."""
        q = ReviewQueue(db=db)
        first = _enqueue(q, "before")
        out = backup_database()
        # Simulate operator restore, then keep using the SAME handle.
        restore_database(db, out)
        second = _enqueue(q, "after")
        leased = q.lease_for_verification(worker_id="daemon-test")
        assert leased is not None
        assert leased.review_job_id in (first, second)
        assert db.get_review_job(second).state in (
            ReviewJobState.QUEUED.value,
            ReviewJobState.VERIFYING.value,
        )
        q.close()

    def test_restore_clears_stale_wal_sidecars(self, db):
        q = ReviewQueue(db=db)
        _enqueue(q, "wal")
        out = backup_database()
        live = Path(db.db_path)
        # Fake stale sidecars the way a crashed writer could leave them.
        # (SQLite itself recreates fresh -wal/-shm on reopen, so we check
        # the stale marker bytes are gone, not the files themselves.)
        (Path(str(live) + "-wal")).write_bytes(b"STALE-WAL-MARKER" * 4)
        (Path(str(live) + "-shm")).write_bytes(b"STALE-SHM-MARKER" * 4)
        restore_database(db, out)
        for suffix, marker in (
            ("-wal", b"STALE-WAL-MARKER"),
            ("-shm", b"STALE-SHM-MARKER"),
        ):
            sidecar = Path(str(live) + suffix)
            if sidecar.exists():
                assert marker not in sidecar.read_bytes()
        assert db.total_jobs() == 1
        q.close()


class TestPruneAndList:
    def test_prune_keeps_newest_n(self, db, tmp_path):
        dest = tmp_path / "backups"
        for i in range(5):
            backup_database(dest_dir=dest, label=f"b{i}")
            # Ensure distinct mtimes for deterministic ordering.
            time.sleep(0.02)
        pruned = prune_backups(dest, keep=2)
        assert len(pruned) == 3
        remaining = sorted(dest.glob("rf-backup-*.db"))
        assert len(remaining) == 2
        assert all("b3" in p.name or "b4" in p.name for p in remaining)

    def test_prune_never_touches_safety_copies(self, db, tmp_path):
        dest = tmp_path / "backups"
        dest.mkdir()
        (dest / "pre-restore-20260101T000000Z.db").write_bytes(b"x")
        backup_database(dest_dir=dest)
        pruned = prune_backups(dest, keep=1)
        assert pruned == []
        assert (dest / "pre-restore-20260101T000000Z.db").exists()

    def test_prune_invalid_keep_rejected(self, db, tmp_path):
        with pytest.raises(ValueError):
            prune_backups(tmp_path, keep=0)

    def test_list_backups_newest_first(self, db, tmp_path):
        dest = tmp_path / "backups"
        first = backup_database(dest_dir=dest, label="first")
        time.sleep(0.02)
        second = backup_database(dest_dir=dest, label="second")
        listed = list_backups(dest)
        assert [b["path"] for b in listed] == [str(second), str(first)]
        assert all(b["valid"] for b in listed)
        assert all(b["size_bytes"] > 0 for b in listed)

    def test_verify_backup_reports_missing_tables(self, tmp_path):
        other = tmp_path / "thin.db"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE review_jobs (id TEXT)")
        conn.commit()
        conn.close()
        info = verify_backup(other)
        assert not info["ok"]
        assert "missing factory tables" in info["error"]
