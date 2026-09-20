"""Prismatic Engine Hypervisor: Append-only SQLite WAL Audit Ledger with Merkle Proofs.

Durable WAL transaction ledger serializing task admissions, tool calls, and test executions.

Guarantees delivered by this module:
- Append-only: the API exposes no update/delete path, and SQLite triggers reject
  any UPDATE/DELETE against the table, so "immutable" is enforced at the storage
  layer, not just by convention.
- Tamper-evident: every entry hash-chains to its predecessor (SHA-256), and
  ``verify_chain_integrity`` recomputes the full chain. Editing an entry, or
  removing any entry except the tail, breaks every later link and is detected.
  Deleting the *final* entry (tail truncation) or replacing the whole DB file
  is NOT detectable from inside the ledger: detecting that requires comparing
  against a Merkle root or entry count anchored externally (see below).
- Crash-safe: one ``BEGIN IMMEDIATE`` transaction covers the read-modify-write
  of the chain tail (atomic across processes sharing the file), and
  ``synchronous=FULL`` means a committed entry survives an OS crash.
- Merkle roots over consecutive execution spans for compact attestation.

What this does NOT promise: it cannot stop someone with write access to the DB
file from replacing or deleting the file wholesale. Tampering *within* the
ledger is what the hash chain detects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.hypervisor.ledger")

_WAL_CHECKPOINT_MODES = frozenset({"PASSIVE", "FULL", "RESTART", "TRUNCATE"})

_GENESIS_PREV_HASH = "0" * 64


def _windows_default_ledger_db() -> Path:
    # tempfile.gettempdir() honors %TEMP%/%TMP% and falls back sanely; never
    # hardcode a drive-letter temp path (a stranger's Windows box may not have
    # the drive or directory you assumed).
    return Path(tempfile.gettempdir()) / "prismatic_db" / "hypervisor_ledger.db"


def _get_default_ledger_db() -> Path:
    if os.environ.get("PRISMATIC_STATE_DIR"):
        return Path(os.environ["PRISMATIC_STATE_DIR"]) / "hypervisor_ledger.db"
    if os.name != "nt":
        try:
            home = Path(os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"))
        except Exception:
            # Last resort: a stranger's machine where even the home directory
            # cannot be determined. Never fall back to another user's path.
            home = Path.cwd()
        return home / ".prismatic" / "db" / "hypervisor_ledger.db"
    return _windows_default_ledger_db()


DEFAULT_LEDGER_DB = _get_default_ledger_db()


@dataclass
class LedgerEntry:
    id: int
    event_id: str
    task_id: str
    producer: str
    action: str
    payload: dict[str, Any]
    timestamp: float
    prev_hash: str
    entry_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "producer": self.producer,
            "action": self.action,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


def _compute_entry_hash(
    prev_hash: str,
    timestamp: float,
    task_id: str,
    producer: str,
    action: str,
    payload_json: str,
) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(str(timestamp).encode("utf-8"))
    h.update(task_id.encode("utf-8"))
    h.update(producer.encode("utf-8"))
    h.update(action.encode("utf-8"))
    h.update(payload_json.encode("utf-8"))
    return h.hexdigest()


def _compute_merkle_root(hashes: list[str]) -> str:
    """Compute standard binary Merkle root over a list of SHA-256 hashes."""
    if not hashes:
        return hashlib.sha256(b"empty_merkle_tree").hexdigest()

    current = list(hashes)
    while len(current) > 1:
        if len(current) % 2 != 0:
            current.append(current[-1])
        next_level = []
        for i in range(0, len(current), 2):
            combined = current[i] + current[i + 1]
            next_level.append(hashlib.sha256(combined.encode("utf-8")).hexdigest())
        current = next_level
    return current[0]


class HypervisorLedger:
    """Thread-safe, append-only, hash-chained audit ledger backed by SQLite WAL."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        # Resolve the default lazily so PRISMATIC_STATE_DIR / PRISMATIC_HOME set
        # after import are still honored.
        self.db_path = Path(db_path) if db_path else _get_default_ledger_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._events_since_checkpoint = 0
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            journal_mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            if not journal_mode or str(journal_mode[0]).upper() != "WAL":
                logger.warning(
                    "Hypervisor ledger at %s could not enable WAL mode (got %s); "
                    "crash safety is reduced.",
                    self.db_path,
                    journal_mode[0] if journal_mode else "unknown",
                )
            # FULL: a committed entry survives an OS crash. This is an audit
            # ledger, not a hot path — durability wins over write latency.
            conn.execute("PRAGMA synchronous=FULL;")
        except sqlite3.Error:
            conn.close()
            raise
        conn.row_factory = sqlite3.Row
        return conn

    def checkpoint_wal(self, mode: str = "PASSIVE") -> dict[str, Any]:
        """Explicitly checkpoint SQLite WAL log to prevent unbounded journal growth."""
        normalized = mode.strip().upper()
        if normalized not in _WAL_CHECKPOINT_MODES:
            raise ValueError(
                f"Invalid WAL checkpoint mode {mode!r}: expected one of {sorted(_WAL_CHECKPOINT_MODES)}"
            )
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(f"PRAGMA wal_checkpoint({normalized});")
                row = cur.fetchone()
                busy, log_frames, checkpointed = row[0], row[1], row[2]
                self._events_since_checkpoint = 0
                return {
                    "busy": busy,
                    "log_frames": log_frames,
                    "checkpointed": checkpointed,
                }
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ledger_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT UNIQUE NOT NULL,
                        task_id TEXT NOT NULL,
                        producer TEXT NOT NULL,
                        action TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        prev_hash TEXT NOT NULL,
                        entry_hash TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_task_id ON ledger_events(task_id);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_producer ON ledger_events(producer);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_events(timestamp);"
                )
                # Storage-layer append-only enforcement: the "immutable" claim
                # is backed by triggers, not just by the absence of an API.
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS ledger_events_no_update
                    BEFORE UPDATE ON ledger_events
                    BEGIN
                        SELECT RAISE(ABORT, 'hypervisor ledger is append-only: UPDATE rejected');
                    END;
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS ledger_events_no_delete
                    BEFORE DELETE ON ledger_events
                    BEGIN
                        SELECT RAISE(ABORT, 'hypervisor ledger is append-only: DELETE rejected');
                    END;
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record_event(
        self,
        task_id: str,
        producer: str,
        action: str,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> LedgerEntry:
        """Record an atomic, cryptographically linked event to the ledger."""
        # Fail fast on bad input: blank identifiers corrupt the audit trail's
        # usefulness, and a non-mapping payload would serialize ambiguously.
        for _name, _value in (
            ("task_id", task_id),
            ("producer", producer),
            ("action", action),
        ):
            if not isinstance(_value, str) or not _value.strip():
                raise ValueError(f"{_name} must be a non-empty string, got {_value!r}")
        if event_id is not None and (
            not isinstance(event_id, str) or not event_id.strip()
        ):
            raise ValueError(f"event_id must be a non-empty string, got {event_id!r}")
        if payload is not None and not isinstance(payload, dict):
            raise TypeError(f"payload must be a dict, got {type(payload).__name__}")
        payload = payload or {}
        try:
            payload_json = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Ledger payload for action {action!r} is not JSON-serializable: {e}"
            ) from e

        with self._lock:
            conn = self._get_connection()
            try:
                # BEGIN IMMEDIATE takes a RESERVED lock up front, so the
                # read-modify-write of the chain tail is atomic even across
                # processes sharing this DB file. A crash between INSERT and
                # COMMIT rolls back: no partial or torn entries.
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    entry = self._insert_event(
                        conn, task_id, producer, action, payload, payload_json, event_id
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except sqlite3.Error as rb_err:
                        logger.error("Ledger rollback failed: %s", rb_err)
                    raise

                # Auto-checkpoint outside the transaction: wal_checkpoint cannot
                # run while a write transaction is active.
                self._events_since_checkpoint += 1
                if self._events_since_checkpoint >= 100:
                    try:
                        conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                        self._events_since_checkpoint = 0
                    except sqlite3.Error as cp_err:
                        logger.debug("WAL auto-checkpoint exception: %s", cp_err)
            finally:
                conn.close()

        return entry

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        producer: str,
        action: str,
        payload: dict[str, Any],
        payload_json: str,
        event_id: str | None,
    ) -> LedgerEntry:
        last_err: Exception | None = None
        for _ in range(5):
            ts = time.time()
            eid = event_id or f"evt_{int(ts * 1000)}_{os.urandom(4).hex()}"

            cur = conn.execute(
                "SELECT entry_hash FROM ledger_events ORDER BY id DESC LIMIT 1;"
            )
            row = cur.fetchone()
            prev_hash = row["entry_hash"] if row else _GENESIS_PREV_HASH

            entry_hash = _compute_entry_hash(
                prev_hash, ts, task_id, producer, action, payload_json
            )

            try:
                cur = conn.execute(
                    """
                    INSERT INTO ledger_events
                        (event_id, task_id, producer, action, payload_json, timestamp, prev_hash, entry_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        eid,
                        task_id,
                        producer,
                        action,
                        payload_json,
                        ts,
                        prev_hash,
                        entry_hash,
                    ),
                )
            except sqlite3.IntegrityError as e:
                if event_id is not None:
                    # Caller-supplied duplicate: fail closed with a clear error.
                    raise ValueError(f"Duplicate ledger event_id {event_id!r}") from e
                # Auto-generated id collision: regenerate and retry.
                last_err = e
                continue

            row_id = cur.lastrowid

            return LedgerEntry(
                id=row_id,
                event_id=eid,
                task_id=task_id,
                producer=producer,
                action=action,
                payload=payload,
                timestamp=ts,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )

        raise RuntimeError(
            f"Failed to record ledger event after event_id retries: {last_err}"
        )

    def list_events(
        self,
        limit: int = 50,
        task_id: str | None = None,
        producer: str | None = None,
    ) -> list[LedgerEntry]:
        """List recorded events with optional filtering."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be a positive int, got {limit!r}")
        with self._lock:
            query = "SELECT * FROM ledger_events WHERE 1=1"
            params: list[Any] = []
            if task_id:
                query += " AND task_id = ?"
                params.append(task_id)
            if producer:
                query += " AND producer = ?"
                params.append(producer)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            conn = self._get_connection()
            try:
                cur = conn.execute(query, params)
                rows = cur.fetchall()
            finally:
                conn.close()

            entries = []
            for r in rows:
                entries.append(
                    LedgerEntry(
                        id=r["id"],
                        event_id=r["event_id"],
                        task_id=r["task_id"],
                        producer=r["producer"],
                        action=r["action"],
                        payload=json.loads(r["payload_json"]),
                        timestamp=r["timestamp"],
                        prev_hash=r["prev_hash"],
                        entry_hash=r["entry_hash"],
                    )
                )
            return entries

    def get_merkle_root(
        self, task_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Compute the current Merkle root over recent entries."""
        events = self.list_events(limit=limit, task_id=task_id)
        hashes = [e.entry_hash for e in reversed(events)]
        root = _compute_merkle_root(hashes)
        return {
            "merkle_root": root,
            "entry_count": len(hashes),
            "task_id": task_id,
            "timestamp": time.time(),
        }

    def verify_chain_integrity(self) -> dict[str, Any]:
        """Verify that every entry in the database is cryptographically unbroken."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("SELECT * FROM ledger_events ORDER BY id ASC;")
                rows = cur.fetchall()
            finally:
                conn.close()

            expected_prev = _GENESIS_PREV_HASH
            for r in rows:
                if r["prev_hash"] != expected_prev:
                    return {
                        "valid": False,
                        "broken_at_id": r["id"],
                        "error": (
                            f"Hash chain break: expected prev_hash {expected_prev}, "
                            f"found {r['prev_hash']}"
                        ),
                    }
                computed = _compute_entry_hash(
                    r["prev_hash"],
                    r["timestamp"],
                    r["task_id"],
                    r["producer"],
                    r["action"],
                    r["payload_json"],
                )
                if computed != r["entry_hash"]:
                    return {
                        "valid": False,
                        "broken_at_id": r["id"],
                        "error": f"Entry hash mismatch: expected {computed}, found {r['entry_hash']}",
                    }
                expected_prev = r["entry_hash"]

            return {
                "valid": True,
                "verified_entries": len(rows),
                "latest_root": _compute_merkle_root([r["entry_hash"] for r in rows]),
            }


_GLOBAL_LEDGER: HypervisorLedger | None = None
_GLOBAL_LEDGER_LOCK = threading.Lock()


def get_hypervisor_ledger() -> HypervisorLedger:
    global _GLOBAL_LEDGER
    with _GLOBAL_LEDGER_LOCK:
        if _GLOBAL_LEDGER is None:
            _GLOBAL_LEDGER = HypervisorLedger()
        return _GLOBAL_LEDGER
