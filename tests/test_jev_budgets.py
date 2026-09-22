"""Tests for per-call cost and latency budgets."""

import json

import pytest

from prismatic.jev import DecisionError
from prismatic.jev.backends import (
    BackendResult,
    BudgetExceededError,
    CallParams,
    OpenRouterDecisionsBackend,
    TypeSafeDecisionsBackend,
)
from prismatic.jev.client import DecisionClient
from prismatic.jev.config import JevConfig
from prismatic.jev.errors import TransportError
from prismatic.jev.questions import Noul, NoulAnswer
from prismatic.jev.resilience import reset_breakers_for_tests, reset_bulkheads_for_tests


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    yield
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()


class FakeBackend:
    backend_name = "fake"
    model = "fake-model"

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def decide(self, state, questions, *, call=None):
        self.calls += 1
        raise self._exc


def _client_with_trace(backend, tmp_path):
    cfg = JevConfig(trace_path=str(tmp_path / "jev.jsonl"))
    return DecisionClient(backend=backend, config=cfg)


# -- validation -------------------------------------------------------------


def test_negative_cost_budget_rejected():
    client = DecisionClient(backend=FakeBackend(DecisionError("x")), config=JevConfig())
    with pytest.raises(DecisionError, match="cost_budget"):
        client.decide({"a": 1}, [Noul("u", "p")], cost_budget=-1.0)


@pytest.mark.parametrize("value", [0.0, -5.0])
def test_nonpositive_latency_budget_rejected(value):
    client = DecisionClient(backend=FakeBackend(DecisionError("x")), config=JevConfig())
    with pytest.raises(DecisionError, match="latency_budget_ms"):
        client.decide({"a": 1}, [Noul("u", "p")], latency_budget_ms=value)


def test_zero_cost_budget_allowed():
    # zero budget + fallback path: the budget gate is about network calls
    client = DecisionClient(backend=FakeBackend(DecisionError("x")), config=JevConfig())
    with pytest.raises(DecisionError):
        client.decide({"a": 1}, [Noul("u", "p")], cost_budget=0.0)


# -- pre-call cost gate ------------------------------------------------------


def test_cost_budget_blocks_overpriced_call():
    backend = OpenRouterDecisionsBackend()
    with pytest.raises(BudgetExceededError, match="estimated max cost"):
        backend._check_cost_budget(b"x" * 100_000, CallParams(cost_budget=0.000001))


def test_cost_budget_allows_cheap_call():
    backend = OpenRouterDecisionsBackend()
    backend._check_cost_budget(b'{"a": 1}', CallParams(cost_budget=100.0))  # no raise


def test_unknown_price_fails_closed_with_budget():
    backend = TypeSafeDecisionsBackend()
    with pytest.raises(DecisionError, match="cannot verify cost_budget"):
        backend._check_cost_budget(b'{"a": 1}', CallParams(cost_budget=100.0))


def test_no_budget_no_gate():
    backend = TypeSafeDecisionsBackend()
    backend._check_cost_budget(b"x" * 10_000_000, CallParams())  # no raise


# -- post-hoc accounting -------------------------------------------------------


def test_post_hoc_overrun_logs_but_returns_result():
    class CostlyBackend:
        backend_name = "openrouter"
        model = "m"

        def decide(self, state, questions, *, call=None):
            return BackendResult(
                backend="openrouter",
                answers={"u": NoulAnswer(probability=0.7)},
                latency_ms=5.0,
                tokens_in=1_000_000,  # $0.042 at openrouter pricing
                cost_usd=0.042,
            )

    client = DecisionClient(backend=CostlyBackend(), config=JevConfig())
    result = client.decide({"a": 1}, [Noul("u", "p")], cost_budget=0.01)
    assert result.cost_usd == pytest.approx(0.042)
    assert result.answers["u"].probability == 0.7


# -- budget failure routes through on_error + trace ------------------------------


def test_budget_exceeded_routes_to_deterministic_and_traces(tmp_path):
    backend = FakeBackend(BudgetExceededError("latency budget exceeded before attempt"))
    client = _client_with_trace(backend, tmp_path)
    result = client.decide(
        {"a": 1},
        [Noul("u", "p")],
        on_error="deterministic",
        defaults={"u": 0.5},
    )
    assert result.deterministic is True
    lines = (tmp_path / "jev.jsonl").read_text().splitlines()
    assert len(lines) == 2  # failure trace + deterministic trace
    failure = json.loads(lines[0])
    assert failure["flags"]["budget_exceeded"] is True
    assert "BudgetExceededError" in failure["error"]


def test_transient_retry_stops_at_max_attempts(monkeypatch):
    backend = OpenRouterDecisionsBackend()
    calls = {"n": 0}

    def _boom(body, *, timeout_s, idempotency_key):
        calls["n"] += 1
        raise TransportError("down", status=503, transient=True, trip_breaker=True)

    monkeypatch.setattr(backend, "_post", _boom)
    cfg = JevConfig(max_attempts=2, backoff_base_s=0.0, backoff_cap_s=0.0)
    backend._config = cfg
    with pytest.raises(TransportError):
        backend.decide({"a": 1}, [Noul("u", "p")])
    assert calls["n"] == 2


def test_permanent_error_not_retried(monkeypatch):
    backend = OpenRouterDecisionsBackend()
    calls = {"n": 0}

    def _boom(body, *, timeout_s, idempotency_key):
        calls["n"] += 1
        raise TransportError("bad request", status=400, transient=False)

    monkeypatch.setattr(backend, "_post", _boom)
    with pytest.raises(TransportError):
        backend.decide({"a": 1}, [Noul("u", "p")])
    assert calls["n"] == 1
