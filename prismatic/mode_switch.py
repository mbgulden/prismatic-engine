"""
Prismatic Engine — Orchestration Mode Switch
===========================================

Gates whether human approval is required between orchestration steps/states.
Supports three modes:
  * Interactive - every transition pauses for human approval
  * Collaborative - major transitions pause; minor transitions auto-fire
  * Autonomous - all transitions auto-fire; only escalations trigger human prompts
"""

from enum import Enum
from typing import Callable, Any, Dict, Set, Tuple, Optional

class OrchestrationMode(str, Enum):
    INTERACTIVE = "interactive"
    COLLABORATIVE = "collaborative"
    AUTONOMOUS = "autonomous"

# The 7 canonical pipeline states described in the system soul / orchestration loops
STATES = [
    "decompose",
    "dispatch",
    "execute",
    "review",
    "feedback",
    "refine",
    "integrate"
]

class ModeSwitch:
    def __init__(self, mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE):
        self.set_mode(mode)
        self.pending_approvals: Set[Tuple[str, str]] = set()
        self.escalation_hooks: list[Callable[[str, str, Optional[str]], Any]] = []

    def set_mode(self, mode: OrchestrationMode | str) -> None:
        """Set the active orchestration mode."""
        if isinstance(mode, str):
            mode = OrchestrationMode(mode.lower())
        self.mode = mode

    def register_escalation_hook(self, hook: Callable[[str, str, Optional[str]], Any]) -> None:
        """Register a callback to be invoked on escalation events."""
        self.escalation_hooks.append(hook)

    def is_major_transition(self, from_state: str, to_state: str) -> bool:
        """
        Determine if a state transition is considered 'major'.
        Major transitions include:
          - Decompose to Execute (any transition leaving decompose or entering execute)
          - Review to Integrate (any transition leaving review or entering integrate)
        """
        f = from_state.strip().lower()
        t = to_state.strip().lower()
        
        # Decompose to Execute (starts from decompose or enters execute)
        if f == "decompose" or t == "execute":
            return True
        # Review to Integrate (starts from review or enters integrate)
        if f == "review" or t == "integrate":
            return True
            
        return False

    def request_approval(
        self,
        from_state: str,
        to_state: str,
        is_escalation: bool = False,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Request approval to transition from from_state to to_state.
        
        Returns:
            bool: True if the transition is allowed to auto-fire,
                  False if the transition must pause and await human approval.
        """
        f = from_state.strip().lower()
        t = to_state.strip().lower()
        key = (f, t)

        if is_escalation:
            # Trigger registered escalation hooks
            for hook in self.escalation_hooks:
                try:
                    hook(f, t, reason)
                except Exception as exc:
                    print(f"[ModeSwitch] Escalation hook failed: {exc}")
            # Escalations always trigger human prompts / pause across all modes
            self.pending_approvals.add(key)
            return False

        if self.mode == OrchestrationMode.INTERACTIVE:
            self.pending_approvals.add(key)
            return False

        elif self.mode == OrchestrationMode.COLLABORATIVE:
            if self.is_major_transition(f, t):
                self.pending_approvals.add(key)
                return False
            return True

        elif self.mode == OrchestrationMode.AUTONOMOUS:
            return True

        return True

    def approve_transition(self, from_state: str, to_state: str) -> bool:
        """
        Manually approve a pending transition.
        
        Returns:
            bool: True if a pending transition was found and approved, False otherwise.
        """
        f = from_state.strip().lower()
        t = to_state.strip().lower()
        key = (f, t)
        
        if key in self.pending_approvals:
            self.pending_approvals.remove(key)
            return True
        return False

    def is_pending(self, from_state: str, to_state: str) -> bool:
        """Check if a transition is currently pending approval."""
        f = from_state.strip().lower()
        t = to_state.strip().lower()
        return (f, t) in self.pending_approvals
