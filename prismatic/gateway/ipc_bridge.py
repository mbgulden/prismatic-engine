"""
prismatic/gateway/ipc_bridge.py — IPC Bridge for Swarm Event Ingestion

Provides two channels for external processes (CLI tools, agents, dispatcher)
to push events into the gateway EventBus:

1. **Unix domain socket** — Zero-network-overhead IPC for local processes.
   Default path: ``$PRISMATIC_STATE_DIR/ipc_bridge.sock``
   Protocol: newline-delimited JSON, one event per line.
   Events are parsed, validated, and published to the EventBus.

2. **HTTP POST endpoint** — For remote or containerized processes.
   Mounted at ``POST /api/gateway/events`` on the gateway FastAPI app.
   Accepts JSON payloads with optional array wrapping.

Both channels validate required fields (type, source) and publish
via :func:`prismatic.gateway.event_bus.get_event_bus().publish()`.

Integration:
    - gateway/server.py registers the HTTP route and starts the Unix socket listener.
    - prismatic/lock.py pushes lock/unlock/heartbeat events via Unix socket or HTTP.
    - Dispatcher pushes agent lifecycle events (launched, completed, failed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.gateway.ipc_bridge")

# Default Unix socket path
DEFAULT_SOCKET_PATH: str = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "ipc_bridge.sock",
)


# ── Event Validation ──────────────────────────────────────────

REQUIRED_FIELDS = {"type", "source"}
VALID_TYPES = {
    "lock", "unlock", "heartbeat", "telemetry",
    "agent_launched", "agent_completed", "agent_failed",
    "agent_heartbeat",
    "governor_allocate", "governor_release",
    "circuit_breaker_trip", "circuit_breaker_reset",
}


def validate_event(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate an incoming event dict. Returns (valid, reason)."""
    if not isinstance(data, dict):
        return False, "Event must be a JSON object"

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        return False, f"Missing required fields: {missing}"

    event_type = data.get("type", "")
    if event_type not in VALID_TYPES:
        return False, (
            f"Unknown event type: {event_type!r}. "
            f"Valid types: {sorted(VALID_TYPES)}"
        )

    return True, ""


# ── Event Publisher (shared by both channels) ─────────────────


