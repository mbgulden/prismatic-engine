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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

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

    __slots__ = ("type", "source", "timestamp", "payload")

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
    def from_dict(cls, data: dict[str, Any]) -> "SwarmEvent":
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

    def _persist_to_sqlite(self, event: "SwarmEvent") -> None:
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
# endpoint and IPC bridge.
_bus: EventBus | None = None


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
