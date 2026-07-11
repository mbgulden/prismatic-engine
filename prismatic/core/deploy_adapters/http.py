"""HTTP deploy rollback adapter shim."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def undo(previous_artifact_sha: str, context: dict[str, Any]) -> bool:
    """Restore an HTTP deployment artifact.

    This default shim fails closed; deployments that support HTTP rollback must
    inject provider-specific restore logic while preserving the same metadata
    contract.
    """

    logger.error(
        "No HTTP rollback adapter configured for artifact %s (client_id=%s)",
        previous_artifact_sha,
        context.get("client_id"),
    )
    return False
