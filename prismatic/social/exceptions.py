"""Typed exceptions for the social pipeline.

Centralizing exceptions lets the CLI surface a single, friendly failure
mode without leaking Graph API internals to the operator.
"""

from __future__ import annotations


class SocialPipelineError(Exception):
    """Base class for all prismatic.social errors."""


class ConfigError(SocialPipelineError):
    """Required configuration missing or malformed."""


class MediaLibraryError(SocialPipelineError):
    """Media library path is missing, unreadable, or empty."""


class CaptionError(SocialPipelineError):
    """Caption generation failed for a photo."""


class MetaAPIError(SocialPipelineError):
    """Meta Graph API call failed with a non-recoverable error."""


class RateLimitError(MetaAPIError):
    """Meta returned 429 or X-App-Usage signal — back off and retry."""


class AuthError(MetaAPIError):
    """Meta returned 401/403 — token expired or permissions missing."""


class QueueError(SocialPipelineError):
    """Queue store read/write/transition failure."""


class IllegalStateTransition(QueueError):
    """Attempted a PostStatus transition not allowed by the state machine."""
