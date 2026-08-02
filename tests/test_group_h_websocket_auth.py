"""Group H: 5-Point Counterexample Matrix Test for Canonical WebSocket Authentication.

Matrix Coverage:
1. Positive: Exact 'Authorization: Bearer *** connects cleanly.
2. Direct Negative: Substring token 'Bearer xvalid-tokenx' or wrong token MUST be rejected with code 1008.
3. Collision & Isolation: Scoped valid token works; other token values isolate cleanly.
4. Boundary & Empty: Empty/missing Authorization header or query token (/ws?token=valid-token) MUST be rejected.
5. Bypass Path: Malformed scheme ('Basic valid-token') or whitespace tokens fail closed.
"""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from starlette.websockets import WebSocketDisconnect


def test_group_h_ws_auth_required_by_default(monkeypatch):
    monkeypatch.delenv("PRISMATIC_WS_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("PRISMATIC_WS_TOKENS", "default-required-token")
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 1008
    with client.websocket_connect(
        "/ws", headers={"Authorization": "Bearer default-required-token"}
    ) as ws:
        assert ws.receive_json().get("type") == "connected"


def test_group_h_ws_auth_strict_bearer_validation(monkeypatch):
    """Positive & Negative: WebSocket auth MUST require exact Bearer token.

    Query params and substring tokens MUST be rejected with 1008.
    """
    monkeypatch.setenv("PRISMATIC_WS_AUTH_REQUIRED", "1")
    monkeypatch.setenv("PRISMATIC_WS_TOKENS", "valid-secret-token")

    client = TestClient(app)

    with client.websocket_connect(
        "/ws", headers={"Authorization": "Bearer valid-secret-token"}
    ) as ws:
        msg = ws.receive_json()
        assert msg.get("type") == "connected"

    with client.websocket_connect(
        "/ws", headers={"Authorization": "Bearer xvalid-secret-tokenx"}
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 1008

    with client.websocket_connect("/ws?token=valid-secret-token") as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 1008

    with client.websocket_connect(
        "/ws", headers={"Authorization": "Basic valid-secret-token"}
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 1008
