"""Phase 5 -- Durability: backup/restore for the shared Review Factory DB.

The factory tables live inside ``agy_completed_work``'s SQLite database
(see :mod:`prismatic.review_factory.db`) -- NOT a separate file. This module
adds operator-triggered backup and restore for that shared file:

- :func:`backup_database` -- consistent online snapshot via ``VACUUM INTO``
  (safe against the live WAL-mode DB), written timestamped into
  ``<db_dir>/backups/``.
- :func:`restore_database` -- fail-closed restore from a backup file.
  Validates the backup is a real SQLite DB containing the factory tables,
  keeps a pre-restore safety copy of the live DB, clears stale WAL files,
  then atomically replaces the live file.
- :func:`prune_backups` -- retention for the backups directory.

Restore is an explicit operator action: stop the daemon (or at least quiesce
the queue) before restoring; in-flight transactions will fail. Nothing here
runs on a schedule -- backups are taken when the operator (or a future
explicitly-configured job) asks for them. See ``scripts/review_factory_db.py``
for the CLI.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from prismatic.review_factory.db import ReviewFactoryDB, default_db_path

# Tables the restore validator requires. Must stay in sync with db.py's
# _CREATE_TABLES (factory-owned tables only; the shared agy tables are not
# ours to require).
REQUIRED_TABLES = frozenset(
    {
        "rf_schema_version",
        "review_jobs",
        "verification_receipts",
        "review_decisions",
        "merge_authorizations",
        "repair_packets",
        "review_factory_audit_log",
    }
)

BACKUP_PREFIX = "rf-backup-"


class DurabilityError(Exception):
    """Raised when a backup/restore operation cannot proceed safely."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_backup_dir(db_path: Optional[Path] = None) -> Path:
    """Backups live next to the DB: ``<db_dir>/backups/``."""
    db_path = Path(db_path) if db_path else default_db_path()
    return db_path.parent / "backups"


def backup_database(
    db_path: Optional[Path] = None,
    dest_dir: Optional[Path] = None,
    *,
    label: str = "",
) -> Path:
    """Take a consistent snapshot of the shared Review Factory DB.

    Uses ``VACUUM INTO`` so the backup is a clean, self-contained SQLite
    file even while the daemon holds the live DB open (WAL mode).

    Returns the backup file path. Raises :class:`DurabilityError` /
    :class:`FileNotFoundError` on failure.
    """
    src = Path(db_path) if db_path else default_db_path()
    if not src.exists():
        raise FileNotFoundError(f"review factory DB not found: {src}")

    dest = Path(dest_dir) if dest_dir else default_backup_dir(src)
    dest.mkdir(parents=True, exist_ok=True)

    suffix = f"-{label}" if label else ""
    out = dest / f"{BACKUP_PREFIX}{_utc_stamp()}{suffix}.db"

    # VACUUM INTO on a dedicated connection: consistent snapshot of the
    # live DB without disturbing the daemon's own connections.
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM INTO ?", (str(out),))
    except sqlite3.Error as exc:
        raise DurabilityError(f"backup failed for {src}: {exc}") from exc
    finally:
        conn.close()

    info = verify_backup(out)
    if not info["ok"]:
        out.unlink(missing_ok=True)
        raise DurabilityError(f"backup verification failed for {out}: {info['error']}")
    return out


def verify_backup(backup_path: Path) -> dict:
    """Check a backup file: valid SQLite, integrity OK, factory tables present.

    Returns ``{"ok": bool, "error": str|None, "tables": [...]}``. Never raises.
    """
    path = Path(backup_path)
    if not path.exists():
        return {"ok": False, "error": f"not found: {path}", "tables": []}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"not a SQLite database: {exc}", "tables": []}
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            return {
                "ok": False,
                "error": f"missing factory tables: {', '.join(missing)}",
                "tables": sorted(tables),
            }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            return {
                "ok": False,
                "error": f"integrity_check failed: {integrity}",
                "tables": sorted(tables),
            }
        return {"ok": True, "error": None, "tables": sorted(tables)}
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"not a SQLite database: {exc}", "tables": []}
    finally:
        conn.close()


