"""P3: Linear State Machine Validation for Deploy Hook.

Validates Linear issue state before transitioning to prevent illegal or ambiguous
transitions (e.g. from Backlog, Cancelled, or Archived).
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Valid source states that are allowed to transition to 'Done' on deploy
ALLOWED_SOURCE_STATES = {
    "in review",
    "in progress",
    "ready for deploy",
    "staging",
    "verifying",
    "done",  # Idempotent re-entry
}

BLOCKED_SOURCE_STATES = {
    "backlog",
    "cancelled",
    "canceled",
    "duplicate",
    "archived",
}


def validate_linear_state_transition(
    issue_id: str,
    current_state: str,
    target_state: str = "Done",
) -> Tuple[bool, str]:
    """Validate whether transitioning from current_state to target_state is permitted.

    Returns (is_allowed, reason_message).
    """
    clean_current = (current_state or "In Review").strip().lower()

    if clean_current in BLOCKED_SOURCE_STATES:
        msg = f"Cannot transition {issue_id} to '{target_state}': issue is currently in '{current_state}' state"
        logger.warning(msg)
        return False, msg

    if clean_current in ALLOWED_SOURCE_STATES:
        return True, f"Transition permitted from '{current_state}' to '{target_state}'"

    # Default fallback: allow standard in-flight states with notice
    return True, f"Allowed transition from '{current_state}' to '{target_state}'"
