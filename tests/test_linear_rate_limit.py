from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prismatic.linear_rate_limit import (
    LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
    LinearRateLimitCircuitOpen,
    ensure_linear_circuit_closed,
    get_linear_rate_limit_snapshot,
    parse_rate_limit_error,
    record_linear_response_headers,
    trip_linear_circuit_from_error,
)


def future_reset() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()


def test_records_headers_and_trips_when_remaining_below_floor(tmp_path: Path):
    state = tmp_path / "linear_state.json"

    snapshot = record_linear_response_headers(
        {
            "x-ratelimit-requests-limit": "2500",
            "x-ratelimit-requests-remaining": "15",
            "x-ratelimit-requests-reset": future_reset(),
        },
        source="test.low_remaining",
        state_path=state,
    )

    assert snapshot["marker"] == LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER
    assert snapshot["cooldown_active"] is True
    assert snapshot["remaining"] == 15
    assert snapshot["limit"] == 2500
    assert "remaining 15" in snapshot["reason"]
    with pytest.raises(LinearRateLimitCircuitOpen):
        ensure_linear_circuit_closed(source="test.blocked", state_path=state)


def test_clear_and_high_remaining_allows_calls(tmp_path: Path):
    state = tmp_path / "linear_state.json"
    record_linear_response_headers(
        {
            "x-ratelimit-requests-limit": "2500",
            "x-ratelimit-requests-remaining": "2400",
        },
        source="test.high_remaining",
        state_path=state,
    )

    ensure_linear_circuit_closed(source="test.allowed", state_path=state)
    snapshot = get_linear_rate_limit_snapshot(state_path=state)

    assert snapshot["cooldown_active"] is False
    assert snapshot["remaining"] == 2400
    assert snapshot["last_call_source"] == "test.allowed"


def test_ratelimited_graphql_error_trips_until_reset(tmp_path: Path):
    state = tmp_path / "linear_state.json"
    reset_at = future_reset()
    payload = {
        "errors": [
            {
                "message": "Rate limit exceeded. Only 2500 requests are allowed per 1 hour.",
                "extensions": {
                    "code": "RATELIMITED",
                    "meta": {"rateLimitResult": {"resetAt": reset_at, "remaining": 0, "limit": 2500}},
                },
            }
        ]
    }

    reason, parsed_reset, remaining, limit = parse_rate_limit_error(payload)
    tripped = trip_linear_circuit_from_error(payload, source="test.graphql", state_path=state)

    assert reason and "Rate limit exceeded" in reason
    assert parsed_reset == reset_at
    assert remaining is None or remaining == 0
    assert limit == 2500
    assert tripped is not None
    assert tripped["cooldown_active"] is True
    assert tripped["cooldown_until"] == reset_at


def test_non_rate_limit_error_does_not_trip(tmp_path: Path):
    state = tmp_path / "linear_state.json"

    tripped = trip_linear_circuit_from_error(
        {"errors": [{"message": "Some schema validation error", "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]},
        source="test.validation",
        state_path=state,
    )

    assert tripped is None
    assert get_linear_rate_limit_snapshot(state_path=state)["cooldown_active"] is False
