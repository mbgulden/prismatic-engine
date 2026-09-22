"""Security tests: credentials never leak into reprs, errors, traces, or keys.

The primitive handles API keys in-process. These tests assert the negative:
no key material appears in repr output, exception text, audit dicts, trace
records, memo keys, or the repair payload — the places a bug would surface it.
"""

import json
import urllib.error

import pytest

from prismatic.jev import DecisionError
from prismatic.jev.backends import OpenRouterDecisionsBackend
from prismatic.jev.client import DecisionClient
from prismatic.jev.config import JevConfig
from prismatic.jev.memo import memo_key
from prismatic.jev.questions import Noul, NoulAnswer
from prismatic.jev.redact import serialize_state, Untrusted
from prismatic.jev.resilience import reset_breakers_for_tests, reset_bulkheads_for_tests

SECRET = "sk-test-secret-abcdef123456"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()
    yield
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()


def test_backend_repr_hides_key():
    backend = OpenRouterDecisionsBackend()
    assert SECRET not in repr(backend)


def test_missing_credential_error_names_var_not_value(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(DecisionError) as excinfo:
        OpenRouterDecisionsBackend()
    assert SECRET not in str(excinfo.value)
    assert "OPENROUTER_API_KEY" in str(excinfo.value)


def test_transport_error_text_hides_key(monkeypatch):
    backend = OpenRouterDecisionsBackend()

    def _boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    with pytest.raises(DecisionError) as excinfo:
        backend.decide({"a": 1}, [Noul("u", "prompt")])
    assert SECRET not in str(excinfo.value)


def test_state_containing_secret_is_redacted_before_wire():
    wire, _ = serialize_state(
        {"config": Untrusted(f"OPENROUTER_KEY={SECRET}"), "note": "ok"}, "nonce1"
    )
    assert SECRET not in json.dumps(wire)


def test_memo_key_is_hash_only():
    key = memo_key(
        schema_version="1.0",
        backend_name="openrouter",
        model="m",
        questions_wire={},
        state_canonical_json=f'{{"k": "{SECRET}"}}',
    )
    assert SECRET not in key


def test_trace_has_no_key_after_failed_decision(monkeypatch, tmp_path):
    backend = OpenRouterDecisionsBackend()

    def _boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    cfg = JevConfig(trace_path=str(tmp_path / "jev.jsonl"))
    client = DecisionClient(backend=backend, config=cfg)
    with pytest.raises(DecisionError):
        client.decide({"k": SECRET}, [Noul("u", "prompt")])
    trace_line = (tmp_path / "jev.jsonl").read_text()
    assert SECRET not in trace_line


def test_audit_dict_has_no_state_values_or_keys():
    from prismatic.jev.backends import BackendResult

    class FakeBackend:
        backend_name = "fake"
        model = "m"

        def decide(self, state, questions, *, call=None):
            return BackendResult(
                backend="fake",
                answers={"u": NoulAnswer(probability=0.7)},
                latency_ms=1.0,
            )

    client = DecisionClient(backend=FakeBackend(), config=JevConfig())
    result = client.decide({"password": SECRET}, [Noul("u", "prompt")])
    audit = json.dumps(result.to_audit_dict(), sort_keys=True)
    assert SECRET not in audit


def test_repair_payload_has_no_key(monkeypatch):
    """Schema-repair resends go through the same redacted payload path."""
    backend = OpenRouterDecisionsBackend()
    seen_bodies = []
    call_count = {"n": 0}

    class FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._payload

    def _fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        seen_bodies.append(req.data)
        if call_count["n"] == 1:
            # schema violation: answers object missing the question
            return FakeResp(b'{"schema_version": "1.0", "answers": {}}')
        return FakeResp(
            b'{"schema_version": "1.0", "answers": {"u": {"probability": 0.7}}}'
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    result = backend.decide(
        {"k": f"token={SECRET}"},
        [Noul("u", "prompt")],
    )
    assert result.repair_attempts == 1
    assert result.answers["u"].probability == 0.7
    for body in seen_bodies:
        assert SECRET.encode() not in body
