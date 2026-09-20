"""Tests for Phase 3 vLLM protocol support.

Covers: the VLLMClient (OpenAI-compatible /v1) health / model /
chat behavior against mocked HTTP, protocol selection in
LLMReviewConfig / LLMDeepReviewAdapter, and the failure-gate
guarantees (401, timeout, invalid JSON -> stage skipped, deterministic
verdict stands).

No real credentials anywhere: all keys in fixtures are fake strings,
and no live endpoint is contacted.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from prismatic.providers.vllm import VLLMClient
from prismatic.review_factory.llm_deep_review import (
    LLMDeepReviewAdapter,
    LLMReviewConfig,
)


# ── HTTP mock helpers ──────────────────────────────────────────────────


class _FakeHTTPResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_urlopen(monkeypatch, handler):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", handler)


def _ok_models(monkeypatch):
    def handler(req, timeout=None):
        assert req.full_url.endswith("/v1/models")
        return _FakeHTTPResponse(
            {"data": [{"id": "qwen3.8-27b-ned"}, {"id": "other-model"}]}
        )

    _install_urlopen(monkeypatch, handler)


# ── VLLMClient unit tests ──────────────────────────────────────────────


class TestVLLMClientHealth:
    def test_health_ok_on_200(self, monkeypatch):
        _ok_models(monkeypatch)
        assert VLLMClient("http://x:8003/v1", api_key="fake").check_health() is True

    def test_health_false_on_connection_error(self, monkeypatch):
        def handler(req, timeout=None):
            raise urllib.error.URLError("refused")

        _install_urlopen(monkeypatch, handler)
        assert VLLMClient("http://x:8003/v1", api_key="fake").check_health() is False

    def test_health_false_on_401(self, monkeypatch):
        def handler(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}")
            )

        _install_urlopen(monkeypatch, handler)
        # Wrong/missing key -> 401 -> unhealthy -> stage skips (fail-closed).
        assert VLLMClient("http://x:8003/v1", api_key="wrong").check_health() is False


class TestVLLMClientModels:
    def test_model_available_exact(self, monkeypatch):
        _ok_models(monkeypatch)
        client = VLLMClient("http://x:8003/v1", api_key="fake")
        assert client.model_available("qwen3.8-27b-ned") is True

    def test_model_available_prefix(self, monkeypatch):
        _ok_models(monkeypatch)
        client = VLLMClient("http://x:8003/v1", api_key="fake")
        assert client.model_available("qwen3.8-27b") is True

    def test_model_missing(self, monkeypatch):
        _ok_models(monkeypatch)
        client = VLLMClient("http://x:8003/v1", api_key="fake")
        assert client.model_available("not-a-model") is False

    def test_model_available_false_on_error(self, monkeypatch):
        def handler(req, timeout=None):
            raise TimeoutError("timed out")

        _install_urlopen(monkeypatch, handler)
        assert (
            VLLMClient("http://x:8003/v1", api_key="fake").model_available("m") is False
        )


class TestVLLMClientChat:
    def test_chat_success_normalizes_shape(self, monkeypatch):
        seen = {}

        def handler(req, timeout=None):
            seen["url"] = req.full_url
            seen["payload"] = json.loads(req.data.decode("utf-8"))
            seen["auth"] = req.get_header("Authorization")
            return _FakeHTTPResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": '{"verdict": "clean"}',
                            }
                        }
                    ]
                }
            )

        _install_urlopen(monkeypatch, handler)
        client = VLLMClient("http://x:8003/v1", api_key="fake-key")
        resp = client.chat(
            "qwen3.8-27b-ned",
            [{"role": "user", "content": "hi"}],
            format="json",
            options={"temperature": 0.1},
            timeout=30.0,
        )
        assert seen["url"] == "http://x:8003/v1/chat/completions"
        assert seen["payload"]["model"] == "qwen3.8-27b-ned"
        assert seen["payload"]["response_format"] == {"type": "json_object"}
        assert seen["payload"]["temperature"] == 0.1
        assert seen["auth"] == "Bearer fake-key"
        # Normalized to the Ollama shape the adapter expects.
        assert resp == {
            "message": {"role": "assistant", "content": '{"verdict": "clean"}'}
        }

    def test_chat_401_returns_none(self, monkeypatch):
        def handler(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}")
            )

        _install_urlopen(monkeypatch, handler)
        client = VLLMClient("http://x:8003/v1", api_key="wrong")
        assert client.chat("m", [{"role": "user", "content": "hi"}]) is None

    def test_chat_timeout_returns_none(self, monkeypatch):
        def handler(req, timeout=None):
            raise TimeoutError("timed out")

        _install_urlopen(monkeypatch, handler)
        client = VLLMClient("http://x:8003/v1", api_key="fake")
        assert client.chat("m", [{"role": "user", "content": "hi"}]) is None

    def test_chat_empty_content_returns_none(self, monkeypatch):
        def handler(req, timeout=None):
            return _FakeHTTPResponse({"choices": []})

        _install_urlopen(monkeypatch, handler)
        client = VLLMClient("http://x:8003/v1", api_key="fake")
        assert client.chat("m", [{"role": "user", "content": "hi"}]) is None

    def test_key_never_logged_on_error(self, monkeypatch, caplog):
        def handler(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "boom", {}, io.BytesIO(b"{}")
            )

        _install_urlopen(monkeypatch, handler)
        with caplog.at_level("WARNING"):
            VLLMClient("http://x:8003/v1", api_key="super-secret-key").chat(
                "m", [{"role": "user", "content": "hi"}]
            )
        assert "super-secret-key" not in caplog.text


# ── Protocol selection ─────────────────────────────────────────────────


def _vllm_config(**overrides):
    base = dict(
        enabled=True,
        protocol="vllm",
        endpoint="http://192.168.1.230:8003/v1",
        model_full="ned-served-id",
        model_bounded="qwen3.8-27b-ned",
        timeout_seconds=5.0,
        max_diff_chars=1000,
        max_rereviews=2,
        temperature=0.0,
    )
    base.update(overrides)
    return LLMReviewConfig(**base)


class FakeVLLMClient:
    """Scripted stand-in mirroring the VLLMClient interface."""

    def __init__(self, chat_response=None, chat_exc=None, healthy=True, models=()):
        self.chat_response = chat_response
        self.chat_exc = chat_exc
        self.healthy = healthy
        self.models = set(models)

    def check_health(self):
        return self.healthy

    def model_available(self, name):
        return name in self.models

    def chat(self, model, messages, **kwargs):
        if self.chat_exc is not None:
            raise self.chat_exc
        return self.chat_response


def _job():
    return SimpleNamespace(
        review_job_id="job-vllm-1",
        task_id="",
        repository="mbgulden/prismatic-engine",
        candidate_commit="abc123def456",
        candidate_tree="tree999",
    )


def _valid_payload():
    return {
        "findings": [
            {
                "severity": "high",
                "file": "prismatic/x.py",
                "lines": "10-20",
                "category": "security",
                "explanation": "Unsanitized input reaches the shell.",
            }
        ],
        "verdict": "repair_required",
        "confidence": 0.8,
        "rationale": "One high-severity finding needs a repair round.",
    }


def _vllm_envelope(payload_obj) -> dict:
    # The normalized shape VLLMClient.chat returns.
    return {"message": {"role": "assistant", "content": json.dumps(payload_obj)}}


class TestProtocolSelection:
    def test_from_env_defaults_to_ollama(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISMATIC_REVIEW_LLM_PROTOCOL", raising=False)
        monkeypatch.delenv("PRISMATIC_REVIEW_LLM", raising=False)
        import prismatic.review_factory.llm_deep_review as mod

        monkeypatch.setattr(mod, "_TOGGLE_FILE", tmp_path / "toggle.json")
        assert LLMReviewConfig.from_env().protocol == "ollama"

    def test_from_env_reads_vllm(self, monkeypatch):
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM_PROTOCOL", "vllm")
        assert LLMReviewConfig.from_env().protocol == "vllm"

    def test_from_env_unknown_protocol_falls_back_to_ollama(self, monkeypatch):
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM_PROTOCOL", "grpc")
        assert LLMReviewConfig.from_env().protocol == "ollama"

    def test_adapter_builds_vllm_client(self, monkeypatch):
        monkeypatch.delenv("VLLM_API_KEY", raising=False)
        monkeypatch.delenv("VLLM_NED_API_KEY", raising=False)
        adapter = LLMDeepReviewAdapter(_vllm_config())
        assert isinstance(adapter.client, VLLMClient)
        assert adapter.client.base_url == "http://192.168.1.230:8003/v1"

    def test_adapter_builds_ollama_client_by_default(self):
        from prismatic.providers.ollama import OllamaClient

        cfg = _vllm_config(protocol="ollama", endpoint="http://localhost:11434")
        assert isinstance(LLMDeepReviewAdapter(cfg).client, OllamaClient)


# ── Failure gates preserved under vLLM ─────────────────────────────────


class TestVLLMFailureGates:
    def test_401_skips_stage(self):
        client = FakeVLLMClient(chat_response=None)  # chat layer saw a 401
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="clean",
                deterministic_findings=[],
            )
            is None
        )

    def test_timeout_skips_stage(self):
        client = FakeVLLMClient(
            chat_exc=TimeoutError("timed out"),
            models={"ned-served-id", "qwen3.8-27b-ned"},
        )
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="repair_required",
                deterministic_findings=[],
            )
            is None
        )

    def test_invalid_json_skips_stage(self):
        client = FakeVLLMClient(
            chat_response={"message": {"content": "not json at all"}},
            models={"ned-served-id", "qwen3.8-27b-ned"},
        )
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="clean",
                deterministic_findings=[],
            )
            is None
        )

    def test_unhealthy_vllm_endpoint_skips_stage(self):
        client = FakeVLLMClient(healthy=False)
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        ok, reason = adapter.gates_pass()
        assert ok is False
        assert "vllm" in reason

    def test_success_path_still_validates_schema(self):
        client = FakeVLLMClient(
            chat_response=_vllm_envelope(_valid_payload()),
            models={"ned-served-id", "qwen3.8-27b-ned"},
        )
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        result = adapter.review(
            _job(),
            diff_text="diff --git a/x b/x",
            deterministic_verdict="clean",
            deterministic_findings=[],
        )
        assert result is not None
        assert result.verdict == "repair_required"
        assert len(result.findings) == 1

    def test_never_downgrade_holds_under_vllm(self):
        from prismatic.review_factory.llm_deep_review import resolve_llm_outcome

        payload = _valid_payload()
        payload["verdict"] = "clean"
        client = FakeVLLMClient(
            chat_response=_vllm_envelope(payload),
            models={"ned-served-id", "qwen3.8-27b-ned"},
        )
        adapter = LLMDeepReviewAdapter(_vllm_config(), client=client)
        result = adapter.review(
            _job(),
            diff_text="diff",
            deterministic_verdict="repair_required",
            deterministic_findings=[],
        )
        outcome = resolve_llm_outcome("repair_required", result)
        assert outcome == "packet"
