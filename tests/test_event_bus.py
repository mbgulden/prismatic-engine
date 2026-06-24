"""Tests for prismatic.gateway.event_bus — async pub/sub event bus.

GRO-2402 follow-up: event_bus.py is the IPC backbone used by:
- gateway/server.py (WebSocket endpoint)
- gateway/ipc_bridge.py (Unix socket receiver → publish)
- prismatic/lock.py (emits lock/unlock/heartbeat events)

Until now, this file had zero direct tests. A bug here would silently
break IPC, lock event emission, and WebSocket fan-out. These tests
cover:
- SwarmEvent: shape, serialization, round-trip
- EventBus: subscribe/unsubscribe, publish fan-out, handler isolation
- History: ring buffer behavior, get_history limit
- Stats: counters update correctly
- Failure isolation: one failing handler doesn't break others
- Singleton: get_event_bus/set_event_bus pattern
"""
from __future__ import annotations

import pytest

import asyncio
import json
import os
import sys
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.gateway.event_bus import (  # noqa: E402
    SwarmEvent,
    EventBus,
    get_event_bus,
    set_event_bus,
)


# ── SwarmEvent ──────────────────────────────────────────────────────
def test_swarm_event_required_fields():
    e = SwarmEvent(event_type="lock", source="fred")
    assert e.type == "lock"
    assert e.source == "fred"
    assert e.timestamp  # ISO-8601 string, non-empty
    assert e.payload == {}


def test_swarm_event_payload_defaults_to_empty_dict():
    e = SwarmEvent(event_type="x", source="y")
    assert e.payload == {}
    assert isinstance(e.payload, dict)


def test_swarm_event_with_payload():
    e = SwarmEvent(event_type="lock", source="fred", payload={"file": "x.py"})
    assert e.payload == {"file": "x.py"}


def test_swarm_event_to_dict_shape():
    e = SwarmEvent("lock", "fred", {"file": "x.py"})
    d = e.to_dict()
    assert d["type"] == "lock"
    assert d["source"] == "fred"
    assert "timestamp" in d
    assert d["payload"] == {"file": "x.py"}


def test_swarm_event_to_json_is_valid_json():
    e = SwarmEvent("lock", "fred", {"file": "x.py"})
    j = e.to_json()
    parsed = json.loads(j)
    assert parsed["type"] == "lock"
    assert parsed["payload"]["file"] == "x.py"


def test_swarm_event_from_dict_round_trip():
    original = SwarmEvent("unlock", "kai", {"file": "y.py", "agent": "kai"})
    d = original.to_dict()
    restored = SwarmEvent.from_dict(d)
    assert restored.type == "unlock"
    assert restored.source == "kai"
    assert restored.payload == {"file": "y.py", "agent": "kai"}


def test_swarm_event_from_dict_missing_fields_uses_defaults():
    """Missing keys → defaults (forward-compat with old/new event schemas)."""
    e = SwarmEvent.from_dict({})
    assert e.type == "unknown"
    assert e.source == "unknown"
    assert e.payload == {}


def test_swarm_event_timestamp_is_iso_8601_utc():
    """Timestamp should parse as ISO-8601 (no microsecond surprises)."""
    from datetime import datetime
    e = SwarmEvent("x", "y")
    # Should parse without error
    ts = datetime.fromisoformat(e.timestamp)
    assert ts.tzinfo is not None  # has timezone


def test_swarm_event_uses_slots():
    """__slots__ declared → no __dict__ (memory-efficient)."""
    e = SwarmEvent("x", "y")
    assert not hasattr(e, "__dict__"), "SwarmEvent should use __slots__"


# ── EventBus: subscribe/unsubscribe ─────────────────────────────────
@pytest.mark.asyncio
async def test_subscribe_adds_handler():
    bus = EventBus()
    async def handler(event):
        pass
    await bus.subscribe(handler)
    assert handler in bus._handlers
    assert bus.stats["handlers"] == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_handler():
    bus = EventBus()
    async def handler(event):
        pass
    await bus.subscribe(handler)
    await bus.unsubscribe(handler)
    assert handler not in bus._handlers
    assert bus.stats["handlers"] == 0


@pytest.mark.asyncio
async def test_unsubscribe_nonexistent_is_noop():
    bus = EventBus()
    async def handler(event):
        pass
    # Should not raise
    await bus.unsubscribe(handler)


@pytest.mark.asyncio
async def test_subscribe_same_handler_twice_is_idempotent():
    """Set semantics: subscribing the same handler twice → still 1."""
    bus = EventBus()
    async def handler(event):
        pass
    await bus.subscribe(handler)
    await bus.subscribe(handler)
    assert bus.stats["handlers"] == 1


