"""
RedisSignalProvider — pub/sub for distributed swarms.
=======================================================

For multi-machine deployments where agents are spread across
nodes and need sub-second signal delivery. Uses Redis pub/sub
channels so the hub can push to hundreds of agents instantly.

Channel naming convention:
  prismatic:signal:{target}   — work signals
  prismatic:ack:{target}      — acknowledgement channel

Production notes:
- Requires `redis` package (pip install redis) — optional dependency
- Lazy import so the package works without Redis installed
- SignalPayload serialized as JSON over the wire
- Each agent subscribes to its own channel on startup
"""

from __future__ import annotations

import json
import time
from typing import Optional, TYPE_CHECKING

from .base import SignalProvider, SignalPayload

if TYPE_CHECKING:
    import redis


class RedisSignalProvider(SignalProvider):
    """Deliver signals via Redis pub/sub channels.

    Signal flow:
      send()  → PUBLISH prismatic:signal:{target} {JSON payload}
      poll()  → SUBSCRIBE + listen for one message (with timeout)
      ack()   → PUBLISH prismatic:ack:{target} {signal_id}
    """

    # TTL for signal keys (seconds) — prevents memory leak if no agent
    # ever acknowledges
    SIGNAL_TTL = 300  # 5 minutes

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "prismatic:signal",
    ):
        """Connect to Redis.

        Args:
            host: Redis server hostname
            port: Redis server port
            db: Redis database number
            password: Optional Redis AUTH password
            prefix: Channel name prefix
        """
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._prefix = prefix
        self._client: "redis.Redis | None" = None

    @property
    def client(self) -> "redis.Redis":
        """Lazy Redis connection — only import redis when used."""
        if self._client is None:
            import redis
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )
            # Verify connection
            self._client.ping()
        return self._client

    def _channel(self, target: str) -> str:
        """Channel name for a given agent target."""
        return f"{self._prefix}:{target}"

    # ── public API ────────────────────────────────────────────

    def send(self, target: str, payload: SignalPayload) -> bool:
        """Publish a signal to the agent's channel.

        Redis pub/sub is fire-and-forget — if no agent is subscribed,
        the message is dropped. For guaranteed delivery, combine with
        a dead-letter queue or use Redis streams instead.
        """
        try:
            data = json.dumps(payload.to_dict())

            # Store signal in a key with TTL so the agent can poll if
            # it missed the pub/sub message (e.g., restart)
            signal_key = f"prismatic:pending:{target}:{payload.signal_id}"
            self.client.setex(signal_key, self.SIGNAL_TTL, data)

            # Publish to the live channel
            subscribers = self.client.publish(self._channel(target), data)

            return subscribers > 0  # True if at least one agent received it
        except Exception as exc:
            print(f"[RedisSignalProvider] send({target}) failed: {exc}")
            return False

    def poll(self, target: str, timeout: float = 0) -> SignalPayload | None:
        """Check for pending signals for this agent.

        First checks the pending keys (for signals sent while agent
        was offline), then listens on the pub/sub channel (for live
        signals arriving right now).

        Args:
            timeout: Seconds to wait for a live signal. 0 = non-blocking,
                     returns immediately if nothing pending.
        """
        try:
            # 1. Check for any stored pending signals (offline recovery)
            pending_pattern = f"prismatic:pending:{target}:*"
            keys = list(self.client.scan_iter(match=pending_pattern, count=10))
            if keys:
                # Return the highest-priority pending signal
                best: SignalPayload | None = None
                best_priority = -1
                for key in keys:
                    raw = self.client.get(key)
                    if raw:
                        try:
                            p = SignalPayload.from_dict(json.loads(raw))
                            if p.priority > best_priority:
                                best = p
                                best_priority = p.priority
                        except (json.JSONDecodeError, KeyError):
                            # Corrupt — clean up
                            self.client.delete(key)
                if best:
                    return best

            # 2. Listen for a live signal via pub/sub
            if timeout <= 0:
                return None

            pubsub = self.client.pubsub()
            pubsub.subscribe(self._channel(target))

            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = deadline - time.time()
                message = pubsub.get_message(timeout=min(remaining, 1.0))
                if message and message["type"] == "message":
                    try:
                        payload = SignalPayload.from_dict(
                            json.loads(message["data"])
                        )
                        pubsub.unsubscribe()
                        pubsub.close()
                        return payload
                    except (json.JSONDecodeError, KeyError):
                        continue  # Malformed message, keep listening

            pubsub.unsubscribe()
            pubsub.close()
            return None

        except Exception as exc:
            print(f"[RedisSignalProvider] poll({target}) failed: {exc}")
            return None

    def acknowledge(self, signal_id: str) -> bool:
        """Remove the pending signal key after processing."""
        try:
            # Find and delete the pending key for this signal
            for key in self.client.scan_iter(match="prismatic:pending:*"):
                if signal_id in key:
                    self.client.delete(key)
                    return True
            return False
        except Exception as exc:
            print(f"[RedisSignalProvider] acknowledge({signal_id}) failed: {exc}")
            return False

    def list_targets(self) -> list[str]:
        """Return all agent targets with pending signals."""
        targets = set()
        try:
            for key in self.client.scan_iter(match="prismatic:pending:*"):
                # Key format: prismatic:pending:{target}:{signal_id}
                parts = key.split(":")
                if len(parts) >= 3:
                    targets.add(parts[2])
        except Exception:
            pass
        return sorted(targets)
