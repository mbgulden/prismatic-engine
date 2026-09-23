"""Deploy lifecycle events pushed to the gateway event bus (Portal Phase 1, P0 #3).

The receiver deploy pipeline is a separate process from the gateway, so
deploy lifecycle can no longer be log-file-only: these events let the portal
update live over the gateway `/ws` instead of polling ``alerts.log``.

Push path (event-based, localhost-only, no new network exposure):

1. The pipeline calls :func:`emit_deploy_event` at each lifecycle moment
   (``pe.deploy.receiver`` for started/succeeded/failed,
   ``pe.deploy.gateway_redeploy`` for gateway-step failed/rolled_back).
2. This module opens an AF_UNIX stream socket to the gateway IPC bridge
   (``$PRISMATIC_STATE_DIR/ipc_bridge.sock`` -- the same socket the live
   gateway listens on) and writes one newline-delimited JSON event, then
   reads the ``{"status":"ok"}`` ACK. Protocol mirrors
   ``prismatic.gateway.ipc_bridge.send_event_via_socket``.
3. The gateway validates the type, publishes to the EventBus, and the
   Phase-1 forwarder relays ``deploy.*`` events to ``/ws`` subscribers.

Stdlib only -- no prismatic imports -- so this stays importable in the same
minimal environments as ``pe.deploy.gateway_redeploy`` and unit-testable
with fakes.

Additive only: emission never raises and never changes deploy, rollback,
phase, metric, or merge-authority behavior. Every failure is swallowed into
``logging`` and reported as a boolean.

Event schema (the contract the Phase-2 portal consumes)::

    {
        "type": "deploy.started" | "deploy.succeeded"
              | "deploy.failed" | "deploy.rolled_back",
        "source": "deploy-receiver",
        "timestamp": "<ISO-8601 UTC>",
        "payload": {
            "deploy_id": "deploy-<sha8>",      # pipeline-level events
            "repo": "owner/repo",              # pipeline-level events
            "pr_sha": "<full sha>",
            "pr_number": 123,
            "pr_title": "...",
            "deployer": "github-action",
            "trigger": "...",                  # pass-through, only when known
            "duration_ms": 12345,              # terminal events
            "success": true,                   # terminal events
            "failure_reason": "...",           # deploy.failed
            "step": "pipeline" | "gateway-redeploy",
            "phase": "started"|"completed"|"failed",  # deploy.rolled_back
            "rolled_back": true,               # deploy.failed (pipeline)
            "previous_release": "...",         # deploy.rolled_back
            "restored_release": "...",         # deploy.rolled_back completed
            "failed_release": "...",           # deploy.rolled_back started
            "attempted_release": "...",        # deploy.failed (gateway step)
            "version_dir": "...",              # deploy.succeeded
            "node": "athens",                  # node-routed deploys
        },
    }
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Event types this module may emit. ``deploy.rolled_back`` carries a
#: ``phase`` payload field (started/completed/failed) so the portal can
#: distinguish the rollback-started and rollback-completed moments while
#: the bus type stays a single stable contract.
DEPLOY_EVENT_TYPES = (
    "deploy.started",
    "deploy.succeeded",
    "deploy.failed",
    "deploy.rolled_back",
)

#: ``source`` field identifying the emitting process on the bus.
EVENT_SOURCE = "deploy-receiver"

_IPC_SOCKET_FILENAME = "ipc_bridge.sock"


def default_ipc_socket_path() -> Path:
    """Resolve the gateway IPC-bridge unix socket path.

    Precedence:
      1. ``$PRISMATIC_IPC_BRIDGE_SOCK`` when set (explicit override / tests).
      2. ``$PRISMATIC_STATE_DIR/ipc_bridge.sock`` -- converges with the live
         gateway, whose ``PRISMATIC_STATE_DIR`` is ``~/.prismatic/db``.
      3. ``~/.prismatic/db/ipc_bridge.sock`` -- the deploy pipeline's home.
         Deliberately not the CWD-relative ``./prismatic_state`` default: the
         receiver runs under systemd and a CWD-relative path is not
         deterministic there (same reasoning as ``deploy_alerts``).
    """
    override = os.environ.get("PRISMATIC_IPC_BRIDGE_SOCK")
    if override:
        return Path(override).expanduser()
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser() / _IPC_SOCKET_FILENAME
    return Path.home() / ".prismatic" / "db" / _IPC_SOCKET_FILENAME


def build_deploy_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one bus event dict. Raises ValueError on an unknown type."""
    if event_type not in DEPLOY_EVENT_TYPES:
        raise ValueError(
            f"unknown deploy event type {event_type!r}; "
            f"valid: {list(DEPLOY_EVENT_TYPES)}"
        )
    return {
        "type": event_type,
        "source": EVENT_SOURCE,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "payload": dict(payload or {}),
    }


def emit_deploy_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    socket_path: Path | str | None = None,
    timeout: float = 5.0,
) -> bool:
    """Push one deploy event to the gateway event bus. Never raises.

    Returns True when the gateway ACKed the event, False otherwise
    (socket absent, timeout, bad ACK). A dropped event is a debug log --
    the alert log remains the durable record, so observability can never
    break the deploy path. ``socket_path`` and the
    ``PRISMATIC_IPC_BRIDGE_SOCK`` env var are injectable for tests.
    """
    try:
        event = build_deploy_event(event_type, payload)
    except ValueError as exc:
        logger.error("deploy-events: refusing to emit: %s", exc)
        return False

    path = (
        Path(socket_path).expanduser()
        if socket_path is not None
        else default_ipc_socket_path()
    )
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
            sock.sendall((json.dumps(event, default=str) + chr(10)).encode("utf-8"))
            ack_raw = sock.recv(1024)
        finally:
            sock.close()
        ack = json.loads(ack_raw.decode("utf-8"))
        return ack.get("status") == "ok"
    except (FileNotFoundError, ConnectionRefusedError):
        # Gateway not listening -- best-effort, fail silently (debug only).
        logger.debug("deploy-events: IPC bridge socket not available at %s", path)
        return False
    except (TimeoutError, OSError, ValueError) as exc:
        # ValueError covers a malformed ACK body.
        logger.debug("deploy-events: send to %s failed: %s", path, exc)
        return False
