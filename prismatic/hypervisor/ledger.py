"""Prismatic Engine Hypervisor: Immutable SQLite WAL Audit Ledger with Merkle Proofs.

Enforces Prismatic Engine Phase 3:
- Durable WAL transaction ledger serializing task admissions, tool calls, and test executions.
- Cryptographically chained rolling SHA-256 digests.
- Merkle root computation over consecutive execution spans.
- Tamper-evident chain verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.hypervisor.ledger")

DEFAULT_LEDGER_DB = Path("/tmp/prismatic_hypervisor_ledger.db") if os.name != "nt" else Path(os.environ.get("TEMP", "C:/temp")) / "prismatic_hypervisor_ledger.db"


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


def _compute_entry_hash(prev_hash: str, timestamp: float, task_id: str, producer: str, action: str, payload_json: str) -> str:
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
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_LEDGER_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
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
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_task_id ON ledger_events(task_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_producer ON ledger_events(producer);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_events(timestamp);")
                conn.commit()

    def record_event(
        self,
        task_id: str,
        producer: str,
        action: str,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> LedgerEntry:
        """Record an atomic, cryptographically linked event to the ledger."""
        with self._lock:
            payload = payload or {}
            payload_json = json.dumps(payload, sort_keys=True)
            ts = time.time()
            eid = event_id or f"evt_{int(ts * 1000)}_{os.urandom(4).hex()}"

            with self._get_connection() as conn:
                cur = conn.execute("SELECT entry_hash FROM ledger_events ORDER BY id DESC LIMIT 1;")
                row = cur.fetchone()
                prev_hash = row["entry_hash"] if row else ("0" * 64)

                entry_hash = _compute_entry_hash(prev_hash, ts, task_id, producer, action, payload_json)

                cur = conn.execute("""
                    INSERT INTO ledger_events (event_id, task_id, producer, action, payload_json, timestamp, prev_hash, entry_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (eid, task_id, producer, action, payload_json, ts, prev_hash, entry_hash))
                row_id = cur.lastrowid
                conn.commit()

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

    def list_events(
        self,
        limit: int = 50,
        task_id: str | None = None,
        producer: str | None = None,
    ) -> list[LedgerEntry]:
        """List recorded events with optional filtering."""
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

            with self._get_connection() as conn:
                cur = conn.execute(query, params)
                rows = cur.fetchall()

            entries = []
            for r in rows:
                entries.append(LedgerEntry(
                    id=r["id"],
                    event_id=r["event_id"],
                    task_id=r["task_id"],
                    producer=r["producer"],
                    action=r["action"],
                    payload=json.loads(r["payload_json"]),
                    timestamp=r["timestamp"],
                    prev_hash=r["prev_hash"],
                    entry_hash=r["entry_hash"],
                ))
            return entries

    def get_merkle_root(self, task_id: str | None = None, limit: int = 100) -> dict[str, Any]:
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
            with self._get_connection() as conn:
                cur = conn.execute("SELECT * FROM ledger_events ORDER BY id ASC;")
                rows = cur.fetchall()

            expected_prev = "0" * 64
            for r in rows:
                if r["prev_hash"] != expected_prev:
                    return {
                        "valid": False,
                        "broken_at_id": r["id"],
                        "error": f"Hash chain break: expected prev_hash {expected_prev}, found {r['prev_hash']}",
                    }
                computed = _compute_entry_hash(
                    r["prev_hash"], r["timestamp"], r["task_id"], r["producer"], r["action"], r["payload_json"]
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
