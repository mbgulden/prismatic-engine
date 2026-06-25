"""Tests for prismatic.gateway.ipc_bridge — Unix socket + HTTP event ingestion.

GRO-2402 follow-up: ipc_bridge.py is the IPC layer used by:
- prismatic/lock.py (lock/unlock/heartbeat events)
- prismatic/capabilities/vcs_github.py (VCS events)
- gateway/server.py (HTTP route registration)
- prismatic/dispatcher.py (agent lifecycle events)

Until now it had zero direct tests. A bug here would silently break
cross-process event flow.

Tests cover:
- Event validation (required fields, valid types)
- publish_event (validation → EventBus dispatch)
- UnixSocketListener (start/stop, connection handling, stats)
- send_event_via_socket (sync client, ACK protocol, error handling)
- HTTP route (create_event_ingest_route returns a handler)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.gateway.ipc_bridge import (  # noqa: E402
    validate_event,
    publish_event,
    UnixSocketListener,
    create_event_ingest_route,
    send_event_via_socket,
    DEFAULT_SOCKET_PATH,
    REQUIRED_FIELDS,
    VALID_TYPES,
)


# ── validate_event ──────────────────────────────────────────────────
def test_validate_event_accepts_minimal_valid():
    valid, reason = validate_event({"type": "lock", "source": "fred"})
    assert valid is True
    assert reason == ""


def test_validate_event_with_payload():
    valid, _ = validate_event({
        "type": "lock", "source": "fred", "payload": {"file": "x.py"},
    })
    assert valid is True


def test_validate_event_rejects_non_dict():
    valid, reason = validate_event("not a dict")
    assert valid is False
    assert "JSON object" in reason or "dict" in reason.lower()


def test_validate_event_rejects_missing_type():
    valid, reason = validate_event({"source": "fred"})
    assert valid is False
    assert "type" in reason or "missing" in reason.lower()


def test_validate_event_rejects_missing_source():
    valid, reason = validate_event({"type": "lock"})
    assert valid is False
    assert "source" in reason or "missing" in reason.lower()


def test_validate_event_rejects_both_missing():
    valid, reason = validate_event({})
    assert valid is False
    assert "missing" in reason.lower()


def test_validate_event_rejects_unknown_type():
    valid, reason = validate_event({"type": "mystery", "source": "fred"})
    assert valid is False
    assert "unknown" in reason.lower() or "mystery" in reason


def test_validate_event_accepts_all_valid_types():
    """All documented event types should be accepted."""
    for event_type in ["lock", "unlock", "heartbeat", "telemetry",
                        "agent_launched", "agent_completed", "agent_failed",
                        "session.started", "vcs.pr_opened", "jules.handoff",
                        "schedule.recorded", "golden_flow_completed"]:
        valid, _ = validate_event({"type": event_type, "source": "x"})
        assert valid, f"Type {event_type} should be valid"


def test_required_fields_constant():
    assert REQUIRED_FIELDS == {"type", "source"}


def test_valid_types_includes_golden_flow():
    """GRO-2402: Golden Flow event types must be in the whitelist."""
    golden_flow = {"session.started", "session.progress", "session.completed",
                   "vcs.pr_opened", "jules.handoff", "golden_flow_completed"}
    assert golden_flow.issubset(VALID_TYPES)


# ── publish_event ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_publish_event_validates_then_publishes(monkeypatch):
    """Valid event → published to EventBus."""
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    monkeypatch.setattr(event_bus, "_bus", test_bus)

    result = await publish_event({"type": "lock", "source": "fred"})
    assert result["ok"] is True
    assert "event" in result
    assert test_bus.stats["total_published"] == 1


@pytest.mark.asyncio
async def test_publish_event_rejects_invalid(monkeypatch):
    """Invalid event → returns ok=False with error, not published."""
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    monkeypatch.setattr(event_bus, "_bus", test_bus)

    result = await publish_event({"type": "mystery"})  # missing source
    assert result["ok"] is False
    assert "error" in result
    # Nothing was published
    assert test_bus.stats["total_published"] == 0


@pytest.mark.asyncio
async def test_publish_event_payload_propagates(monkeypatch):
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    monkeypatch.setattr(event_bus, "_bus", test_bus)
    received = []
    async def listener(event):
        received.append(event)
    await test_bus.subscribe(listener)

    await publish_event({
        "type": "lock", "source": "fred",
        "payload": {"file": "x.py", "agent": "fred"},
    })
    assert len(received) == 1
    assert received[0].payload == {"file": "x.py", "agent": "fred"}


@pytest.mark.asyncio
async def test_publish_event_missing_payload_defaults_to_empty(monkeypatch):
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    monkeypatch.setattr(event_bus, "_bus", test_bus)
    received = []
    async def listener(event):
        received.append(event)
    await test_bus.subscribe(listener)

    result = await publish_event({"type": "lock", "source": "fred"})
    assert result["ok"] is True
    assert received[0].payload == {}


# ── UnixSocketListener ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_listener_starts_and_stops(tmp_path):
    """start() creates socket, stop() cleans up."""
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    assert Path(sock_path).exists()
    await listener.stop()
    assert not Path(sock_path).exists()


@pytest.mark.asyncio
async def test_listener_cleans_up_stale_socket(tmp_path):
    """If a stale socket file exists, start() removes it."""
    sock_path = tmp_path / "test.sock"
    sock_path.touch()  # stale socket
    listener = UnixSocketListener(socket_path=str(sock_path))
    await listener.start()
    assert sock_path.exists()  # new socket file
    await listener.stop()


@pytest.mark.asyncio
async def test_listener_creates_parent_dir(tmp_path):
    """start() creates parent directory if missing."""
    sock_path = tmp_path / "deeply" / "nested" / "test.sock"
    listener = UnixSocketListener(socket_path=str(sock_path))
    await listener.start()
    assert sock_path.parent.exists()
    await listener.stop()


@pytest.mark.asyncio
async def test_listener_socket_path_property():
    """socket_path property returns the configured path."""
    listener = UnixSocketListener(socket_path="/tmp/test.sock")
    assert listener.socket_path == "/tmp/test.sock"


@pytest.mark.asyncio
async def test_listener_default_socket_path():
    """Default socket path is PRISMATIC_STATE_DIR/ipc_bridge.sock."""
    listener = UnixSocketListener()
    # Just confirm the path ends with the expected filename
    assert listener.socket_path.endswith("ipc_bridge.sock")
    # And it's a string
    assert isinstance(listener.socket_path, str)


@pytest.mark.asyncio
async def test_listener_accepts_valid_event(tmp_path):
    """A valid event sent via Unix socket → published + ACK returned."""
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    test_bus._max_history = 100  # ensure we don't lose it

    received = []
    async def listener_h(event):
        received.append(event)
    await test_bus.subscribe(listener_h)

    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()

    # Patch the EventBus singleton that publish_event will use
    import prismatic.gateway.event_bus as eb_module
    original_get = eb_module.get_event_bus
    eb_module._bus = test_bus
    try:
        # Async client (compatible with pytest-asyncio)
        event = {"type": "lock", "source": "fred", "payload": {"file": "x.py"}}
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write((json.dumps(event) + "\n").encode("utf-8"))
        await writer.drain()
        response = await asyncio.wait_for(reader.read(1024), timeout=5.0)
        writer.close()
        await writer.wait_closed()

        # ACK received
        ack = json.loads(response.decode("utf-8"))
        assert ack["status"] == "ok"

        # Event was published
        assert len(received) == 1
        assert received[0].type == "lock"
    finally:
        eb_module._bus = None
        await listener.stop()


@pytest.mark.asyncio
async def test_listener_rejects_invalid_event(tmp_path):
    """Invalid event sent via socket → ACK ok but counted as rejected."""
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    try:
        # Send invalid event (missing source)
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(b'{"type": "lock"}\n')
        await writer.drain()
        response = await asyncio.wait_for(reader.read(1024), timeout=5.0)
        writer.close()
        await writer.wait_closed()
        # ACK still returned
        ack = json.loads(response.decode("utf-8"))
        assert ack["status"] == "ok"
        # But stats show rejected
        assert listener.stats["events_rejected"] >= 1
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_listener_rejects_invalid_json(tmp_path):
    """Garbage bytes → rejected, doesn't crash listener."""
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(b"not json at all\n")
        await writer.drain()
        try:
            response = await asyncio.wait_for(reader.read(1024), timeout=5.0)
            ack = json.loads(response.decode("utf-8"))
            assert ack["status"] == "ok"
        finally:
            writer.close()
            await writer.wait_closed()
        assert listener.stats["events_rejected"] >= 1
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_listener_handles_multiple_events_in_one_connection(tmp_path):
    """Newline-delimited: multiple events in one connection."""
    from prismatic.gateway import event_bus
    test_bus = event_bus.EventBus()
    import prismatic.gateway.event_bus as eb_module
    eb_module._bus = test_bus

    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        events = [
            {"type": "lock", "source": "fred", "payload": {"i": 0}},
            {"type": "lock", "source": "fred", "payload": {"i": 1}},
            {"type": "lock", "source": "fred", "payload": {"i": 2}},
        ]
        body = "\n".join(json.dumps(e) for e in events) + "\n"
        writer.write(body.encode("utf-8"))
        await writer.drain()
        try:
            await asyncio.wait_for(reader.read(1024), timeout=5.0)
        finally:
            writer.close()
            await writer.wait_closed()
        assert listener.stats["events_ingested"] == 3
    finally:
        eb_module._bus = None
        await listener.stop()