@pytest.mark.asyncio
async def test_multiple_handlers_can_subscribe():
    bus = EventBus()
    async def h1(event):
        pass
    async def h2(event):
        pass
    await bus.subscribe(h1)
    await bus.subscribe(h2)
    assert bus.stats["handlers"] == 2


# ── EventBus: publish fan-out ───────────────────────────────────────
@pytest.mark.asyncio
async def test_publish_calls_all_handlers():
    bus = EventBus()
    received = []
    async def h1(event):
        received.append(("h1", event.type))
    async def h2(event):
        received.append(("h2", event.type))
    await bus.subscribe(h1)
    await bus.subscribe(h2)
    await bus.publish("lock", "fred")
    assert len(received) == 2
    assert ("h1", "lock") in received
    assert ("h2", "lock") in received


@pytest.mark.asyncio
async def test_publish_with_no_handlers_does_not_crash():
    bus = EventBus()
    # Should not raise
    event = await bus.publish("lonely", "fred")
    assert event.type == "lonely"


@pytest.mark.asyncio
async def test_publish_returns_the_event():
    bus = EventBus()
    event = await bus.publish("x", "y", {"key": "val"})
    assert isinstance(event, SwarmEvent)
    assert event.type == "x"
    assert event.payload == {"key": "val"}


@pytest.mark.asyncio
async def test_publish_increments_total_published():
    bus = EventBus()
    await bus.publish("a", "x")
    await bus.publish("b", "y")
    await bus.publish("c", "z")
    assert bus.stats["total_published"] == 3


@pytest.mark.asyncio
async def test_publish_increments_total_delivered():
    bus = EventBus()
    async def h(event):
        pass
    await bus.subscribe(h)
    await bus.publish("a", "x")
    await bus.publish("b", "y")
    assert bus.stats["total_delivered"] == 2


@pytest.mark.asyncio
async def test_publish_with_payload_passes_to_handler():
    bus = EventBus()
    received = []
    async def h(event):
        received.append(event.payload)
    await bus.subscribe(h)
    await bus.publish("lock", "fred", {"file": "x.py"})
    assert received == [{"file": "x.py"}]


# ── EventBus: failure isolation ─────────────────────────────────────
@pytest.mark.asyncio
async def test_failing_handler_does_not_break_others():
    """One handler raises → others still receive the event."""
    bus = EventBus()
    received = []
    async def good_handler(event):
        received.append(event.type)
    async def bad_handler(event):
        raise RuntimeError("handler error")
    await bus.subscribe(good_handler)
    await bus.subscribe(bad_handler)
    await bus.publish("lock", "fred")
    # Good handler still got the event
    assert "lock" in received


@pytest.mark.asyncio
async def test_failing_handler_increments_failure_counter():
    bus = EventBus()
    async def bad_handler(event):
        raise RuntimeError("handler error")
    await bus.subscribe(bad_handler)
    await bus.publish("a", "x")
    assert bus.stats["total_failures"] == 1


@pytest.mark.asyncio
async def test_multiple_failures_tracked():
    bus = EventBus()
    async def bad_handler(event):
        raise RuntimeError("err")
    await bus.subscribe(bad_handler)
    for _ in range(5):
        await bus.publish("x", "y")
    assert bus.stats["total_failures"] == 5


# ── EventBus: history ring buffer ───────────────────────────────────
@pytest.mark.asyncio
async def test_history_records_published_events():
    bus = EventBus()
    await bus.publish("a", "x")
    await bus.publish("b", "y")
    history = bus.get_history()
    assert len(history) == 2
    assert history[0]["type"] == "a"
    assert history[1]["type"] == "b"


@pytest.mark.asyncio
async def test_history_respects_max_history():
    """Ring buffer: oldest events drop when capacity exceeded."""
    bus = EventBus(max_history=3)
    for i in range(5):
        await bus.publish(f"e{i}", "x")
    history = bus.get_history()
    assert len(history) == 3
    # Newest 3 retained
    assert [h["type"] for h in history] == ["e2", "e3", "e4"]


@pytest.mark.asyncio
async def test_get_history_default_limit_is_50():
    bus = EventBus(max_history=200)
    for i in range(75):
        await bus.publish(f"e{i}", "x")
    history = bus.get_history()
    # Default limit is 50
    assert len(history) == 50
    # Last 50
    assert history[-1]["type"] == "e74"


@pytest.mark.asyncio
async def test_get_history_explicit_limit():
    bus = EventBus()
    for i in range(10):
        await bus.publish(f"e{i}", "x")
    history = bus.get_history(limit=3)
    assert len(history) == 3
    assert [h["type"] for h in history] == ["e7", "e8", "e9"]


