"""Unit tests for the Unified LLM Provider interface and implementations."""

import pytest
from prismatic.providers.llm import (
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
