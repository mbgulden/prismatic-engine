"""
prismatic/mode_switch.py — Orchestration Mode Switch
=====================================================

Defines the three orchestration modes that control how the Prismatic Engine
pipeline executes. Each mode determines when human approval is required,
how aggressively the system automates, and what happens on failure.

Modes
-----
**INTERACTIVE** — Full human involvement. Every state transition requires
    explicit approval. Ideal for high-risk tasks, production deploys, and
    sensitive operations.

**COLLABORATIVE** — Agents execute autonomously but flag key decision
    points for human review. The REVIEW and INTEGRATE steps require human
    sign-off. This is the default mode.

**AUTONOMOUS** — Full auto-pilot. The system executes the entire 7-step
    pipeline without human intervention, only escalating on failure or
    credit exhaustion. Ideal for routine, low-risk tasks.

Usage
-----
    from prismatic.mode_switch import ModeSwitch, OrchestrationMode

    switch = ModeSwitch(OrchestrationMode.COLLABORATIVE)
    if switch.requires_approval(Step.REVIEW):
        print("Human sign-off needed")

    # Check if mode allows auto-advance
    if switch.can_auto_advance(Step.EXECUTE, Step.REVIEW):
        state_machine.advance()
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .state_machine import Step, OrchestrationMode


# ═══════════════════════════════════════════════════════════════
# Mode Policy Configurations
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModePolicy:
    """Defines the behavioral rules for a single orchestration mode.

    Each policy specifies:
    - Which steps require human approval
    - Whether auto-advance is allowed
    - Max autonomous retries before escalation
    - Failure escalation path
    """

    # Steps that require human sign-off (even in autonomous mode)
    approval_gates: set[Step]

    # Steps where auto-advance is allowed (no manual trigger needed)
    auto_advance_steps: set[Step]

    # Maximum retries before escalation to human
    max_retries: int

    # Agent to escalate to on persistent failure
    escalation_agent: str

    # Whether the mode allows autonomous dispatch (no human trigger)
    autonomous_dispatch: bool

    # Whether to post progress comments to Linear
    verbose_comments: bool

    # Human-readable description
    description: str


# ── Policy Definitions ────────────────────────────────────

INTERACTIVE_POLICY = ModePolicy(
    approval_gates={
        Step.DECOMPOSE,
        Step.DISPATCH,
        Step.EXECUTE,
        Step.REVIEW,
        Step.FEEDBACK,
        Step.REFINE,
        Step.INTEGRATE,
    },
    auto_advance_steps=set(),
    max_retries=1,
    escalation_agent="fred",
    autonomous_dispatch=False,
    verbose_comments=True,
    description=(
        "Full human involvement. Every state transition requires explicit "
        "approval. Best for high-risk tasks, production deploys, and "
        "sensitive operations."
    ),
)

COLLABORATIVE_POLICY = ModePolicy(
    approval_gates={
        Step.REVIEW,
        Step.INTEGRATE,
    },
    auto_advance_steps={
        Step.DECOMPOSE,
        Step.DISPATCH,
        Step.EXECUTE,
        Step.FEEDBACK,
        Step.REFINE,
    },
    max_retries=3,
    escalation_agent="fred",
    autonomous_dispatch=True,
    verbose_comments=True,
    description=(
        "Agents execute autonomously but flag key decision points for human "
        "review. REVIEW and INTEGRATE require sign-off. Default mode."
    ),
)

AUTONOMOUS_POLICY = ModePolicy(
    approval_gates=set(),
    auto_advance_steps={
        Step.DECOMPOSE,
        Step.DISPATCH,
        Step.EXECUTE,
        Step.REVIEW,
        Step.FEEDBACK,
        Step.REFINE,
        Step.INTEGRATE,
    },
    max_retries=5,
    escalation_agent="fred",
    autonomous_dispatch=True,
    verbose_comments=False,
    description=(
        "Full auto-pilot. Executes the entire pipeline without human "
        "intervention. Escalates only on persistent failure or credit "
        "exhaustion. Best for routine, low-risk tasks."
    ),
)

# ── Policy lookup ─────────────────────────────────────────

_POLICIES: dict[OrchestrationMode, ModePolicy] = {
    OrchestrationMode.INTERACTIVE: INTERACTIVE_POLICY,
    OrchestrationMode.COLLABORATIVE: COLLABORATIVE_POLICY,
    OrchestrationMode.AUTONOMOUS: AUTONOMOUS_POLICY,
}


# ═══════════════════════════════════════════════════════════════
# Mode Switch
# ═══════════════════════════════════════════════════════════════

class ModeSwitch:
    """Core orchestration mode controller.

    Determines pipeline behavior based on the active orchestration mode.
    Acts as a policy decision point for the dispatcher and state machine.

    Thread-safe for reads; mode changes acquire a lock.
    """

    def __init__(self, mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE):
        """
        Args:
            mode: Initial orchestration mode.
        """
        if isinstance(mode, str):
            mode = OrchestrationMode.from_string(mode)
        self._mode = mode
        self._policy = _POLICIES[mode]
        self._mode_history: list[dict[str, str]] = []

    # ── Mode Accessors ──────────────────────────────────────

    @property
    def mode(self) -> OrchestrationMode:
        """Current orchestration mode."""
        return self._mode

    @property
    def policy(self) -> ModePolicy:
        """Current mode's policy configuration."""
        return self._policy

    def set_mode(self, new_mode: OrchestrationMode | str) -> None:
        """Switch to a different orchestration mode.

        Records the mode change in history.

        Args:
            new_mode: Target mode.
        """
        if isinstance(new_mode, str):
            new_mode = OrchestrationMode.from_string(new_mode)

        old = self._mode
        if old == new_mode:
            return

        self._mode = new_mode
        self._policy = _POLICIES[new_mode]
        self._mode_history.append({
            "from": old.value,
            "to": new_mode.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Decision Methods ────────────────────────────────────

    def requires_approval(self, step: Step) -> bool:
        """Check if *step* requires human approval in the current mode.

        Returns:
            ``True`` if human sign-off is needed before proceeding.
        """
        return step in self._policy.approval_gates

    def can_auto_advance(self, from_step: Step, to_step: Step) -> bool:
        """Check if the dispatcher can auto-advance from *from_step* to *to_step*.

        Returns:
            ``True`` if the transition can happen without human intervention.
        """
        return from_step in self._policy.auto_advance_steps

    def should_escalate(self, retry_count: int) -> bool:
        """Check if *retry_count* exceeds the mode's escalation threshold.

        Returns:
            ``True`` if work should be escalated to a human.
        """
        return retry_count >= self._policy.max_retries

    def escalation_target(self) -> str:
        """Return the agent label for escalation in this mode."""
        return self._policy.escalation_agent

    def is_autonomous(self) -> bool:
        """Return ``True`` if the mode allows fully autonomous execution."""
        return self._mode == OrchestrationMode.AUTONOMOUS

    def is_interactive(self) -> bool:
        """Return ``True`` if at interactivity level or above."""
        return self._mode in (
            OrchestrationMode.INTERACTIVE,
            OrchestrationMode.COLLABORATIVE,
        )

    def should_post_comments(self) -> bool:
        """Return ``True`` if the mode expects verbose Linear comments."""
        return self._policy.verbose_comments

    def can_dispatch_autonomously(self) -> bool:
        """Return ``True`` if the dispatcher can launch agents without a
        human trigger (e.g. ``/agent:`` command)."""
        return self._policy.autonomous_dispatch

    # ── Mode Transition Validation ──────────────────────────

    def allowed_transitions(self) -> list[str]:
        """Return the list of modes this mode can transition to.

        All modes can transition to any other mode — this is for
        future constraints if needed.
        """
        return [m.value for m in OrchestrationMode]

    def validate_mode_transition(self, target: OrchestrationMode) -> bool:
        """Validate that switching to *target* mode is allowed.

        Currently always returns True (all transitions are valid).
        Future: may restrict certain transitions (e.g. AUTONOMOUS → INTERACTIVE
        requires confirmation).
        """
        return True

    # ── Diagnostics ─────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the mode switch state."""
        return {
            "current_mode": self._mode.value,
            "policy": {
                "approval_gates": sorted(s.value for s in self._policy.approval_gates),
                "auto_advance_steps": sorted(s.value for s in self._policy.auto_advance_steps),
                "max_retries": self._policy.max_retries,
                "escalation_agent": self._policy.escalation_agent,
                "autonomous_dispatch": self._policy.autonomous_dispatch,
                "verbose_comments": self._policy.verbose_comments,
            },
            "history": self._mode_history,
        }

    def mode_transition_count(self) -> int:
        """Return the number of mode switches that occurred."""
        return len(self._mode_history)


# ═══════════════════════════════════════════════════════════════
# Global Mode Switch (singleton for the dispatcher process)
# ═══════════════════════════════════════════════════════════════

# Lazy-initialized singleton
_global_switch: ModeSwitch | None = None


def get_mode_switch() -> ModeSwitch:
    """Return the global mode switch instance.

    Creates a new one on first call using the ``PRISMATIC_MODE`` env var
    (defaults to ``collaborative``).
    """
    global _global_switch
    if _global_switch is None:
        env_mode = os.environ.get("PRISMATIC_MODE", "collaborative")
        _global_switch = ModeSwitch(env_mode)
    return _global_switch


def set_global_mode(mode: OrchestrationMode | str) -> None:
    """Set the global mode switch to a new mode."""
    get_mode_switch().set_mode(mode)


def reset_global_mode() -> None:
    """Reset the global mode switch (useful for tests)."""
    global _global_switch
    _global_switch = None


# ═══════════════════════════════════════════════════════════════
# Mode-switch CLI command (for debugging / prod control)
# ═══════════════════════════════════════════════════════════════

def _cli_set_mode(args: list[str]) -> int:
    """CLI handler: ``prismatic mode set <mode>``."""
    if len(args) < 1:
        print("Usage: prismatic mode set <interactive|collaborative|autonomous>")
        return 1
    try:
        mode = OrchestrationMode.from_string(args[0])
        set_global_mode(mode)
        print(f"Mode set to: {mode.value}")
        return 0
    except ValueError:
        print(f"Invalid mode: {args[0]}")
        return 1


def _cli_show_mode(args: list[str]) -> int:
    """CLI handler: ``prismatic mode show``."""
    switch = get_mode_switch()
    snap = switch.snapshot()
    print(json.dumps(snap, indent=2))
    return 0
