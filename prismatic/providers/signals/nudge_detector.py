"""
Nudge Signal Detector — shared signal-type registry for the Prismatic Engine.

This module defines the known nudge signal types and provides detection
functions used by both the dispatcher (prismatic.dispatcher) and agent-side
pollers (Kai's nudge_poller.py, etc.).

Adding a new signal type:
    1. Add it to SIGNAL_TYPES below.
    2. Add a detect_<name>() function if programmatic detection is needed.
    3. Agent pollers check ``payload.metadata.get("signal_type")`` to route.

Signal types defined here:
    - ``agy_review_complete`` — AGY finished reviewing an issue; Kai should
      pick up the results.

Born from: GRO-1481 — Fix AGY→Kai nudge back
"""

from __future__ import annotations

from typing import Any

# ── Signal Type Registry ──────────────────────────────────────────

SIGNAL_TYPES: dict[str, dict[str, Any]] = {
    "agy_review_complete": {
        "description": (
            "AGY has completed a review of an issue that was previously "
            "routed through Kai (kai→agy→fred pipeline). Kai should "
            "inspect the AGY review results and proceed with implementation "
            "or further routing."
        ),
        "priority": 2,        # High priority — Kai should act promptly
        "target": "kai",       # Primary recipient
        "notify": "fred",      # Also notify Fred for oversight
        "action": "review",    # Kai should REVIEW AGY's output
    },
}


# ── Detection Functions ────────────────────────────────────────────

def is_agy_review_complete(metadata: dict[str, Any]) -> bool:
    """Check if a signal payload indicates an AGY review completion.

    Usage in agent poller::

        payload = json.loads(nudge_file.read_text())
        if is_agy_review_complete(payload.get("metadata", {})):
            print("AGY review complete — pick up results!")

    Args:
        metadata: The ``metadata`` dict from a SignalPayload.

    Returns:
        ``True`` if this is an ``agy_review_complete`` signal.
    """
    return metadata.get("signal_type") == "agy_review_complete"


def get_signal_info(signal_type: str) -> dict[str, Any] | None:
    """Look up signal type metadata from the registry.

    Args:
        signal_type: Signal type string (e.g. ``"agy_review_complete"``).

    Returns:
        Signal type info dict, or ``None`` if unknown.
    """
    return SIGNAL_TYPES.get(signal_type)


def list_signal_types() -> list[str]:
    """Return all registered signal type names."""
    return list(SIGNAL_TYPES.keys())