async def publish_event(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and publish an event to the EventBus.

    Returns a result dict with ``ok``, ``event``, and optional ``error``.
    """
    valid, reason = validate_event(data)
    if not valid:
        logger.warning("IPC bridge: invalid event: %s", reason)
        return {"ok": False, "error": reason}

    from prismatic.gateway.event_bus import get_event_bus

    bus = get_event_bus()
    event = await bus.publish(
        event_type=data["type"],
        source=data["source"],
        payload=data.get("payload", {}),
    )

    logger.debug(
        "IPC bridge: published event type=%s source=%s",
        data["type"], data["source"],
    )
    return {"ok": True, "event": event.to_dict()}


# ── Channel 1: Unix Domain Socket Listener ────────────────────


class UnixSocketListener:
    """Async Unix domain socket server for swarm event ingestion.

    Listens on a local socket file. Each connection is handled
    independently — the client sends one or more newline-delimited
    JSON objects and disconnects.  Each line is validated and
    published to the EventBus.

    Usage::

        listener = UnixSocketListener()
        await listener.start()
        # ... gateway runs ...
        await listener.stop()
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self._socket_path = socket_path or DEFAULT_SOCKET_PATH
        self._server: asyncio.AbstractServer | None = None
        self._total_connections: int = 0
        self._total_events: int = 0
        self._total_rejected: int = 0

    @property
    def socket_path(self) -> str:
        return self._socket_path

    async def start(self) -> None:
        """Start the Unix socket server."""
        # Clean up stale socket file
        sock_path = Path(self._socket_path)
        if sock_path.exists():
            sock_path.unlink()

        sock_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self._socket_path,
        )
        logger.info(
            "IPC bridge Unix socket listening on %s", self._socket_path
        )

    async def stop(self) -> None:
        """Stop the Unix socket server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        sock_path = Path(self._socket_path)
        if sock_path.exists():
            sock_path.unlink()
        logger.info("IPC bridge Unix socket stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single Unix socket connection."""
        self._total_connections += 1
        peer = writer.get_extra_info("peername", "unknown")

        try:
            data = await asyncio.wait_for(reader.read(65536), timeout=10.0)
            if not data:
                return

            lines = data.decode("utf-8").strip().split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event_data = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._total_rejected += 1
                    logger.warning(
                        "IPC bridge: invalid JSON from %s: %s", peer, exc
                    )
                    continue

                result = await publish_event(event_data)
                if result["ok"]:
                    self._total_events += 1
                else:
                    self._total_rejected += 1

            # Send ACK
            writer.write(b'{"status":"ok"}\n')
            await writer.drain()

        except asyncio.TimeoutError:
            logger.warning("IPC bridge: timeout from %s", peer)
        except (ConnectionResetError, BrokenPipeError):
            logger.debug("IPC bridge: connection reset by %s", peer)
        except Exception:
            logger.exception("IPC bridge: error handling connection from %s", peer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @property
    def stats(self) -> dict[str, int]:
        return {
            "connections": self._total_connections,
            "events_ingested": self._total_events,
            "events_rejected": self._total_rejected,
        }


# ── Channel 2: HTTP POST Endpoint (FastAPI route) ─────────────


def create_event_ingest_route():
    """Create a FastAPI route for HTTP-based event ingestion.

    Returns a FastAPI APIRouter that can be mounted on the gateway app.

    Usage in server.py::

        from prismatic.gateway.ipc_bridge import create_event_ingest_route
        app.include_router(create_event_ingest_route())
    """
    from fastapi import APIRouter, Request, Response, Body

    router = APIRouter()

    @router.post("/events")
    async def ingest_events(body: dict | list = Body(...)) -> dict[str, Any] | Response:
        """Ingest one or more swarm events via HTTP POST.

        Accepts a single event object or an array of event objects.
        Each event must have ``type`` and ``source`` fields.
        """
        # Normalize to list
        events = body if isinstance(body, list) else [body]

        results = []
        for event_data in events:
            result = await publish_event(event_data)
            results.append(result)

        ok_count = sum(1 for r in results if r["ok"])
        fail_count = len(results) - ok_count

        status_code = 200 if fail_count == 0 else 207  # Multi-Status
        return Response(
            status_code=status_code,
            content=json.dumps({
                "total": len(results),
                "ok": ok_count,
                "failed": fail_count,
                "results": results,
            }),
            media_type="application/json",
        )

    @router.get("/events/history")
    async def event_history(limit: int = 50) -> dict[str, Any]:
        """Return recent swarm events from the EventBus history."""
        from prismatic.gateway.event_bus import get_event_bus

        bus = get_event_bus()
        return {
            "events": bus.get_history(limit=limit),
            "stats": bus.stats,
        }

    return router


# ── CLI Client (for external processes) ───────────────────────


def send_event_via_socket(
    event_type: str,
    source: str,
    payload: dict[str, Any] | None = None,
    *,
    socket_path: str | None = None,
    timeout: float = 5.0,
) -> bool:
    """Send an event to the IPC bridge via Unix socket (synchronous).

    This is the interface used by CLI tools like ``prismatic-lock``
    to report lock/unlock operations.

    Args:
        event_type: Event type (e.g. 'lock', 'unlock').
        source: Originating agent/process name.
        payload: Optional event-specific data.
        socket_path: Unix socket path override.
        timeout: Connection timeout in seconds.

    Returns:
        True if the event was accepted, False on failure.
    """
    import socket

    sock_path = socket_path or DEFAULT_SOCKET_PATH

    event = {
        "type": event_type,
        "source": source,
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "payload": payload or {},
    }

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sock_path)
        sock.sendall((json.dumps(event) + "\n").encode("utf-8"))

        # Read ACK
        response = sock.recv(1024)
        sock.close()

        ack = json.loads(response.decode("utf-8"))
        return ack.get("status") == "ok"
    except (FileNotFoundError, ConnectionRefusedError):
        # Socket not available — fail silently, it's best-effort
        logger.debug("IPC bridge socket not available at %s", sock_path)
        return False
    except (socket.timeout, OSError, json.JSONDecodeError) as exc:
        logger.debug("IPC bridge send failed: %s", exc)
        return False
