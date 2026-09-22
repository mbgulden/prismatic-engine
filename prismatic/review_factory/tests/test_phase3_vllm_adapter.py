"""Integration tests for the RF-3 vLLM adapter path.

All tests run against a stubbed ``http.server`` thread that mimics the
OpenAI-compatible ``/v1`` surface (``GET /v1/models``,
``POST /v1/chat/completions``). No live endpoint is contacted and no real
credentials exist anywhere in this file — every key is a fake canary
string, and canary test 5 proves the canary never reaches logs or
exception text.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from prismatic.providers.ollama import OllamaClient
from prismatic.providers.vllm import VLLMClient
from prismatic.review_factory.llm_deep_review import (
    LLMDeepReviewAdapter,
    LLMReviewConfig,
    PROVIDER_REGISTRY,
    resolve_llm_outcome,
)

_ENV_MAX_DIFF_FULL = "PRISMATIC_REVIEW_LLM_MAX_DIFF_FULL"

# ── Stubbed vLLM server ────────────────────────────────────────────────


class _StubState:
    """Mutable behavior switches for the stub handler (reset per test)."""

    mode = "ok"  # "ok" | "unauthorized" | "garbage" | "slow"
    auth_headers: list = []
    chat_payload: dict | None = None


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, status: int, body: str, content_type: str = "application/json"):
        raw = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client already gave up (timeout tests)

    def _record_auth(self):
        _StubState.auth_headers.append(self.headers.get("Authorization"))

    def do_GET(self):
        self._record_auth()
        if self.path == "/v1/models":
            if _StubState.mode == "unauthorized":
                self._send(401, '{"error": "unauthorized"}')
                return
            self._send(200, json.dumps({"data": [{"id": "qwen-test-ned"}]}))
        else:
            self._send(404, '{"error": "not found"}')

    def do_POST(self):
        self._record_auth()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path != "/v1/chat/completions":
            self._send(404, '{"error": "not found"}')
            return
        mode = _StubState.mode
        if mode == "unauthorized":
            self._send(401, '{"error": "unauthorized"}')
            return
        if mode == "slow":
            time.sleep(5)  # longer than any test timeout
            self._send(200, json.dumps({"choices": [{"message": {"content": "{}"}}]}))
            return
        if mode == "garbage":
            self._send(200, "this is not valid json {{{", "text/plain")
            return
        content = json.dumps(_StubState.chat_payload or {})
        self._send(
            200,
            json.dumps({"choices": [{"message": {"content": content}}]}),
        )


@pytest.fixture()
def stub_server():
    _StubState.mode = "ok"
    _StubState.auth_headers = []
    _StubState.chat_payload = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


# ── Helpers ────────────────────────────────────────────────────────────


def _vllm_config(base_url: str, **overrides) -> LLMReviewConfig:
    cfg = dict(
        enabled=True,
        endpoint=f"{base_url}/v1",
        protocol="vllm",
        model_full="qwen-test-ned",
        model_bounded="",
        timeout_seconds=30.0,
        max_diff_chars=1000,
        max_diff_full_chars=600_000,
        max_rereviews=2,
        temperature=0.0,
    )
    cfg.update(overrides)
    return LLMReviewConfig(**cfg)


def _vllm_client(base_url: str, api_key: str = "test-key-not-secret") -> VLLMClient:
    return VLLMClient(base_url=f"{base_url}/v1", api_key=api_key, timeout=10.0)


def _job():
    return SimpleNamespace(
        repository="prismatic-engine",
        candidate_commit="abc123",
        candidate_tree="tree9",
    )


def _clean_payload() -> dict:
    return {
        "findings": [],
        "verdict": "clean",
        "confidence": 0.95,
        "rationale": "the stubbed model sees nothing wrong",
    }


# ── The 8 integration tests ────────────────────────────────────────────


def test_end_to_end_adapter_review(stub_server):
    """vLLM protocol path through the adapter: models + chat -> validated result."""
    _StubState.chat_payload = {
        "findings": [
            {
                "severity": "medium",
                "file": "prismatic/x.py",
                "lines": "12-18",
                "category": "security",
                "explanation": "test finding from the stubbed model",
            }
        ],
        "verdict": "repair_required",
        "confidence": 0.8,
        "rationale": "test rationale",
    }
    adapter = LLMDeepReviewAdapter(
        _vllm_config(stub_server), client=_vllm_client(stub_server)
    )
    result = adapter.review(
        _job(),
        diff_text="--- a/x.py\n+++ b/x.py\n@@ small diff\n",
        deterministic_verdict="clean",
        deterministic_findings=[],
    )
    assert result is not None
    assert result.verdict == "repair_required"
    assert len(result.findings) == 1
    assert result.findings[0].file == "prismatic/x.py"
    assert result.model == "qwen-test-ned"


def test_401_is_fail_closed(stub_server):
    """Auth failure on the health gate skips the stage; verdict stands."""
    _StubState.mode = "unauthorized"
    adapter = LLMDeepReviewAdapter(
        _vllm_config(stub_server), client=_vllm_client(stub_server)
    )
    ok, _reason = adapter.gates_pass()
    assert ok is False
    assert (
        adapter.review(
            _job(),
            diff_text="diff",
            deterministic_verdict="clean",
            deterministic_findings=[],
        )
        is None
    )


def test_timeout_is_fail_closed(stub_server):
    """A chat call that exceeds the per-call timeout returns None, no hang."""
    _StubState.mode = "slow"
    _StubState.chat_payload = _clean_payload()
    adapter = LLMDeepReviewAdapter(
        _vllm_config(stub_server, timeout_seconds=1.0),
        client=_vllm_client(stub_server),
    )
    started = time.monotonic()
    result = adapter.review(
        _job(),
        diff_text="diff",
        deterministic_verdict="clean",
        deterministic_findings=[],
    )
    elapsed = time.monotonic() - started
    assert result is None
    assert elapsed < 10, f"review() took too long: {elapsed:.1f}s"


def test_garbage_json_is_fail_closed(stub_server):
    """Non-JSON chat output fails schema validation -> stage skipped."""
    _StubState.mode = "garbage"
    adapter = LLMDeepReviewAdapter(
        _vllm_config(stub_server), client=_vllm_client(stub_server)
    )
    assert (
        adapter.review(
            _job(),
            diff_text="diff",
            deterministic_verdict="clean",
            deterministic_findings=[],
        )
        is None
    )


def test_secret_never_logged(stub_server, monkeypatch, caplog):
    """The API key is sent (server-side header) but never lands in logs/errors."""
    canary = "CANARY-VLLM-KEY-7d3f9a2c"
    monkeypatch.setenv("VLLM_API_KEY", canary)
    monkeypatch.delenv("VLLM_NED_API_KEY", raising=False)
    raised: list[str] = []
    with caplog.at_level(logging.WARNING):
        # 1. connection refused
        dead = VLLMClient(base_url="http://127.0.0.1:1/v1", timeout=1.0)
        try:
            dead.check_health()
        except Exception as exc:  # noqa: BLE001 - collecting, not handling
            raised.append(str(exc))
        # 2. 401 from the stub
        _StubState.mode = "unauthorized"
        unauth = VLLMClient(base_url=f"{stub_server}/v1", timeout=5.0)
        try:
            unauth.check_health()
        except Exception as exc:  # noqa: BLE001
            raised.append(str(exc))
        # 3. garbage JSON from the stub
        _StubState.mode = "garbage"
        try:
            unauth.chat(
                "qwen-test-ned",
                [{"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        except Exception as exc:  # noqa: BLE001
            raised.append(str(exc))
    haystack = caplog.text + "\n".join(raised)
    assert canary not in haystack, "API key leaked into logs or exception text"
    # ...but it WAS actually sent to the server, in the header only.
    assert _StubState.auth_headers, "expected the stub to see requests"
    assert _StubState.auth_headers[-1] == f"Bearer {canary}"


def test_max_diff_full_parsing(monkeypatch):
    """PRISMATIC_REVIEW_LLM_MAX_DIFF_FULL: default 600k, custom honored, garbage->default."""
    monkeypatch.delenv(_ENV_MAX_DIFF_FULL, raising=False)
    assert LLMReviewConfig.from_env().max_diff_full_chars == 600_000
    monkeypatch.setenv(_ENV_MAX_DIFF_FULL, "12345")
    assert LLMReviewConfig.from_env().max_diff_full_chars == 12_345
    monkeypatch.setenv(_ENV_MAX_DIFF_FULL, "not-a-number")
    assert LLMReviewConfig.from_env().max_diff_full_chars == 600_000


def test_provider_registry(monkeypatch):
    """Registry maps protocols to client classes; unknown falls back to ollama."""
    assert PROVIDER_REGISTRY["vllm"] == ("prismatic.providers.vllm", "VLLMClient")
    assert PROVIDER_REGISTRY["ollama"] == (
        "prismatic.providers.ollama",
        "OllamaClient",
    )
    monkeypatch.setenv("VLLM_API_KEY", "registry-test-key")
    monkeypatch.delenv("VLLM_NED_API_KEY", raising=False)
    vllm_adapter = LLMDeepReviewAdapter(
        LLMReviewConfig(protocol="vllm", endpoint="http://127.0.0.1:9/v1")
    )
    assert isinstance(vllm_adapter.client, VLLMClient)
    ollama_adapter = LLMDeepReviewAdapter(LLMReviewConfig(protocol="ollama"))
    assert isinstance(ollama_adapter.client, OllamaClient)
    unknown_adapter = LLMDeepReviewAdapter(LLMReviewConfig(protocol="bogus"))
    assert isinstance(unknown_adapter.client, OllamaClient)


def test_no_downgrade_through_adapter(stub_server):
    """LLM says 'clean' on a deterministic REJECTED -> repair packet, never a downgrade.

    The deterministic verdict string is never mutated; the only thing the
    LLM output can produce here is a repair work order. There is no
    auto-merge path: resolve_llm_outcome is a pure function of the action
    enum (packet/escalate/advisory/none) and cannot rewrite the verdict.
    """
    _StubState.chat_payload = _clean_payload()  # the model insists: "clean"
    adapter = LLMDeepReviewAdapter(
        _vllm_config(stub_server), client=_vllm_client(stub_server)
    )
    det_verdict = "rejected"
    llm_result = adapter.review(
        _job(),
        diff_text="some diff",
        deterministic_verdict=det_verdict,
        deterministic_findings=[],
    )
    assert llm_result is not None
    assert llm_result.verdict == "clean"  # the LLM output itself is not rewritten
    outcome = resolve_llm_outcome(det_verdict, llm_result)
    assert outcome == "packet"  # repair work order, never a downgrade
    assert det_verdict == "rejected"  # deterministic verdict untouched
    assert outcome not in ("advisory", "escalate", "none")