@pytest.mark.asyncio
async def test_history_returns_dicts_not_events():
    """get_history returns JSON-serializable dicts (not SwarmEvent objects)."""
    bus = EventBus()
    await bus.publish("a", "x")
    history = bus.get_history()
    assert isinstance(history[0], dict)
    # Should be JSON-serializable
    json.dumps(history)


# ── EventBus: stats ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stats_initial_values():
    bus = EventBus()
    s = bus.stats
    assert s["handlers"] == 0
    assert s["history_size"] == 0
    assert s["total_published"] == 0
    assert s["total_delivered"] == 0
    assert s["total_failures"] == 0


@pytest.mark.asyncio
async def test_stats_reflects_activity():
    bus = EventBus()
    async def h(event):
        pass
    await bus.subscribe(h)
    await bus.publish("a", "x")
    s = bus.stats
    assert s["handlers"] == 1
    assert s["history_size"] == 1
    assert s["total_published"] == 1
    assert s["total_delivered"] == 1


@pytest.mark.asyncio
async def test_stats_failures_tracked_separately():
    bus = EventBus()
    async def bad(event):
        raise RuntimeError("nope")
    await bus.subscribe(bad)
    await bus.publish("a", "x")
    s = bus.stats
    assert s["total_published"] == 1
    assert s["total_delivered"] == 0  # never succeeded
    assert s["total_failures"] == 1


# ── Singleton pattern ───────────────────────────────────────────────
def test_get_event_bus_returns_singleton():
    set_event_bus(None)  # reset
    bus1 = get_event_bus()
    bus2 = get_event_bus()
    assert bus1 is bus2


def test_set_event_bus_replaces_singleton():
    bus1 = get_event_bus()
    new_bus = EventBus()
    set_event_bus(new_bus)
    assert get_event_bus() is new_bus
    assert get_event_bus() is not bus1
    # Restore for other tests
    set_event_bus(EventBus())


def test_set_event_bus_to_none_creates_new():
    """get_event_bus after set_event_bus(None) creates a new bus."""
    set_event_bus(None)
    bus = get_event_bus()
    assert isinstance(bus, EventBus)
    # Cleanup
    set_event_bus(EventBus())


# ── Concurrency ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_concurrent_publish_does_not_lose_events():
    """10 concurrent publishers × 10 events each = 100 events, all recorded."""
    bus = EventBus(max_history=1000)
    async def publish_many(start):
        for i in range(10):
            await bus.publish(f"e{start + i}", f"src{start}")
    await asyncio.gather(*[publish_many(i * 10) for i in range(10)])
    assert bus.stats["total_published"] == 100
    history = bus.get_history(limit=1000)
    assert len(history) == 100


@pytest.mark.asyncio
async def test_concurrent_subscribe_during_publish():
    """Subscribing during a publish doesn't crash (handler may or may not see event)."""
    bus = EventBus()
    received = []
    async def h(event):
        received.append(event.type)
    await bus.subscribe(h)
    async def add_handler_later():
        await asyncio.sleep(0.001)  # mid-publish
        async def h2(event):
            pass
        await bus.subscribe(h2)
    await asyncio.gather(
        bus.publish("a", "x"),
        bus.publish("b", "y"),
        add_handler_later(),
    )
    assert "a" in received
    assert "b" in received


# ── Integration: real usage pattern ────────────────────────────────
@pytest.mark.asyncio
async def test_lock_event_pattern():
    """Simulates prismatic.lock emitting a lock event."""
    bus = EventBus()
    received = []
    async def listener(event):
        received.append(event)
    await bus.subscribe(listener)
    # Lock acquired
    await bus.publish("lock", "lock:fred", {"file": "x.py", "agent": "fred"})
    # Heartbeat
    await bus.publish("heartbeat", "lock:fred", {"file": "x.py"})
    # Unlock
    await bus.publish("unlock", "lock:fred", {"file": "x.py"})
    assert len(received) == 3
    assert received[0].type == "lock"
    assert received[1].type == "heartbeat"
    assert received[2].type == "unlock"
    assert received[0].payload == {"file": "x.py", "agent": "fred"}


@pytest.mark.asyncio
async def test_telemetry_event_pattern():
    """Simulates telemetry publishing a credit event."""
    bus = EventBus()
    received = []
    async def listener(event):
        received.append(event)
    await bus.subscribe(listener)
    await bus.publish("telemetry", "credit_tracker", {
        "provider": "google-antigravity",
        "credits_spent": 100,
        "remaining": 24900,
    })
    assert received[0].payload["provider"] == "google-antigravity"
    assert received[0].payload["credits_spent"] == 100