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
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger("prismatic.gateway.event_bus")

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

        # Persist to history ring buffer
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

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
