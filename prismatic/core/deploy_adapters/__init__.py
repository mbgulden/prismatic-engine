"""Deployment rollback adapter shims for PWP run-state rollbacks.

Adapters expose a small ``undo(previous_artifact_sha, context) -> bool`` contract
used by :func:`prismatic.core.pwp_state.handle_rollback`.  Concrete deploy
systems can replace these functions with provider-specific restore logic while
keeping the rollback metadata payload stable.
"""

from __future__ import annotations

from . import cloudflare, file, http

__all__ = ["cloudflare", "file", "http"]
