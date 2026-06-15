"""
prismatic/gateway/ws_broadcaster.py — WebSocket broadcast server for swarm events.

Subscribes to the EventBus and forwards all events to connected WebSocket
dashboard clients in real-time.  Starts an asyncio WebSocket server on a
configurable port (default: 8765, mapped to Firewall/Gateway port 9000).

Integration:
    - gateway/__init__.py calls start_ws_broadcaster() during gateway startup
    - Runs in a daemon thread with its own event loop
    - Uses the module-level EventBus singleton via get_event_bus()
    - Client protocol: send "ping" → receive "pong" with subscriber count
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Set

import websockets
from websockets.asyncio.server import ServerConnection

from prismatic.gateway.event_bus import SwarmEvent, get_event_bus

logger = logging.getLogger("prismatic.gateway.ws_broadcaster")

# ── Config ──────────────────────────────────────────────

DEFAULT_WS_HOST = "0.0.0.0"
DEFAULT_WS_PORT = 8765  # Mapped to gateway port 9000 via reverse proxy


def _get_config() -> tuple[str, int]:
    host = os.environ.get("PRISMATIC_WS_HOST", DEFAULT_WS_HOST)
    port = int(os.environ.get("PRISMATIC_WS_PORT", str(DEFAULT_WS_PORT)))
    return host, port


# ── Broadcaster ─────────────────────────────────────────


class WSBroadcaster:
    """WebSocket broadcast server that subscribes to EventBus.

    Lifecycle:
        start()     — Launch the WebSocket server in a daemon thread.
        stop()      — Close all connections and stop the server.
    """

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or _get_config()[0]
        self.port = port or _get_config()[1]

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._clients: Set[ServerConnection] = set()
        self._active = False

    # ── Public API ────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket server in a background daemon thread."""
        if self._active:
            logger.warning("WSBroadcaster already running")
            return

        self._active = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("WSBroadcaster starting on ws://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Stop the WebSocket server and disconnect all clients."""
        self._active = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop())

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ── Internal ──────────────────────────────────────

    def _run_loop(self) -> None:
        """Daemon thread entry point — runs the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception:
            logger.exception("WSBroadcaster event loop crashed")
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        """Start the WebSocket server and subscribe to EventBus."""
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
        )
        logger.info(
            "WSBroadcaster WebSocket server listening on ws://%s:%d",
            self.host,
            self.port,
        )

        # Subscribe to EventBus — forward all events to clients
        bus = get_event_bus()
        await bus.subscribe(self._on_event)

        # Keep running until stopped
        while self._active:
            await asyncio.sleep(1)

    async def _shutdown(self) -> None:
        """Close server and disconnect clients."""
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("WSBroadcaster shut down")

    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a new WebSocket client connection."""
        self._clients.add(websocket)
        logger.info("Dashboard connected (total=%d)", len(self._clients))

        # Send initial connection metadata
        connect_msg = {
            "type": "connected",
            "source": "ws_broadcaster",
            "payload": {"active_subscribers": len(self._clients)},
        }
        try:
            await websocket.send(json.dumps(connect_msg))
        except Exception:
            pass

        try:
            async for message in websocket:
                # Ping/pong for client-side keepalive
                if message.strip().lower() == "ping":
                    pong = {
                        "type": "pong",
                        "source": "ws_broadcaster",
                        "payload": {"clients": len(self._clients)},
                    }
                    await websocket.send(json.dumps(pong))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("Dashboard disconnected (total=%d)", len(self._clients))

    async def _on_event(self, event: SwarmEvent) -> None:
        """EventBus handler — broadcast event to all connected clients."""
        if not self._clients:
            return

        message = json.dumps(event.to_dict(), default=str)
        dead: list[ServerConnection] = []

        for ws in self._clients:
            try:
                await ws.send(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._clients.discard(ws)


# ── Convenience ─────────────────────────────────────────

_broadcaster: WSBroadcaster | None = None


def start_ws_broadcaster(
    host: str | None = None, port: int | None = None
) -> WSBroadcaster:
    """Start (or return existing) WebSocket broadcaster singleton."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = WSBroadcaster(host=host, port=port)
        _broadcaster.start()
    return _broadcaster


def stop_ws_broadcaster() -> None:
    """Stop the WebSocket broadcaster singleton."""
    global _broadcaster
    if _broadcaster:
        _broadcaster.stop()
        _broadcaster = None
