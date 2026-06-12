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
      pick up the results. (Legacy Kai-specific variant — still supported.)
    - ``review_complete`` — Generalized: ANY reviewer agent finished work;
      the ``origin_agent`` metadata field tells the recipient which issue
      was routed back. Pattern: Kai→AGY→Kai, Ned→AGY→Ned, etc.

Born from: GRO-1481 — Fix AGY→Kai nudge back
Generalized: GRO-1485 — Close the feedback loop for all agent pairs
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
        "target": "kai",       # Primary recipient (legacy Kai-specific)
        "notify": "fred",      # Also notify Fred for oversight
        "action": "review",    # Kai should REVIEW AGY's output
    },
    "review_complete": {
        "description": (
            "Generalized review completion signal. Any agent (Kai, Ned, "
            "etc.) that previously requested a peer review has now had "
            "that review completed by the reviewer agent. The "
            "``origin_agent`` metadata field identifies which agent "
            "should pick up the results."
        ),
        "priority": 2,        # High priority — origin should act promptly
        "target": "dynamic",   # Determined by origin_agent metadata
        "notify": "fred",      # Also notify Fred for oversight
        "action": "review",    # Origin should REVIEW the output
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


def is_review_complete(metadata: dict[str, Any]) -> bool:
    """Check if a signal payload indicates a generalized review completion.

    Args:
        metadata: The ``metadata`` dict from a SignalPayload.

    Returns:
        ``True`` if this is a ``review_complete`` signal.
    """
    return metadata.get("signal_type") == "review_complete"


def get_origin_agent(metadata: dict[str, Any]) -> str | None:
    """Extract the origin agent from review_complete signal metadata.

    Args:
        metadata: The ``metadata`` dict from a SignalPayload.

    Returns:
        Origin agent name (e.g. ``"kai"``), or ``None`` if not set.
    """
    return metadata.get("origin_agent")


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
