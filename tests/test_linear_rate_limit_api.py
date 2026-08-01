from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.linear_rate_limit import (
    LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
    LinearRateLimitState,
)


def future_reset() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()


def test_linear_rate_limit_api_exposes_cooldown(monkeypatch, tmp_path: Path):
    state = tmp_path / "linear_state.json"
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(state))
    LinearRateLimitState(state).trip(reason="fixture low request budget", reset_at=future_reset(), source="test.api")
    client = TestClient(server.app)

    res = client.get("/api/gateway/linear/rate-limit")

    assert res.status_code == 200
    body = res.json()
    assert body["marker"] == LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER
    assert body["linear_rate_limit"]["cooldown_active"] is True
    assert body["linear_rate_limit"]["reason"] == "fixture low request budget"


def test_dispatcher_status_embeds_linear_rate_limit_state(monkeypatch, tmp_path: Path):
    state = tmp_path / "linear_state.json"
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(state))
    LinearRateLimitState(state).trip(reason="fixture cooldown", reset_at=future_reset(), source="test.status")
    client = TestClient(server.app)

    res = client.get("/api/gateway/dispatcher/status")

    assert res.status_code == 200
    body = res.json()
    assert body["linear_rate_limit"]["marker"] == LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER
    assert body["linear_rate_limit"]["cooldown_active"] is True
    assert body["linear_rate_limit"]["last_call_source"] == "test.status"
