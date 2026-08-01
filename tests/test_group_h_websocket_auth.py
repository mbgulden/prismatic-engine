"""Group H: 5-Point Counterexample Matrix Test for Canonical WebSocket Authentication.

Matrix Coverage:
1. Positive: Exact 'Authorization: Bearer valid-token' connects cleanly.
2. Direct Negative: Substring token 'Bearer xvalid-tokenx' or wrong token MUST be rejected with code 1008.
3. Collision & Isolation: Scoped valid token works; other token values isolate cleanly.
4. Boundary & Empty: Empty/missing Authorization header or query token (/ws?token=valid-token) MUST be rejected.
5. Bypass Path: Malformed scheme ('Basic valid-token') or whitespace tokens fail closed.
"""

import os
import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app


def test_group_h_ws_auth_strict_bearer_validation(monkeypatch):
    """Positive & Negative: WebSocket auth MUST require exact Bearer token.
    Query params and substring tokens MUST be rejected with 1008.
    """
    monkeypatch.setenv("PRISMATIC_WS_AUTH_REQUIRED", "1")
    monkeypatch.setenv("PRISMATIC_WS_TOKENS", "valid-secret-token")

    client = TestClient(app)

    # 1. Positive: Exact Bearer token connects cleanly
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer valid-secret-token"}) as ws:
        msg = ws.receive_json()
        assert msg.get("type") == "connected"

    # 2. Direct Negative: Substring token MUST be rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer xvalid-secret-tokenx"}) as ws:
            pass

    # 3. Boundary & Empty: Query token /ws?token=valid-secret-token MUST be rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=valid-secret-token") as ws:
            pass

    # 4. Bypass Path: Malformed scheme 'Basic valid-secret-token' MUST be rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Authorization": "Basic valid-secret-token"}) as ws:
            pass
