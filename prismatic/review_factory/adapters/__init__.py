"""L0 harness adapters: harness_output -> ReviewArtifact (plan §4).

Pure translators: no network, no side effects, no judgment. Core never
imports harness code; a new harness is one new module plus one registry
entry here — no core changes.

``ReviewArtifact`` is workstream A's ``prismatic/review_factory/artifact.py``
(the §2 schema, fail-closed boundary validation, content hashing).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prismatic.review_factory.artifact import ArtifactValidationError, ReviewArtifact

from ._artifact import AdapterError
from .claude_code import ClaudeCodeAdapter
from .codex_cli import CodexCliAdapter
from .gemini_cli import GeminiCliAdapter
from .github_pr import GitHubPRAdapter
from .hermes import HermesRunAdapter

__all__ = [
    "AdapterError",
    "ArtifactValidationError",
    "ClaudeCodeAdapter",
    "CodexCliAdapter",
    "GeminiCliAdapter",
    "GitHubPRAdapter",
    "HarnessAdapter",
    "HermesRunAdapter",
    "ReviewArtifact",
    "adapter_harness_ids",
    "get_adapter",
]


@runtime_checkable
class HarnessAdapter(Protocol):
    """Plan §4 adapter contract."""

    harness_id: str

    def to_artifact(self, raw: Any) -> ReviewArtifact: ...
    def explicit_gaps(self, raw: Any) -> list[str]: ...


_ADAPTERS: dict[str, type] = {
    "claude-code-cli": ClaudeCodeAdapter,
    "gemini-cli": GeminiCliAdapter,
    "codex-cli": CodexCliAdapter,
    "hermes": HermesRunAdapter,
    "github-pr": GitHubPRAdapter,
}


def adapter_harness_ids() -> tuple[str, ...]:
    """harness_ids with a shipped adapter. ``manual`` has none by design:
    per the plan §11 rollback, a harness without an adapter falls back to
    manual artifact submission."""
    return tuple(_ADAPTERS)


def get_adapter(harness_id: str) -> HarnessAdapter:
    """Return the adapter for ``harness_id``. Fail-closed on unknown ids."""
    try:
        cls = _ADAPTERS[harness_id]
    except KeyError:
        raise KeyError(
            f"unknown harness_id {harness_id!r}; known: {sorted(_ADAPTERS)}"
        ) from None
    return cls()
