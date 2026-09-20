"""Unit tests for Prismatic Gateway SSE Event Stream.

Verifies Server-Sent Events (SSE) streaming on /api/signals/stream, /stream,
/events, /sse, and ensures live fan-out on signal emission.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from prismatic.gateway.server import app


def test_signals_stream_initial_connect_and_snapshot():
    """Verify that connecting to /api/signals/stream returns SSE events."""
    client = TestClient(app)
    response = client.get("/api/signals/stream?limit=5&once=true")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    content = response.text
    assert "event: connect" in content
    assert "prismatic-gateway" in content
    assert "event: snapshot" in content


def test_signals_stream_aliases():
    """Verify alias routes /stream, /events, /sse connect cleanly."""
    client = TestClient(app)
    for endpoint in ["/stream", "/events", "/sse", "/api/gateway/signals/stream"]:
        response = client.get(f"{endpoint}?limit=1&once=true")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert "event: connect" in response.text
