"""
prismatic/gateway/event_bus.py — Async Event Bus for Prismatic Gateway

A lightweight pub/sub event bus that lives inside the gateway process.
WebSocket clients subscribe to event streams; external processes push
events via the IPC bridge (Unix sockets or HTTP loopbacks).

Events are structured as JSON-serializable dicts with required keys:
    type      — event category (e.g. 'lock', 'unlock', 'heartbeat', 'telemetry')
    source    — originating agent/process name
    timestamp — ISO-8601 UTC
    payload   — event-specific data dict

Events are broadcast to all connected WebSocket clients via FanoutRoute.

Integration points:
    - gateway/server.py — WebSocket endpoint + startup/shutdown lifecycle
    - gateway/ipc_bridge.py — Unix socket receiver → event_bus.publish()
    - prismatic/lock.py — emits lock/unlock/heartbeat events via IPC bridge
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.gateway.event_bus")

# SQLite bus path — durable event log shared with the dispatch consumer.
# Path resolution: $PRISMATIC_BUS_DB > $PRISMATIC_HOME-relative > ./prismatic_state
_BUS_DB_PATH = Path(os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite")
if not _BUS_DB_PATH.is_absolute():
    home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
    _BUS_DB_PATH = Path(home) / _BUS_DB_PATH
_BUS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Retention (Phase D.2a): keep last 10k events AND prune anything older than 14 days.
_BUS_RETENTION_DAYS = int(os.environ.get("PRISMATIC_BUS_RETENTION_DAYS", "14"))
_BUS_MAX_EVENTS = int(os.environ.get("PRISMATIC_BUS_MAX_EVENTS", "10000"))
_sqlite_lock = threading.Lock()

# ── Schema (D.3) ─────────────────────────────────────────────
# Single canonical schema for every event on the bus. Validated at publish()
# so handlers can trust shape. Phase D.3 — event schema validation.

SWARM_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "source", "timestamp", "payload"],
    "properties": {
        "type": {"type": "string", "minLength": 1, "maxLength": 128},
        "source": {"type": "string", "minLength": 1, "maxLength": 128},
        "timestamp": {"type": "string", "minLength": 10, "maxLength": 64},
        "payload": {"type": "object"},
    },
    "additionalProperties": False,
}


def _validate_event_shape(event_dict: dict[str, Any]) -> tuple[bool, str]:
    """Minimal schema check — no jsonschema dep required.
    Returns (ok, reason)."""
    for key in ("type", "source", "timestamp", "payload"):
        if key not in event_dict:
            return False, f"missing field: {key}"
    if not isinstance(event_dict["type"], str) or not event_dict["type"]:
        return False, "type must be non-empty string"
    if not isinstance(event_dict["source"], str) or not event_dict["source"]:
        return False, "source must be non-empty string"
    if not isinstance(event_dict["timestamp"], str):
        return False, "timestamp must be ISO string"
    if not isinstance(event_dict["payload"], dict):
        return False, "payload must be object"
    return True, "ok"

# ── Types ──────────────────────────────────────────────────

EventHandler = Callable[["SwarmEvent"], Awaitable[None]]


class SwarmEvent:
    """A single event in the swarm event bus."""

    __slots__ = ("payload", "source", "timestamp", "type")

    def __init__(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.type = event_type
        self.source = source
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.payload = payload or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmEvent:
        return cls(
            event_type=data.get("type", "unknown"),
            source=data.get("source", "unknown"),
            payload=data.get("payload", {}),
        )


# ── Event Bus ──────────────────────────────────────────────


class EventBus:
    """Async pub/sub event bus for the Prismatic Gateway.

    Maintains a set of async handler callbacks. When :meth:`publish`
    is called, all registered handlers receive the event concurrently.

    Thread-safe: all mutation is guarded by an asyncio.Lock.
    """

    def __init__(self, max_history: int = 200) -> None:
        self._handlers: set[EventHandler] = set()
        self._lock = asyncio.Lock()
        self._history: list[SwarmEvent] = []
        self._max_history = max_history
        self._total_published: int = 0
        self._total_delivered: int = 0
        self._total_failures: int = 0

    # ── Public API ──────────────────────────────────────

    async def publish(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> SwarmEvent:
        """Publish an event to all registered handlers.

        Returns the created SwarmEvent for optional post-processing.
        """
        event = SwarmEvent(event_type, source, payload)
        self._total_published += 1

        # D.3 schema validation — drop malformed events loudly, don't crash.
        ok, reason = _validate_event_shape(event.to_dict())
        if not ok:
            self._total_failures += 1
            logger.error("Rejecting malformed event from %s: %s", source, reason)
            return event

        # Persist to history ring buffer
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Persist to durable SQLite bus (drained by dispatch_consumer_v2.py).
        try:
            self._persist_to_sqlite(event)
        except Exception as exc:
            logger.warning("SQLite bus persist failed (event still delivered in-process): %s", exc)

        # Fan out to all handlers concurrently
        async with self._lock:
            handlers = list(self._handlers)

        if handlers:
            results = await asyncio.gather(
                *[self._deliver(h, event) for h in handlers],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    self._total_failures += 1
                    logger.error(
                        "Event handler failed: %s",
                        result,
                        exc_info=result,
                    )

        return event

    async def subscribe(self, handler: EventHandler) -> None:
        """Register an async handler to receive all published events."""
        async with self._lock:
            self._handlers.add(handler)
        logger.debug("Handler subscribed (total=%d)", len(self._handlers))

    async def unsubscribe(self, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        async with self._lock:
            self._handlers.discard(handler)
        logger.debug("Handler unsubscribed (total=%d)", len(self._handlers))

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent events as a list of dicts."""
        return [
            e.to_dict()
            for e in self._history[-limit:]
        ]

    @property
    def stats(self) -> dict[str, int]:
        return {
            "handlers": len(self._handlers),
            "history_size": len(self._history),
            "total_published": self._total_published,
            "total_delivered": self._total_delivered,
            "total_failures": self._total_failures,
        }

    # ── Internal ─────────────────────────────────────────

    async def _deliver(self, handler: EventHandler, event: SwarmEvent) -> None:
        """Deliver an event to a single handler, tracking success/failure."""
        try:
            await handler(event)
            self._total_delivered += 1
        except Exception:
            raise  # re-raised for gather() error tracking

    def _persist_to_sqlite(self, event: SwarmEvent) -> None:
        """Write event to the durable SQLite bus.

        Schema matches dispatch_consumer_v2.py's read path:
            dedup_key TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ts REAL NOT NULL

        After each write, prune anything older than 14 days AND keep the
        last 10k events (Phase D.2a overflow trim). WAL mode for concurrent
        gateway-writer + consumer-reader access.
        """
        with _sqlite_lock:
            conn = sqlite3.connect(_BUS_DB_PATH, timeout=5)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                # Schema matches dispatch_consumer_v3.py: rowid + processed column.
                # The consumer reads with `WHERE rowid > ? AND processed = 0` and
                # marks rows processed atomically.
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        dedup_key TEXT UNIQUE,
                        topic TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        ts REAL NOT NULL,
                        processed INTEGER DEFAULT 0
                    )
                    """
                )
                # Migrate older schemas that don't have `processed`.
                try:
                    conn.execute("ALTER TABLE events ADD COLUMN processed INTEGER DEFAULT 0")
                except Exception:
                    pass  # column already exists
                # dedup_key: stable per event_type+source+ts-second. The
                # consumer's fetch_new_events uses ts for ordering, so we
                # need a unique-per-event key. Use type+source+timestamp+hash.
                import hashlib
                payload_str = json.dumps(event.payload or {}, sort_keys=True, default=str)
                p_hash = hashlib.md5(payload_str.encode()).hexdigest()[:12]
                dedup_key = f"{event.type}:{event.source}:{event.timestamp}:{p_hash}"
                conn.execute(
                    "INSERT OR IGNORE INTO events (dedup_key, topic, payload_json, ts) VALUES (?, ?, ?, ?)",
                    (
                        dedup_key,
                        event.type,
                        json.dumps(event.to_dict(), default=str),
                        time.time(),
                    ),
                )
                conn.commit()
                # Phase D.2a: overflow trim — keep last N events, prune anything older than D days.
                conn.execute(
                    "DELETE FROM events WHERE ts < ?",
                    (time.time() - _BUS_RETENTION_DAYS * 86400,),
                )
                conn.execute(
                    "DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT -1 OFFSET ?)",
                    (_BUS_MAX_EVENTS,),
                )
                conn.commit()
            finally:
                conn.close()


# ── Singleton ────────────────────────────────────────────────

# Module-level singleton — one EventBus per gateway process.
# Initialized by gateway server startup, accessed by WebSocket

# ── Layer (b) startup migration ──────────────────────────────
# The live `events` table was created before `_persist_to_sqlite` gained the
# `rowid ... AUTOINCREMENT` clause (the v2-era plain-rowid schema). Because
# `CREATE TABLE IF NOT EXISTS` is a no-op on the pre-existing table, the
# AUTOINCREMENT declaration never took effect on the live bus: after the
# consumer's vacuum emptied `events`, the next insert restarted rowids at 1
# under a cursor that assumes they never decrease (the split-bus defect).
# This migration removes that root condition: it recreates a plain-rowid
# `events` table with the declared AUTOINCREMENT schema, preserving every
# existing rowid (so the cursor stays valid) and pinning `sqlite_sequence.seq`
# to the max rowid so the next insert is max+1. Idempotent and one-way by
# design — an AUTOINCREMENT table stays AUTOINCREMENT. Called once at gateway
# startup (server.py lifespan), never on the per-publish path.


def _events_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        is not None
    )


def _events_is_autoincrement(conn: sqlite3.Connection) -> bool:
    """True when the `events` DDL declares rowid ... AUTOINCREMENT.

    Detection is on the schema SQL, NOT on the `sqlite_sequence` row: a 0-row
    AUTOINCREMENT table has no `sqlite_sequence` row at all, so the sequence
    row is an unreliable idempotency signal.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    return bool(row) and "AUTOINCREMENT" in (row[0] or "").upper()


