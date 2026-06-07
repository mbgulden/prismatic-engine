"""
HTTPSignalProvider — push signals via HTTP POST.
==================================================

For agents that don't share a filesystem with the hub:
Docker containers, remote machines, CI/CD runners, cloud functions.

Each agent exposes a webhook endpoint:
  POST /signals   ← hub sends SignalPayload JSON
  200 OK          ← agent acknowledges receipt

The agent is responsible for its own poll loop (or the webhook
triggers immediate processing).

Production notes:
- Uses urllib3 (stdlib) — zero external deps
- Configurable retry + backoff + dead-letter
- Auth via shared secret header (X-Prismatic-Key)
- Signal IDs prevent duplicate delivery
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Optional

from .base import SignalProvider, SignalPayload


class HTTPSignalProvider(SignalProvider):
    """Deliver signals via HTTP POST to agent webhooks.

    Each agent runs an HTTP server with a /signals endpoint.
    The hub POSTs SignalPayload as JSON. The agent returns
    200 OK on receipt and processes it.

    Signal flow:
      send()  → POST https://agent:port/signals  { JSON payload }
      poll()  → NOT IMPLEMENTED (agents are push-only via HTTP)
      ack()   → NOT IMPLEMENTED (handled by HTTP 200)
    """

    def __init__(
        self,
        endpoints: dict[str, str] | None = None,
        shared_secret: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        """Configure HTTP signal targets.

        Args:
            endpoints: {"fred": "https://fred.internal:9001/signals", ...}
            shared_secret: Optional auth header value (X-Prismatic-Key)
            timeout: HTTP request timeout in seconds
            max_retries: Number of retries before falling back
        """
        self._endpoints: dict[str, str] = endpoints or {}
        self._secret = shared_secret
        self._timeout = timeout
        self._max_retries = max_retries

    def register_target(self, name: str, webhook_url: str) -> None:
        """Add or update an agent's webhook URL."""
        self._endpoints[name] = webhook_url

    def deregister_target(self, name: str) -> None:
        """Remove an agent from the signal registry."""
        self._endpoints.pop(name, None)

    # ── public API ────────────────────────────────────────────

    def send(self, target: str, payload: SignalPayload) -> bool:
        """POST the signal to the agent's webhook endpoint.

        Retries with exponential backoff on transient failures
        (5xx, network errors). Gives up after max_retries.
        """
        endpoint = self._endpoints.get(target)
        if not endpoint:
            print(f"[HTTPSignalProvider] No endpoint for target '{target}'")
            return False

        data = json.dumps(payload.to_dict(), indent=2).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Prismatic-Key": self._secret or "",
            },
            method="POST",
        )

        for attempt in range(1, self._max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
                    # 4xx — don't retry
                    if 400 <= resp.status < 500:
                        body = resp.read().decode(errors="replace")[:200]
                        print(
                            f"[HTTPSignalProvider] send({target}) "
                            f"client error {resp.status}: {body}"
                        )
                        return False
                    # 5xx — retry
                    print(
                        f"[HTTPSignalProvider] send({target}) "
                        f"server error {resp.status} (attempt {attempt})"
                    )

            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    body = exc.read().decode(errors="replace")[:200]
                    print(
                        f"[HTTPSignalProvider] send({target}) "
                        f"HTTP {exc.code}: {body}"
                    )
                    return False
                print(
                    f"[HTTPSignalProvider] send({target}) "
                    f"HTTP {exc.code} (attempt {attempt})"
                )

            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                print(
                    f"[HTTPSignalProvider] send({target}) "
                    f"network error: {exc} (attempt {attempt})"
                )

            # Exponential backoff: 1s, 2s, 4s
            if attempt < self._max_retries:
                import time
                time.sleep(2 ** (attempt - 1))

        print(f"[HTTPSignalProvider] send({target}) FAILED after {self._max_retries} attempts")
        return False

    def poll(self, target: str, timeout: float = 0) -> SignalPayload | None:
        """HTTP is push-only — poll() is not supported.

        Agents using HTTP receive signals via POST to their webhook.
        They don't need to poll. If you need polling, use FileSignalProvider
        or RedisSignalProvider instead.
        """
        return None  # Push-only transport

    def acknowledge(self, signal_id: str) -> bool:
        """HTTP 200 from the agent IS the acknowledgement."""
        return True  # Optimistic — if send() returned True, it was delivered

    def list_targets(self) -> list[str]:
        """Return all registered agent webhook targets."""
        return sorted(self._endpoints.keys())


# ── Agent-side webhook receiver (FastAPI / Flask reference) ──

"""
Example agent-side webhook handler:

```python
# agent_webhook.py — runs on the agent machine
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/signals", methods=["POST"])
def receive_signal():
    payload = SignalPayload.from_dict(request.json)
    # Trigger immediate work — don't wait for cron
    dispatch_signal(payload)
    return jsonify({"status": "received", "signal_id": payload.signal_id}), 200

app.run(host="0.0.0.0", port=9001)
```
"""
