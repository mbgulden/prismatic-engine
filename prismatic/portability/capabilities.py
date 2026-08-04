"""
Prismatic Engine — Closed Capability Vocabulary
================================================

Defines the closed capability scope vocabulary for provider registry and bindings.
"""

from __future__ import annotations

from enum import Enum
from typing import Set

from prismatic.portability.exceptions import InvalidCapabilityError


class CapabilityScope(str, Enum):
    """Closed vocabulary of valid provider capability scopes."""

    VCS_READ = "vcs:read"
    VCS_WRITE = "vcs:write"
    ISSUE_READ = "issue:read"
    ISSUE_WRITE = "issue:write"
    CHAT_SEND = "chat:send"
    LLM_GENERATE = "llm:generate"
    JOB_EXECUTE = "job:execute"
    BLOB_READ = "blob:read"
    BLOB_WRITE = "blob:write"
    METRICS_EMIT = "metrics:emit"


_KNOWN_CAPABILITY_VALUES: Set[str] = {cap.value for cap in CapabilityScope}


def validate_capability_scope(scope: str) -> str:
    """Validate that a capability scope string belongs to the closed vocabulary.

    Raises:
        InvalidCapabilityError: If the capability scope is unknown or malformed.
    """
    if not isinstance(scope, str) or not scope.strip():
        raise InvalidCapabilityError("Capability scope must be a non-empty string.")

    normalized = scope.strip()
    if normalized not in _KNOWN_CAPABILITY_VALUES:
        raise InvalidCapabilityError(
            f"Unknown capability scope {normalized!r}. Must be one of: {sorted(_KNOWN_CAPABILITY_VALUES)}"
        )
    return normalized