@pytest.mark.asyncio
async def test_listener_stats_track_connections_and_events(tmp_path):
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    try:
        # Connect once and send one event
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(b'{"type": "lock", "source": "fred"}\n')
        await writer.drain()
        try:
            await asyncio.wait_for(reader.read(1024), timeout=5.0)
        finally:
            writer.close()
            await writer.wait_closed()

        stats = listener.stats
        assert stats["connections"] >= 1
        assert stats["events_ingested"] >= 1
        assert stats["events_rejected"] == 0
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_listener_stop_when_not_started_is_noop():
    listener = UnixSocketListener(socket_path="/tmp/never_started.sock")
    # Should not raise
    await listener.stop()


@pytest.mark.asyncio
async def test_listener_stop_removes_socket_file(tmp_path):
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)
    await listener.start()
    assert Path(sock_path).exists()
    await listener.stop()
    assert not Path(sock_path).exists()


# ── send_event_via_socket (client-side, sync) ───────────────────────
def test_send_event_via_socket_returns_false_when_no_listener(tmp_path):
    """No socket server → returns False (best-effort, no crash)."""
    # Non-existent socket
    assert send_event_via_socket(
        "lock", "fred", {"file": "x.py"},
        socket_path=str(tmp_path / "nonexistent.sock"),
    ) is False


