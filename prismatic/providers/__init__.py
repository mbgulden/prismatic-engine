"""
Prismatic Engine — Providers Package
=====================================

Provider-agnostic abstractions for signals and tasks.
Signals carry work to agents; tasks pull work from issue trackers.
"""
from __future__ import annotations

from . import signals
from . import tasks
from .github import GitHubProvider
from .llm import (
    AntigravityProvider,
    BaseLLMProvider,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    OllamaProvider,
    OpenAIProvider,
    VLLMProvider,
    get_llm_provider,
)

__all__ = [
    "signals",
    "tasks",
    "GitHubProvider",
    "AntigravityProvider",
    "BaseLLMProvider",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "VLLMProvider",
    "get_llm_provider",
]
