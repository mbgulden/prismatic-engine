"""Tests for maybe_decide(): the zero-network local pre-check."""

import os

import pytest

from prismatic.jev import DecisionError
from prismatic.jev.backends import BackendResult
from prismatic.jev.client import DecisionClient
from prismatic.jev.config import JevConfig
from prismatic.jev.errors import TransportError
from prismatic.jev.gates import CallSiteGate
from prismatic.jev.questions import Noul, NoulAnswer
from prismatic.jev.resilience import (
    classify_http_error,
    get_breaker,
    reset_breakers_for_tests,
    reset_bulkheads_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()
    for name in ("SWARMJEV_ENABLED", "SWARMJEV_CALLSITE_TRIAGE_ENABLED"):
        os.environ.pop(name, None)
    yield
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()
    for name in ("SWARMJEV_ENABLED", "SWARMJEV_CALLSITE_TRIAGE_ENABLED"):
        os.environ.pop(name, None)


class FakeBackend:
    backend_name = "fake-or"
    model = "fake-model"

    def __init__(self, answers):
        self._answers = answers
        self.calls = 0

    def decide(self, state, questions, *, call=None):
        self.calls += 1
        return BackendResult(backend="fake-or", answers=self._answers, latency_ms=1.0)


def _client(backend):
    return DecisionClient(backend=backend, config=JevConfig())


def test_closed_gate_short_circuits_without_network(monkeypatch):
    backend = FakeBackend({"u": NoulAnswer(probability=0.7)})
    client = _client(backend)
    gate = CallSiteGate("triage")  # env unset → closed
    pre = client.maybe_decide({"a": 1}, [Noul("u", "p")], gate=gate)
    assert pre.proceed is False
    assert pre.result is None
    assert "triage" in pre.reason
    assert backend.calls == 0


def test_open_gate_proceeds(monkeypatch):
    monkeypatch.setenv("SWARMJEV_ENABLED", "1")
    monkeypatch.setenv("SWARMJEV_CALLSITE_TRIAGE_ENABLED", "true")
    backend = FakeBackend({"u": NoulAnswer(probability=0.7)})
    client = _client(backend)
    pre = client.maybe_decide({"a": 1}, [Noul("u", "p")], gate=CallSiteGate("triage"))
    assert pre.proceed is True
    assert pre.result is not None
    assert pre.result.answers["u"].probability == 0.7
    assert backend.calls == 1


def test_open_breaker_short_circuits_without_network():
    backend = FakeBackend({"u": NoulAnswer(probability=0.7)})
    breaker = get_breaker(
        backend.backend_name, backend.model, failure_threshold=1, reset_timeout_s=60.0
    )
    breaker.record_transport_error(classify_http_error(500, {}))
    assert breaker.state == "open"
    client = _client(backend)
    pre = client.maybe_decide({"a": 1}, [Noul("u", "p")])
    assert pre.proceed is False
    assert "circuit breaker open" in pre.reason
    assert backend.calls == 0


def test_no_gate_no_breaker_proceeds():
    backend = FakeBackend({"u": NoulAnswer(probability=0.7)})
    pre = _client(backend).maybe_decide({"a": 1}, [Noul("u", "p")])
    assert pre.proceed is True
    assert backend.calls == 1


def test_maybe_decide_validates_like_decide():
    backend = FakeBackend({})
    client = _client(backend)
    with pytest.raises(DecisionError, match="at least one question"):
        client.maybe_decide({"a": 1}, [])
    with pytest.raises(DecisionError, match="unique"):
        client.maybe_decide({"a": 1}, [Noul("u", "p"), Noul("u", "p")])


def test_maybe_decide_backend_failure_still_raises():
    class BoomBackend:
        backend_name = "boom"
        model = "m"

        def decide(self, state, questions, *, call=None):
            raise TransportError("down", transient=False)

    pre_client = _client(BoomBackend())
    with pytest.raises(TransportError):
        pre_client.maybe_decide({"a": 1}, [Noul("u", "p")])