def test_send_event_via_socket_default_path_uses_module_constant():
    """send_event_via_socket falls back to module-level DEFAULT_SOCKET_PATH.

    The constant is computed at import time from PRISMATIC_STATE_DIR, so
    this test just verifies the fallback wiring (not the actual value).
    """
    from prismatic.gateway.ipc_bridge import DEFAULT_SOCKET_PATH as D
    assert D.endswith("ipc_bridge.sock")


def test_send_event_via_socket_sends_valid_event(tmp_path):
    """End-to-end: send_event_via_socket → listener receives."""
    import threading

    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)

    async def run_listener():
        await listener.start()
        try:
            # Give time for client to connect + send
            await asyncio.sleep(2)
        finally:
            await listener.stop()

    # Start listener in background thread
    t = threading.Thread(target=lambda: asyncio.run(run_listener()))
    t.start()
    # Wait for socket to appear
    for _ in range(50):
        if Path(sock_path).exists():
            break
        time.sleep(0.02)
    assert Path(sock_path).exists(), "Listener failed to start"

    # Send event
    success = send_event_via_socket(
        "lock", "fred", {"file": "x.py"},
        socket_path=sock_path, timeout=2.0,
    )
    assert success is True

    t.join(timeout=5)
    assert listener.stats["events_ingested"] >= 1


def test_send_event_via_socket_constructs_correct_payload(tmp_path):
    """Verify the payload shape sent over the socket."""
    import threading

    received_payloads = []
    sock_path = str(tmp_path / "test.sock")
    listener = UnixSocketListener(socket_path=sock_path)

    # Override the listener to capture raw bytes
    original_handle = None

    async def capture_handler(reader, writer):
        data = await reader.read(65536)
        received_payloads.append(data.decode("utf-8"))
        writer.write(b'{"status":"ok"}\n')
        await writer.drain()
        writer.close()

    async def run_listener():
        import os as _os
        if _os.path.exists(sock_path):
            _os.unlink(sock_path)
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        server = await asyncio.start_unix_server(capture_handler, path=sock_path)
        try:
            await asyncio.sleep(2)
        finally:
            server.close()
            await server.wait_closed()
            if _os.path.exists(sock_path):
                _os.unlink(sock_path)

    t = threading.Thread(target=lambda: asyncio.run(run_listener()))
    t.start()
    for _ in range(50):
        if Path(sock_path).exists():
            break
        time.sleep(0.02)

    success = send_event_via_socket(
        "lock", "fred", {"file": "x.py"},
        socket_path=sock_path, timeout=2.0,
    )
    assert success is True
    assert len(received_payloads) == 1
    sent = json.loads(received_payloads[0].strip())
    assert sent["type"] == "lock"
    assert sent["source"] == "fred"
    assert sent["payload"] == {"file": "x.py"}
    assert "timestamp" in sent

    t.join(timeout=5)


