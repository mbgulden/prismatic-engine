"""Unified platform and harness-agnostic LLM provider interface for Prismatic Hypervisor.

Supports local models (Ollama, vLLM) and hosted frontier APIs (OpenAI, Anthropic, Gemini)
through a single streaming and non-streaming interface without proprietary SDK lock-in.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

logger = logging.getLogger("prismatic.providers.llm")


@dataclass
class LLMMessage:
    """A role-tagged message in a conversation thread."""

    role: str  # system, user, assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class LLMRequest:
    """Normalized multi-harness inference request."""

    messages: list[LLMMessage]
    model: str = "default"
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False
    system_prompt: str | None = None

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        system_prompt: str | None = None,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMRequest:
        msgs = []
        if system_prompt:
            msgs.append(LLMMessage(role="system", content=system_prompt))
        msgs.append(LLMMessage(role="user", content=prompt))
        return cls(
            messages=msgs,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            system_prompt=system_prompt,
        )


@dataclass
class LLMResponse:
    """Normalized multi-harness inference response."""

    text: str
    model: str
    provider: str
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseLLMProvider(ABC):
    """Abstract sovereign provider interface for language model inference."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute a blocking completion request."""
        raise NotImplementedError

    @abstractmethod
    def stream_generate(self, request: LLMRequest) -> Iterator[str]:
        """Stream completion tokens sequentially."""
        raise NotImplementedError

    @abstractmethod
    def check_health(self) -> bool:
        """Check provider connectivity and status."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models for this provider."""
        raise NotImplementedError


class OllamaProvider(BaseLLMProvider):
    """Local inference provider via Ollama HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        url = base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        super().__init__(base_url=url, api_key=api_key, timeout=timeout)

    def generate(self, request: LLMRequest) -> LLMResponse:
        model = request.model if request.model != "default" else "llama3.2:latest"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
            "stream": False,
        }
        url = f"{self.base_url}/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data.get("message", {}).get("content", "")
                return LLMResponse(
                    text=text,
                    model=model,
                    provider="ollama",
                    finish_reason="stop" if data.get("done") else "length",
                    usage={
                        "prompt_tokens": data.get("prompt_eval_count", 0),
                        "completion_tokens": data.get("eval_count", 0),
                    },
                    raw=data,
                )
        except Exception as exc:
            logger.error("Ollama generate failed: %s", exc)
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

    def stream_generate(self, request: LLMRequest) -> Iterator[str]:
        model = request.model if request.model != "default" else "llama3.2:latest"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
            "stream": True,
        }
        url = f"{self.base_url}/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    if line:
                        chunk = json.loads(line.decode("utf-8"))
                        text_part = chunk.get("message", {}).get("content", "")
                        if text_part:
                            yield text_part
        except Exception as exc:
            logger.error("Ollama stream failed: %s", exc)
            raise RuntimeError(f"Ollama stream failed: {exc}") from exc

    def check_health(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []


class VLLMProvider(BaseLLMProvider):
    """High-throughput local/remote inference provider via vLLM OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        url = base_url or os.environ.get("VLLM_BASE_URL") or "http://localhost:8000/v1"
        key = api_key or os.environ.get("VLLM_API_KEY", "EMPTY")
        super().__init__(base_url=url, api_key=key, timeout=timeout)

    def generate(self, request: LLMRequest) -> LLMResponse:
        model = request.model if request.model != "default" else "default"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data.get("choices", [{}])[0]
                text = choice.get("message", {}).get("content", "")
                return LLMResponse(
                    text=text,
                    model=data.get("model", model),
                    provider="vllm",
                    finish_reason=choice.get("finish_reason", "stop"),
                    usage=data.get("usage", {}),
                    raw=data,
                )
        except Exception as exc:
            logger.error("vLLM generate failed: %s", exc)
            raise RuntimeError(f"vLLM request failed: {exc}") from exc

    def stream_generate(self, request: LLMRequest) -> Iterator[str]:
        model = request.model if request.model != "default" else "default"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: ") and line_str != "data: [DONE]":
                        try:
                            chunk = json.loads(line_str[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except Exception as exc:
            logger.error("vLLM stream failed: %s", exc)
            raise RuntimeError(f"vLLM stream failed: {exc}") from exc

    def check_health(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/models", method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/models", method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []


class OpenAIProvider(BaseLLMProvider):
    """Frontier hosted provider via OpenAI API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        url = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        super().__init__(base_url=url, api_key=key, timeout=timeout)

    def generate(self, request: LLMRequest) -> LLMResponse:
        model = request.model if request.model != "default" else "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data.get("choices", [{}])[0]
                text = choice.get("message", {}).get("content", "")
                return LLMResponse(
                    text=text,
                    model=data.get("model", model),
                    provider="openai",
                    finish_reason=choice.get("finish_reason", "stop"),
                    usage=data.get("usage", {}),
                    raw=data,
                )
        except Exception as exc:
            logger.error("OpenAI generate failed: %s", exc)
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    def stream_generate(self, request: LLMRequest) -> Iterator[str]:
        model = request.model if request.model != "default" else "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: ") and line_str != "data: [DONE]":
                        try:
                            chunk = json.loads(line_str[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except Exception as exc:
            logger.error("OpenAI stream failed: %s", exc)
            raise RuntimeError(f"OpenAI stream failed: {exc}") from exc

    def check_health(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list[str]:
        return ["gpt-4o", "gpt-4o-mini", "o1-preview", "o1-mini"]


class MockProvider(BaseLLMProvider):
    """Deterministic in-memory mock provider for hermetic testing."""

    def __init__(self, response_text: str = "mocked completion", **kwargs: Any) -> None:
        super().__init__(base_url="mock://local", **kwargs)
        self.response_text = response_text

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=self.response_text,
            model=request.model,
            provider="mock",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )

    def stream_generate(self, request: LLMRequest) -> Iterator[str]:
        words = self.response_text.split(" ")
        for w in words:
            yield w + " "

    def check_health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["mock-model-v1"]


def get_llm_provider(
    provider_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> BaseLLMProvider:
    """Factory to acquire an instantiated LLMProvider based on configuration or environment."""
    name = (provider_name or os.environ.get("PRISMATIC_LLM_PROVIDER") or "ollama").lower().strip()

    if name == "mock":
        return MockProvider(**kwargs)
    elif name in {"ollama", "local"}:
        return OllamaProvider(base_url=base_url, api_key=api_key, **kwargs)
    elif name in {"vllm", "tgi"}:
        return VLLMProvider(base_url=base_url, api_key=api_key, **kwargs)
    elif name in {"openai"}:
        return OpenAIProvider(base_url=base_url, api_key=api_key, **kwargs)
    else:
        logger.info("Defaulting provider '%s' to OllamaProvider", name)
        return OllamaProvider(base_url=base_url, api_key=api_key, **kwargs)