def migrate_events_to_autoincrement(db_path: str) -> str:
    """Migrate a legacy plain-rowid `events` table to AUTOINCREMENT.

    Safe to call on every gateway startup: it is a no-op when the table is
    already AUTOINCREMENT or does not exist, and a one-way conversion when the
    table is the old plain-rowid schema. Runs under `BEGIN IMMEDIATE` so a
    concurrent consumer/gateway write cannot interleave the recreate. All
    existing rowids are preserved and `sqlite_sequence.seq` is pinned to the
    max rowid, so the cursor and any future inserts continue from where the
    table left off instead of restarting at 1.

    Returns a short status for logging:
        'missing'        — no `events` table; nothing to do.
        'skipped'        — already AUTOINCREMENT (idempotent no-op).
        'migrated'       — converted a non-empty table (rowids preserved).
        'migrated-empty' — converted an empty plain-rowid table.
    """
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        if not _events_table_exists(conn):
            return "missing"
        if _events_is_autoincrement(conn):
            return "skipped"

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE events RENAME TO events_old")
            conn.execute(
                """
                CREATE TABLE events (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT UNIQUE,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    processed INTEGER DEFAULT 0
                )
                """
            )
            # Copy rows with rowids preserved (explicit rowid → cursor stays valid).
            conn.execute(
                """
                INSERT INTO events (rowid, dedup_key, topic, payload_json, ts, processed)
                SELECT rowid, dedup_key, topic, payload_json, ts, processed FROM events_old
                """
            )
            max_row = conn.execute("SELECT MAX(rowid) FROM events").fetchone()[0]
            # Belt + suspenders: pin sqlite_sequence.seq to the max rowid so the
            # next insert is max+1, never a recycled low id. (INSERT..SELECT with
            # an explicit rowid already advances seq, but set it explicitly to
            # match the spec.) sqlite_sequence has no PK on `name` here, so no
            # ON CONFLICT — use a conditional UPDATE/INSERT instead.
            if max_row is not None:
                if conn.execute(
                    "SELECT 1 FROM sqlite_sequence WHERE name='events'"
                ).fetchone():
                    conn.execute(
                        "UPDATE sqlite_sequence SET seq=? WHERE name='events'",
                        (max_row,),
                    )
                else:
                    conn.execute(
                        "INSERT INTO sqlite_sequence (name, seq) VALUES ('events', ?)",
                        (max_row,),
                    )
            conn.execute("DROP TABLE events_old")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return "migrated" if max_row is not None else "migrated-empty"
    finally:
        conn.close()


# ── Singleton (continued) ────────────────────────────────────
# endpoint and IPC bridge.
_bus: EventBus | None = None


def ensure_bus_autoincrement() -> str:
    """Run the Layer (b) bus migration against this process's own bus DB.

    Thin public wrapper so callers (the gateway lifespan in server.py) don't
    touch the private ``_BUS_DB_PATH``. Safe to call on every startup: it is a
    no-op when the bus is already AUTOINCREMENT or absent. Returns the same
    status string as :func:`migrate_events_to_autoincrement`.
    """
    status = migrate_events_to_autoincrement(str(_BUS_DB_PATH))
    logger.info("Bus AUTOINCREMENT migration at startup: %s", status)
    return status


def get_event_bus() -> EventBus:
    """Return the module-level EventBus singleton, creating it if needed."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def set_event_bus(bus: EventBus) -> None:
    """Replace the module-level EventBus (for testing)."""
    global _bus
    _bus = bus
