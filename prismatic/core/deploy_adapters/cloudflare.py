"""Cloudflare Pages rollback adapter shim."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def undo(previous_artifact_sha: str, context: dict[str, Any]) -> bool:
    """Restore a Cloudflare Pages deployment artifact.

    Provider credentials and project mapping live outside the run-state store,
    so this shim fails closed until the active deploy runner supplies a concrete
    implementation.  The stable inputs are ``previous_artifact_sha`` and
    ``context['rollback_restore']``.
    """

    logger.error(
        "No Cloudflare rollback adapter configured for artifact %s (client_id=%s)",
        previous_artifact_sha,
        context.get("client_id"),
    )
    return False
