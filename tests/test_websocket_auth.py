"""
tests/test_websocket_auth.py
============================

Verifies the /ws endpoint enforces per-client authentication (GRO-2058).

Three auth paths:
1. Bearer token (PRISMATIC_WS_TOKEN)
2. HMAC signature (PRISMATIC_WS_SECRET, with X-WS-Signature + X-WS-Timestamp)
3. No auth configured (dev mode) — accepts all (relying on IP allowlist)

Configuration:
- PRISMATIC_WS_ALLOWED_ORIGINS: comma-separated origin list
- PRISMATIC_WS_REPLAY_WINDOW: timestamp drift tolerance (default 60s)

Refs: GRO-2058 (per-client auth), Tier 7 hardening.
"""

import unittest
import os
import sys
import time
import hmac
import hashlib
from unittest.mock import MagicMock, AsyncMock


sys.modules["prismatic.providers.signals"] = MagicMock()
sys.modules["prismatic.credit_policy_engine"] = MagicMock()


def _hmac_sign(secret: str, timestamp: int) -> str:
    """Compute X-WS-Signature for /ws upgrade (same pattern as webhook)."""
    payload = f"GET\n/ws\n{timestamp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket for testing."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}
        self.accepted = False
        self.closed_code: int | None = None
        self.closed_reason: str | None = None
        self.sent: list = []

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed_code = code
        self.closed_reason = reason

    async def send_json(self, msg):
        self.sent.append(msg)

    async def receive_text(self):
        raise NotImplementedError("test stub")


