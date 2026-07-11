"""File deploy rollback adapter."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def undo(previous_artifact_sha: str, context: dict[str, Any]) -> bool:
    """Restore a file-target artifact.

    The current run-state contract stores the prior artifact identifier and
    metadata bundle; actual file restoration is environment-specific and is
    expected to be supplied by the deploy runner.  Returning ``False`` keeps
    the rollback handler fail-closed when no concrete runner has patched in a
    provider implementation.
    """

    logger.error(
        "No file rollback adapter configured for artifact %s (run_id=%s)",
        previous_artifact_sha,
        context.get("run_id"),
    )
    return False