def restore_database(
    db: ReviewFactoryDB,
    backup_path: Path,
    *,
    safety_dir: Optional[Path] = None,
) -> dict:
    """Restore the shared DB from a backup file. Fail-closed.

    Steps:
    1. Validate the backup (valid SQLite, integrity OK, factory tables).
    2. Snapshot the live DB to a pre-restore safety copy (``VACUUM INTO``).
    3. Close this handle's connection, clear stale ``-wal``/``-shm`` files,
       atomically replace the live file with the backup.
    4. Leave ``db`` usable: its next ``conn`` access re-opens the restored file.

    The caller MUST have stopped the daemon (or quiesced the queue) first;
    any other open connections to the live DB will see failures, not silent
    corruption. Returns a report dict with the safety-copy path.
    """
    src = Path(backup_path)
    info = verify_backup(src)
    if not info["ok"]:
        raise DurabilityError(
            f"refusing to restore from invalid backup: {info['error']}"
        )

    live = Path(db.db_path)
    if not live.exists():
        raise FileNotFoundError(f"live review factory DB not found: {live}")

    stamp = _utc_stamp()
    sdir = Path(safety_dir) if safety_dir else default_backup_dir(live)
    sdir.mkdir(parents=True, exist_ok=True)
    safety_copy = sdir / f"pre-restore-{stamp}.db"

    # Consistent snapshot of the live DB before we touch it.
    snap = sqlite3.connect(str(live))
    try:
        snap.execute("VACUUM INTO ?", (str(safety_copy),))
    except sqlite3.Error as exc:
        raise DurabilityError(f"pre-restore safety copy failed: {exc}") from exc
    finally:
        snap.close()

    # No open handle of ours may point at the old file during replacement.
    # Copy (not move) so the source backup survives the restore; the final
    # os.replace is atomic.
    db.close()
    staging = live.parent / f".restoring-{stamp}.db"
    try:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(live) + suffix)
            sidecar.unlink(missing_ok=True)
        shutil.copy2(src, staging)
        os.replace(staging, live)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise DurabilityError(f"atomic replace failed: {exc}") from exc

    # Re-open against the restored file so the handle keeps working.
    db.ensure_tables()
    return {
        "restored_from": str(src),
        "live_db": str(live),
        "safety_copy": str(safety_copy),
        "tables": info["tables"],
    }


def prune_backups(
    dest_dir: Optional[Path] = None,
    *,
    keep: int = 7,
    db_path: Optional[Path] = None,
) -> list[Path]:
    """Delete oldest backups, keeping the ``keep`` newest. Returns pruned paths.

    Only files matching the ``rf-backup-*.db`` naming are touched; the
    ``pre-restore-*.db`` safety copies are never pruned here.
    """
    if keep < 1:
        raise ValueError("keep must be >= 1")
    d = Path(dest_dir) if dest_dir else default_backup_dir(db_path)
    if not d.exists():
        return []
    backups = sorted(
        (p for p in d.glob(f"{BACKUP_PREFIX}*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    pruned = []
    for old in backups[:-keep] if len(backups) > keep else []:
        old.unlink()
        pruned.append(old)
    return pruned


def list_backups(
    dest_dir: Optional[Path] = None,
    *,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Newest-first listing of backups with size and verification status."""
    d = Path(dest_dir) if dest_dir else default_backup_dir(db_path)
    if not d.exists():
        return []
    out = []
    for p in sorted(
        d.glob(f"{BACKUP_PREFIX}*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        info = verify_backup(p)
        out.append(
            {
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "mtime": datetime.fromtimestamp(
                    p.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "valid": info["ok"],
            }
        )
    return out