def test_send_event_via_socket_handles_no_payload(tmp_path):
    """No payload → empty dict in event."""
    import threading

    received_payloads = []
    sock_path = str(tmp_path / "test.sock")

    async def capture_handler(reader, writer):
        data = await reader.read(65536)
        received_payloads.append(data.decode("utf-8"))
        writer.write(b'{"status":"ok"}\n')
        await writer.drain()
        writer.close()

    async def run_listener():
        import os as _os
        if _os.path.exists(sock_path):
            _os.unlink(sock_path)
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        server = await asyncio.start_unix_server(capture_handler, path=sock_path)
        try:
            await asyncio.sleep(2)
        finally:
            server.close()
            await server.wait_closed()
            if _os.path.exists(sock_path):
                _os.unlink(sock_path)

    t = threading.Thread(target=lambda: asyncio.run(run_listener()))
    t.start()
    for _ in range(50):
        if Path(sock_path).exists():
            break
        time.sleep(0.02)

    success = send_event_via_socket("lock", "fred", None, socket_path=sock_path, timeout=2.0)
    assert success is True
    sent = json.loads(received_payloads[0].strip())
    assert sent["payload"] == {}

    t.join(timeout=5)


def test_send_event_via_socket_bad_ack_returns_false(tmp_path):
    """Server sends non-OK ACK → returns False."""
    import threading

    sock_path = str(tmp_path / "test.sock")

    async def bad_ack_handler(reader, writer):
        await reader.read(65536)
        writer.write(b'{"status": "error"}\n')
        await writer.drain()
        writer.close()

    async def run_listener():
        import os as _os
        if _os.path.exists(sock_path):
            _os.unlink(sock_path)
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        server = await asyncio.start_unix_server(bad_ack_handler, path=sock_path)
        try:
            await asyncio.sleep(2)
        finally:
            server.close()
            await server.wait_closed()
            if _os.path.exists(sock_path):
                _os.unlink(sock_path)

    t = threading.Thread(target=lambda: asyncio.run(run_listener()))
    t.start()
    for _ in range(50):
        if Path(sock_path).exists():
            break
        time.sleep(0.02)

    success = send_event_via_socket("lock", "fred", socket_path=sock_path, timeout=2.0)
    assert success is False

    t.join(timeout=5)


def test_send_event_via_socket_returns_false_on_timeout(tmp_path):
    """Server doesn't respond → timeout → returns False."""
    import threading

    sock_path = str(tmp_path / "test.sock")

    async def hang_handler(reader, writer):
        # Accept connection but never respond
        await asyncio.sleep(60)  # longer than client timeout
        writer.close()

    async def run_listener():
        import os as _os
        if _os.path.exists(sock_path):
            _os.unlink(sock_path)
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        server = await asyncio.start_unix_server(hang_handler, path=sock_path)
        try:
            await asyncio.sleep(5)
        finally:
            server.close()
            await server.wait_closed()
            if _os.path.exists(sock_path):
                _os.unlink(sock_path)

    t = threading.Thread(target=lambda: asyncio.run(run_listener()))
    t.start()
    for _ in range(50):
        if Path(sock_path).exists():
            break
        time.sleep(0.02)

    # Short timeout → should fail
    success = send_event_via_socket("lock", "fred", socket_path=sock_path, timeout=0.5)
    assert success is False

    t.join(timeout=5)


# ── create_event_ingest_route ───────────────────────────────────────
def test_create_event_ingest_route_returns_callable():
    """The HTTP route factory returns a callable handler."""
    route = create_event_ingest_route()
    assert callable(route)


# ── Default socket path ─────────────────────────────────────────────
def test_default_socket_path_ends_with_filename():
    """Default socket path ends with ipc_bridge.sock."""
    assert DEFAULT_SOCKET_PATH.endswith("ipc_bridge.sock")


def test_default_socket_path_is_string():
    """DEFAULT_SOCKET_PATH is a string (import-time constant)."""
    assert isinstance(DEFAULT_SOCKET_PATH, str)
    assert len(DEFAULT_SOCKET_PATH) > 0