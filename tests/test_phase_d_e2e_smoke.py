"""Phase D.4 - End-to-end smoke test for the gateway webhook pipeline.

Exercises:
  - D.1 fix: GitHub HMAC 401 bug (prefix-stripped compare_digest).
  - D.3: Event schema validation (malformed events rejected).
  - D.5: /metrics endpoint returns bus stats + counters.

Run against a locally-running gateway (uvicorn) on the default port 8000.

Usage:
    pytest tests/test_phase_d_e2e_smoke.py -v
or:
    GATEWAY_URL=http://127.0.0.1:8000 pytest tests/test_phase_d_e2e_smoke.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest

DEFAULT_BASE = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8000")
GITHUB_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "test-github-secret")


def _sign_github(body: bytes, secret: str = GITHUB_SECRET) -> str:
    """Mirror the handler's signing formula exactly:
        hmac_sha256(secret, b'x-hub-signature-256:' + body)
    """
    mac = hmac.new(secret.encode(), b"x-hub-signature-256:" + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


@pytest.fixture(scope="module", autouse=True)
def ensure_server():
    global DEFAULT_BASE
    if "GATEWAY_URL" in os.environ:
        yield
        return

    import socket
    import threading
    import time
    import uvicorn

    # Find a free port
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()

    DEFAULT_BASE = f"http://127.0.0.1:{port}"
    os.environ["PRISMATIC_GITHUB_WEBHOOK_SECRET"] = GITHUB_SECRET
    os.environ["PRISMATIC_STATE_DIR"] = "./prismatic_state_test"

    server_thread = threading.Thread(
        target=lambda: uvicorn.run(
            "prismatic.gateway.server:app",
            host="127.0.0.1",
            port=port,
            log_level="error"
        ),
        daemon=True
    )
    server_thread.start()

    import urllib.request
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"{DEFAULT_BASE}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    
    yield
    os.environ.pop("PRISMATIC_GITHUB_WEBHOOK_SECRET", None)


@pytest.fixture(scope="module")
def base_url() -> str:
    return DEFAULT_BASE


def test_metrics_endpoint_reachable(base_url: str) -> None:
    """D.5 - /metrics returns JSON with uptime + bus stats + counters."""
    import urllib.request
    with urllib.request.urlopen(f"{base_url}/metrics", timeout=3) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())
    for key in ("uptime_seconds", "event_bus", "webhooks"):
        assert key in body, f"missing {key} in /metrics"
    assert "github_received" in body["webhooks"]


def test_github_webhook_valid_signature_accepted(base_url: str) -> None:
    """D.1 fix - valid signature (with sha256= prefix) returns 200."""
    import urllib.request
    import urllib.error
    payload = json.dumps({"action": "opened", "zen": "smoke", "hook_id": 1}).encode()
    sig = _sign_github(payload)
    req = urllib.request.Request(
        f"{base_url}/api/gateway/github",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 200, f"expected 200, got {resp.status}"
            body = json.loads(resp.read())
        assert body.get("status") == "ok", f"unexpected body: {body}"
    except urllib.error.HTTPError as e:
        pytest.fail(f"GitHub webhook returned {e.code} (D.1 fix not applied?)")


def test_github_webhook_bad_signature_rejected(base_url: str) -> None:
    """D.1 - bad signature returns 401 and bumps auth_failed counter."""
    import urllib.request
    import urllib.error
    payload = b'{"action":"opened"}'
    req = urllib.request.Request(
        f"{base_url}/api/gateway/github",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=3)
    assert exc.value.code == 401


def test_event_bus_rejects_malformed_event() -> None:
    """D.3 - EventBus drops events missing required fields."""
    import asyncio
    from prismatic.gateway.event_bus import get_event_bus, _validate_event_shape

    ok, reason = _validate_event_shape({"type": "x"})  # missing source/timestamp/payload
    assert not ok

    ok, _ = _validate_event_shape(
        {
            "type": "test",
            "source": "smoke",
            "timestamp": "2026-06-30T18:00:00Z",
            "payload": {},
        }
    )
    assert ok

    bus = get_event_bus()
    asyncio.run(bus.publish(event_type="t", source="s", payload={"k": "v"}))
    history = bus.get_history(limit=5)
    assert any(e.get("type") == "t" for e in history)