class TestWebSocketAuth(unittest.TestCase):
    """Test /ws auth in isolation by calling the underlying function."""

    def setUp(self):
        # Reload module to pick up env changes
        import importlib
        import prismatic.gateway.server as srv_module
        importlib.reload(srv_module)
        self.srv = srv_module

    def tearDown(self):
        # Reset env for next test
        for var in (
            "PRISMATIC_WS_TOKEN",
            "PRISMATIC_WS_SECRET",
            "PRISMATIC_WS_ALLOWED_ORIGINS",
            "PRISMATIC_WS_REPLAY_WINDOW",
        ):
            os.environ.pop(var, None)

    def _run_handler(self, websocket):
        """Run the websocket handler and return the websocket after."""
        import asyncio
        return asyncio.run(self.srv.websocket_endpoint(websocket))

    def test_bearer_token_accepts_valid_token(self):
        """WS accepts a client presenting the correct Bearer token."""
        os.environ["PRISMATIC_WS_TOKEN"] = "test-ws-token-abc123"
        self.setUp()
        ws = FakeWebSocket(headers={"authorization": "Bearer test-ws-token-abc123"})
        self._run_handler(ws)
        self.assertTrue(ws.accepted, "WS should accept valid bearer token")
        self.assertIsNone(ws.closed_code)

    def test_bearer_token_rejects_invalid_token(self):
        """WS rejects a client with wrong Bearer token."""
        os.environ["PRISMATIC_WS_TOKEN"] = "test-ws-token-abc123"
        self.setUp()
        ws = FakeWebSocket(headers={"authorization": "Bearer wrong-token"})
        self._run_handler(ws)
        self.assertFalse(ws.accepted)
        self.assertEqual(ws.closed_code, 1008)
        self.assertEqual(ws.closed_reason, "unauthorized")

    def test_bearer_token_rejects_missing_token(self):
        """WS rejects a client with no Authorization header."""
        os.environ["PRISMATIC_WS_TOKEN"] = "test-ws-token-abc123"
        self.setUp()
        ws = FakeWebSocket(headers={})
        self._run_handler(ws)
        self.assertFalse(ws.accepted)
        self.assertEqual(ws.closed_code, 1008)

    def test_hmac_signature_accepts_valid(self):
        """WS accepts valid HMAC signature with fresh timestamp."""
        os.environ["PRISMATIC_WS_SECRET"] = "test-ws-secret"
        self.setUp()
        ts = int(time.time())
        sig = _hmac_sign("test-ws-secret", ts)
        ws = FakeWebSocket(headers={
            "x-ws-signature": sig,
            "x-ws-timestamp": str(ts),
        })
        self._run_handler(ws)
        self.assertTrue(ws.accepted)
        self.assertIsNone(ws.closed_code)

    def test_hmac_signature_rejects_stale_timestamp(self):
        """WS rejects HMAC signature with timestamp outside replay window."""
        os.environ["PRISMATIC_WS_SECRET"] = "test-ws-secret"
        os.environ["PRISMATIC_WS_REPLAY_WINDOW"] = "60"
        self.setUp()
        ts = int(time.time()) - 600  # 10 min old
        sig = _hmac_sign("test-ws-secret", ts)
        ws = FakeWebSocket(headers={
            "x-ws-signature": sig,
            "x-ws-timestamp": str(ts),
        })
        self._run_handler(ws)
        self.assertFalse(ws.accepted)

    def test_hmac_signature_rejects_future_timestamp(self):
        """WS rejects HMAC signature from the future."""
        os.environ["PRISMATIC_WS_SECRET"] = "test-ws-secret"
        self.setUp()
        ts = int(time.time()) + 600  # 10 min in future
        sig = _hmac_sign("test-ws-secret", ts)
        ws = FakeWebSocket(headers={
            "x-ws-signature": sig,
            "x-ws-timestamp": str(ts),
        })
        self._run_handler(ws)
        self.assertFalse(ws.accepted)

    def test_hmac_signature_rejects_wrong_secret(self):
        """WS rejects HMAC signed with wrong secret."""
        os.environ["PRISMATIC_WS_SECRET"] = "test-ws-secret"
        self.setUp()
        ts = int(time.time())
        sig = _hmac_sign("wrong-secret", ts)
        ws = FakeWebSocket(headers={
            "x-ws-signature": sig,
            "x-ws-timestamp": str(ts),
        })
        self._run_handler(ws)
        self.assertFalse(ws.accepted)

    def test_hmac_with_non_numeric_timestamp_rejected(self):
        """WS rejects HMAC with malformed timestamp."""
        os.environ["PRISMATIC_WS_SECRET"] = "test-ws-secret"
        self.setUp()
        ws = FakeWebSocket(headers={
            "x-ws-signature": "abc123",
            "x-ws-timestamp": "not-a-number",
        })
        self._run_handler(ws)
        self.assertFalse(ws.accepted)

    def test_origin_allowlist_rejects_unknown_origin(self):
        """WS rejects requests from origins not in allowlist."""
        os.environ["PRISMATIC_WS_TOKEN"] = "test-ws-token"
        os.environ["PRISMATIC_WS_ALLOWED_ORIGINS"] = "https://app.example.com,https://other.example.com"
        self.setUp()
        ws = FakeWebSocket(headers={
            "authorization": "Bearer test-ws-token",
            "origin": "https://evil.example.com",
        })
        self._run_handler(ws)
        self.assertFalse(ws.accepted)
        self.assertEqual(ws.closed_code, 1008)
        self.assertEqual(ws.closed_reason, "origin not allowed")

    def test_origin_allowlist_accepts_allowed_origin(self):
        """WS accepts requests from origin in allowlist."""
        os.environ["PRISMATIC_WS_TOKEN"] = "test-ws-token"
        os.environ["PRISMATIC_WS_ALLOWED_ORIGINS"] = "https://app.example.com"
        self.setUp()
        ws = FakeWebSocket(headers={
            "authorization": "Bearer test-ws-token",
            "origin": "https://app.example.com",
        })
        self._run_handler(ws)
        self.assertTrue(ws.accepted)

    def test_no_auth_configured_accepts_all(self):
        """With no token/secret configured, WS accepts (dev mode + IP allowlist)."""
        # No PRISMATIC_WS_TOKEN or PRISMATIC_WS_SECRET
        self.setUp()
        ws = FakeWebSocket(headers={})
        self._run_handler(ws)
        self.assertTrue(ws.accepted)
        self.assertIsNone(ws.closed_code)


if __name__ == "__main__":
    unittest.main()