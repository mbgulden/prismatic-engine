"""Agent harness adapters for Prismatic Engine."""

from __future__ import annotations

import logging
import os
from typing import Any

from prismatic.harnesses.agy_cli import AGYCLIHarness
from prismatic.harnesses.base import AgentHarness, HarnessCapabilities, HarnessStatus
from prismatic.harnesses.hermes.adapter import HermesHarness

logger = logging.getLogger("prismatic.harnesses")


def get_agy_harness(
    runtime_preference: str | None = None,
    config: dict[str, Any] | None = None,
) -> AgentHarness:
    """Resolve the active AGY harness adapter based on canary environment and availability.

    Canary Safety Invariants (Phase 1):
    1. Default is 'cli' so existing fleet systems remain untouched.
    2. If 'sdk' is requested but google.antigravity is missing, logs a diagnostic warning
       and cleanly falls back to 'cli' rather than crashing with an ImportError.
    """
    runtime = (runtime_preference or os.environ.get("PRISMATIC_AGY_RUNTIME", "cli")).strip().lower()

    if runtime == "sdk":
        from prismatic.harnesses.agy_sdk import AGYSDKHarness, is_sdk_available

        if is_sdk_available():
            return AGYSDKHarness(config=config)

        logger.warning(
            "PRISMATIC_AGY_RUNTIME='sdk' requested, but google.antigravity is unavailable. "
            "Falling back cleanly to AGYCLIHarness for canary safety."
        )

    return AGYCLIHarness(config=config)


__all__ = [
    "AGYCLIHarness",
    "AgentHarness",
    "HarnessCapabilities",
    "HarnessStatus",
    "HermesHarness",
    "get_agy_harness",
]
