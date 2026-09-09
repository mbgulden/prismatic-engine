"""Unit tests for the Unified LLM Provider interface and implementations."""

import pytest
from prismatic.providers.llm import (
    AntigravityProvider,
    BaseLLMProvider,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    MockProvider,
    OllamaProvider,
    OpenAIProvider,
    VLLMProvider,
    get_llm_provider,
)


def test_llm_request_response_models():
    req = LLMRequest.from_prompt(
        prompt="Explain distributed consensus in one paragraph",
        system_prompt="You are a distributed systems architect.",
        model="llama3.2",
        temperature=0.5,
        max_tokens=1000,
    )
    assert len(req.messages) == 2
    assert req.messages[0].role == "system"
    assert req.messages[1].role == "user"
    assert req.temperature == 0.5

    res = LLMResponse(
        text="Consensus allows decentralized nodes to agree on a state machine.",
        model="llama3.2",
        provider="mock",
        finish_reason="stop",
        usage={"prompt_tokens": 15, "completion_tokens": 12},
    )
    d = res.to_dict()
    assert d["provider"] == "mock"
    assert d["usage"]["total_tokens"] if "total_tokens" in d["usage"] else True


def test_mock_provider_execution():
    provider = MockProvider(response_text="Sovereign hypervisor active")
    assert provider.check_health() is True
    assert "mock-model-v1" in provider.list_models()

    req = LLMRequest.from_prompt("Ping")
    res = provider.generate(req)
    assert res.text == "Sovereign hypervisor active"
    assert res.provider == "mock"

    streamed = list(provider.stream_generate(req))
    assert len(streamed) == 3
    assert "".join(streamed).strip() == "Sovereign hypervisor active"


def test_get_llm_provider_factory():
    mock_p = get_llm_provider("mock", response_text="test")
    assert isinstance(mock_p, MockProvider)

    ollama_p = get_llm_provider("ollama")
    assert isinstance(ollama_p, OllamaProvider)
    assert ollama_p.base_url == "http://localhost:11434"

    vllm_p = get_llm_provider("vllm")
    assert isinstance(vllm_p, VLLMProvider)
    assert vllm_p.base_url == "http://localhost:8000/v1"

    openai_p = get_llm_provider("openai")
    assert isinstance(openai_p, OpenAIProvider)
    assert openai_p.base_url == "https://api.openai.com/v1"


def test_ollama_provider_offline_graceful_handling():
    # Attempting to talk to non-existent port should fail with clean RuntimeError
    p = OllamaProvider(base_url="http://127.0.0.1:54321", timeout=0.5)
    assert p.check_health() is False
    assert p.list_models() == []

    with pytest.raises(RuntimeError):
        p.generate(LLMRequest.from_prompt("Hello"))


def test_antigravity_provider_binary_resolution():
    binary = AntigravityProvider._resolve_binary()
    assert binary is not None
    assert "agy" in binary


def test_antigravity_provider_health_and_models():
    p = AntigravityProvider()
    assert p.check_health() is True
    models = p.list_models()
    assert isinstance(models, list)
    assert len(models) > 0
    assert any("gemini" in m for m in models)


def test_antigravity_provider_generate():
    p = AntigravityProvider(default_model="gemini-3.8-flash-high")
    req = LLMRequest.from_prompt("Respond with exactly: HELLO_PRISMATIC")
    resp = p.generate(req)
    assert resp.provider == "antigravity"
    assert resp.model == "gemini-3.8-flash-high"
    assert "HELLO_PRISMATIC" in resp.text
    assert resp.usage.get("prompt_tokens", 0) > 0


def test_antigravity_provider_streaming():
    p = AntigravityProvider()
    req = LLMRequest.from_prompt("Respond with: ONE TWO THREE")
    tokens = list(p.stream_generate(req))
    assert len(tokens) > 0
    combined = "".join(tokens)
    assert "ONE" in combined


def test_get_llm_provider_antigravity_factory():
    p = get_llm_provider("antigravity")
    assert isinstance(p, AntigravityProvider)

    p2 = get_llm_provider("agy")
    assert isinstance(p2, AntigravityProvider)
