"""
prismatic/state_machine.py — 7-Step Iterative Loop State Machine
=================================================================

The core execution state machine for the Prismatic Engine. Each issue
moves through seven canonical states from decomposition to integration.
This is the implementation of Epic 6: 7-Step Iterative Loop & Mode Switch.

States
------
1. **DECOMPOSE** — Break task into subtasks, identify dependencies
2. **DISPATCH**  — Route to appropriate agent via label assignment
3. **EXECUTE**   — Agent carries out the work
4. **REVIEW**    — Peer/automated review of the work
5. **FEEDBACK**  — Deliver review findings back to origin agent
6. **REFINE**    — Origin agent applies feedback
7. **INTEGRATE** — Merge, mark done, update registry

Usage
-----
    from prismatic.state_machine import PipelineStateMachine, Step

    sm = PipelineStateMachine(issue_id="GRO-1234")
    sm.transition(Step.DECOMPOSE)
    sm.transition(Step.DISPATCH, agent="fred")
    print(sm.current_step)  # Step.DISPATCH
    print(sm.history)       # List of {step, timestamp, metadata}
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════
# Step Enumeration
# ═══════════════════════════════════════════════════════════════

class Step(Enum):
    """The seven canonical pipeline steps."""

    DECOMPOSE = "decompose"
    DISPATCH = "dispatch"
    EXECUTE = "execute"
    REVIEW = "review"
    FEEDBACK = "feedback"
    REFINE = "refine"
    INTEGRATE = "integrate"

    # Pseudo-states for lifecycle tracking
    CREATED = "created"
    FAILED = "failed"
    COMPLETED = "completed"

    def __str__(self) -> str:
        return self.value

    def display_name(self) -> str:
        """Human-readable name for UI/logging."""
        return self.value.capitalize()

    @property
    def order(self) -> int:
        """Numeric position in the 7-step chain (0-indexed)."""
        _order = {
            Step.CREATED: -1,
            Step.DECOMPOSE: 0,
            Step.DISPATCH: 1,
            Step.EXECUTE: 2,
            Step.REVIEW: 3,
            Step.FEEDBACK: 4,
            Step.REFINE: 5,
            Step.INTEGRATE: 6,
            Step.COMPLETED: 7,
            Step.FAILED: -2,
        }
        return _order.get(self, -1)


# ═══════════════════════════════════════════════════════════════
# Orchestration Mode (imported from mode_switch, defined here
# as a lightweight enum for the state machine's use)
# ═══════════════════════════════════════════════════════════════

class OrchestrationMode(Enum):
    """Execution mode for the pipeline.

    Controls how much human involvement is required at each step.
    Full definitions are in ``prismatic.mode_switch``.
    """

    INTERACTIVE = "interactive"      # Full human approval at each gate
    COLLABORATIVE = "collaborative"  # Agents execute, flag for review
    AUTONOMOUS = "autonomous"        # Full auto-pilot, escalate on failure

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, s: str) -> "OrchestrationMode":
        """Parse from string, defaulting to COLLABORATIVE."""
        try:
            return cls(s.lower())
        except ValueError:
            return cls.COLLABORATIVE


# ═══════════════════════════════════════════════════════════════
# Transition Event dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class TransitionEvent:
    """Record of a single state transition."""

    from_step: Step | None  # None for initial transition
    to_step: Step
    timestamp: str
    agent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def step_name(self) -> str:
        return self.to_step.value


# ═══════════════════════════════════════════════════════════════
# State Machine
# ═══════════════════════════════════════════════════════════════

# Valid forward transitions (strict 7-step chain)
_STEP_FLOW: dict[Step, Step] = {
    Step.DECOMPOSE: Step.DISPATCH,
    Step.DISPATCH: Step.EXECUTE,
    Step.EXECUTE: Step.REVIEW,
    Step.REVIEW: Step.FEEDBACK,
    Step.FEEDBACK: Step.REFINE,
    Step.REFINE: Step.INTEGRATE,
    Step.INTEGRATE: Step.COMPLETED,
    Step.CREATED: Step.DECOMPOSE,
}

# Steps that are allowed to transition to FAILED from
_FAILABLE_STEPS: set[Step] = {
    Step.DECOMPOSE,
    Step.DISPATCH,
    Step.EXECUTE,
    Step.REVIEW,
    Step.FEEDBACK,
    Step.REFINE,
    Step.INTEGRATE,
}

# Review loop: after REVIEW→FEEDBACK→REFINE, if more changes needed,
# loop back to REVIEW instead of INTEGRATE
_REFINE_REVIEW_LOOP: bool = True  # Can be toggled per pipeline


class PipelineStateMachine:
    """7-step iterative loop state machine for a single issue/pipeline run.

    Tracks an issue through: Decompose → Dispatch → Execute → Review →
    Feedback → Refine → Integrate → Complete.

    Supports review loops (Refine → Review) and failure transitions from
    any step. Thread-safe via internal lock.
    """

    # ── Persistence ──────────────────────────────────────────────

    DEFAULT_STORE: str = os.path.join(
        os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
        "pipelines",
    )

    def __init__(
        self,
        issue_id: str,
        mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE,
        store_dir: str | None = None,
        allow_review_loop: bool = True,
    ):
        """Initialize the state machine for an issue.

        Args:
            issue_id: Linear issue identifier (e.g. ``"GRO-1234"``).
            mode: Orchestration mode (default: COLLABORATIVE).
            store_dir: Directory for persistent state files.
                Defaults to ``<PRISMATIC_STATE_DIR>/pipelines/``.
            allow_review_loop: If ``True``, Refine may loop back to
                Review for additional iterations.
        """
        if isinstance(mode, str):
            mode = OrchestrationMode.from_string(mode)

        self.issue_id = issue_id
        self.mode = mode
        self.allow_review_loop = allow_review_loop

        # State
        self.current_step: Step = Step.CREATED
        self.history: list[TransitionEvent] = []
        self._review_cycles: int = 0
        self._lock = threading.RLock()

        # Persistence
        self._store_dir = store_dir or self.DEFAULT_STORE
        os.makedirs(self._store_dir, exist_ok=True)
        self._store_path = os.path.join(
            self._store_dir, f"{issue_id.replace('/', '_')}.json"
        )

        # Restore from disk if exists
        self._load()

    # ── Public API ────────────────────────────────────────────

    def transition(
        self,
        to_step: Step | str,
        agent: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TransitionEvent:
        """Transition the state machine to *to_step*.

        Validates that the transition is legal per the 7-step chain.
        If the step is the same as current, returns a no-op event.

        Args:
            to_step: Target step (Step enum or string name).
            agent: Agent performing the transition.
            metadata: Optional key-value metadata (e.g. ``{"reason": "..."}``).

        Returns:
            The TransitionEvent recording the transition.

        Raises:
            ValueError: If the transition is invalid.
        """
        if isinstance(to_step, str):
            to_step = Step(to_step.lower())

        with self._lock:
            event = self._do_transition(to_step, agent, metadata or {})
            self._persist()
            return event

    def can_transition(self, to_step: Step | str) -> bool:
        """Check if a transition is valid without executing it."""
        if isinstance(to_step, str):
            try:
                to_step = Step(to_step.lower())
            except ValueError:
                return False
        return self._validate_transition(to_step) is not None

    def next_step(self) -> Step | None:
        """Return the next expected step in the chain, or None if complete."""
        if self.current_step == Step.COMPLETED:
            return None
        if self.current_step == Step.FAILED:
            return None
        return _STEP_FLOW.get(self.current_step)

    def is_complete(self) -> bool:
        """Return True if the pipeline has reached COMPLETED."""
        return self.current_step == Step.COMPLETED

    def is_failed(self) -> bool:
        """Return True if the pipeline has reached FAILED."""
        return self.current_step == Step.FAILED

    def is_terminal(self) -> bool:
        """Return True if the pipeline is in a terminal state."""
        return self.current_step in (Step.COMPLETED, Step.FAILED)

    def fail(self, reason: str = "", agent: str = "") -> TransitionEvent:
        """Transition the state machine to FAILED.

        Args:
            reason: Human-readable failure reason.
            agent: Agent reporting the failure.

        Returns:
            The TransitionEvent.
        """
        return self.transition(
            Step.FAILED,
            agent=agent,
            metadata={"reason": reason},
        )

    def advance(
        self,
        agent: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TransitionEvent | None:
        """Advance to the next step in the canonical chain.

        Returns:
            TransitionEvent, or None if already complete.
        """
        nxt = self.next_step()
        if nxt is None:
            return None
        return self.transition(nxt, agent=agent, metadata=metadata or {})

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the current state."""
        with self._lock:
            return {
                "issue_id": self.issue_id,
                "mode": self.mode.value,
                "current_step": self.current_step.value,
                "review_cycles": self._review_cycles,
                "is_terminal": self.is_terminal(),
                "history": [
                    {
                        "from_step": e.from_step.value if e.from_step else None,
                        "to_step": e.to_step.value,
                        "timestamp": e.timestamp,
                        "agent": e.agent,
                        "metadata": e.metadata,
                    }
                    for e in self.history
                ],
            }

    def progress_pct(self) -> float:
        """Return pipeline progress as a percentage (0-100)."""
        if self.is_complete():
            return 100.0
        if self.is_failed():
            return -1.0
        if self.current_step == Step.CREATED:
            return 0.0
        return (self.current_step.order / 6.0) * 100.0

    # ── Internal ──────────────────────────────────────────────

    def _validate_transition(self, to_step: Step) -> Step | None:
        """Check if the transition is valid. Returns the expected step if valid."""
        current = self.current_step

        # Same step is always OK (no-op)
        if to_step == current:
            return to_step

        # Terminal states cannot be left
        if current in (Step.COMPLETED, Step.FAILED):
            return None

        # FAILED can transition from any non-terminal step
        if to_step == Step.FAILED:
            return to_step

        # COMPLETED only from INTEGRATE
        if to_step == Step.COMPLETED:
            return to_step if current == Step.INTEGRATE else None

        # CREATED → DECOMPOSE is always valid
        if current == Step.CREATED and to_step == Step.DECOMPOSE:
            return to_step

        # Standard forward progression
        expected = _STEP_FLOW.get(current)
        if expected == to_step:
            return to_step

        # Review loop: REFINE → REVIEW (if enabled)
        if self.allow_review_loop and current == Step.REFINE and to_step == Step.REVIEW:
            return to_step

        return None

    def _do_transition(
        self,
        to_step: Step,
        agent: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TransitionEvent:
        """Execute a validated transition (caller must hold lock)."""
        metadata = metadata or {}

        validated = self._validate_transition(to_step)
        if validated is None:
            allowed = []
            nxt = self.next_step()
            if nxt:
                allowed.append(nxt.value)
            if self.current_step not in (Step.COMPLETED, Step.FAILED):
                allowed.append("failed")
            if self.allow_review_loop and self.current_step == Step.REFINE:
                allowed.append(Step.REVIEW.value)
            raise ValueError(
                f"Invalid transition: {self.current_step.value} → {to_step.value}. "
                f"Allowed: {', '.join(allowed)}"
            )

        # Track review cycles
        if to_step == Step.REVIEW and self.current_step == Step.REFINE:
            self._review_cycles += 1
            metadata["review_cycle"] = self._review_cycles

        event = TransitionEvent(
            from_step=self.current_step,
            to_step=to_step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            metadata=metadata,
        )

        self.history.append(event)
        self.current_step = to_step
        return event

    # ── Persistence ───────────────────────────────────────────

    def _persist(self) -> None:
        """Write current state to disk."""
        try:
            data = self.snapshot()
            with open(self._store_path, "w") as f:
                json.dump(data, f, indent=2)
        except (OSError, IOError) as exc:
            # Best-effort persistence — log but don't crash
            print(f"[state_machine] Failed to persist {self.issue_id}: {exc}")

    def _load(self) -> None:
        """Restore state from disk if available."""
        if not os.path.exists(self._store_path):
            return
        try:
            with open(self._store_path) as f:
                data = json.load(f)

            # Restore mode
            if "mode" in data:
                self.mode = OrchestrationMode.from_string(data["mode"])

            # Restore current step
            if "current_step" in data:
                try:
                    self.current_step = Step(data["current_step"])
                except ValueError:
                    pass  # Keep default if step is unknown

            # Restore review cycles
            self._review_cycles = data.get("review_cycles", 0)

            # Rebuild history
            self.history.clear()
            for h in data.get("history", []):
                from_step = Step(h["from_step"]) if h.get("from_step") else None
                to_step = Step(h["to_step"])
                self.history.append(
                    TransitionEvent(
                        from_step=from_step,
                        to_step=to_step,
                        timestamp=h.get("timestamp", ""),
                        agent=h.get("agent", ""),
                        metadata=h.get("metadata", {}),
                    )
                )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            print(f"[state_machine] Failed to load {self.issue_id}: {exc}")


# ═══════════════════════════════════════════════════════════════
# State Machine Registry
# ═══════════════════════════════════════════════════════════════

class StateMachineRegistry:
    """Thread-safe registry of active pipeline state machines.

    Provides lookup by issue ID and automatic lazy initialization.
    """

    def __init__(self, store_dir: str | None = None):
        self._machines: dict[str, PipelineStateMachine] = {}
        self._lock = threading.RLock()
        self._store_dir = store_dir or PipelineStateMachine.DEFAULT_STORE

    def get(
        self,
        issue_id: str,
        mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE,
    ) -> PipelineStateMachine:
        """Get or create a state machine for *issue_id*.

        Returns an existing instance if one was previously created in this
        process, otherwise creates a new one (which may restore from disk).
        """
        with self._lock:
            if issue_id not in self._machines:
                self._machines[issue_id] = PipelineStateMachine(
                    issue_id=issue_id,
                    mode=mode,
                    store_dir=self._store_dir,
                )
            return self._machines[issue_id]

    def remove(self, issue_id: str) -> bool:
        """Remove a completed state machine from the in-memory registry.

        Does NOT delete the on-disk state file (preserves history).
        """
        with self._lock:
            if issue_id in self._machines:
                del self._machines[issue_id]
                return True
            return False

    def list_active(self) -> list[str]:
        """Return issue IDs for all non-terminal state machines."""
        with self._lock:
            return [
                iid
                for iid, sm in self._machines.items()
                if not sm.is_terminal()
            ]

    def list_all(self) -> list[str]:
        """Return all registered issue IDs."""
        with self._lock:
            return list(self._machines.keys())

    def clear_completed(self) -> int:
        """Remove completed/failed machines from the registry. Returns count."""
        with self._lock:
            terminal = [
                iid for iid, sm in self._machines.items() if sm.is_terminal()
            ]
            for iid in terminal:
                del self._machines[iid]
            return len(terminal)


# ═══════════════════════════════════════════════════════════════
# Convenience: Auto-advancing dispatcher helper
# ═══════════════════════════════════════════════════════════════

def build_pipeline_chain(
    issue_id: str,
    mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE,
) -> PipelineStateMachine:
    """Create a state machine and auto-advance to DECOMPOSE.

    This is the standard entry point when a new pipeline issue is created.
    """
    sm = PipelineStateMachine(issue_id=issue_id, mode=mode)
    sm.transition(Step.DECOMPOSE)
    return sm


def step_for_agent_label(label: str) -> Step | None:
    """Map an agent label (e.g. ``"agent:fred"``) to the pipeline step it represents.

    Agent label → pipeline step mapping:
        ``agent:fred``   → DECOMPOSE (orchestrator)
        ``agent:kai``    → DISPATCH (reviewer/router)
        ``agent:agy``    → EXECUTE (builder)
        ``agent:jules``  → REVIEW (tester/QA)
        ``agent:codex``  → REFINE (polisher)
        ``agent:done``   → INTEGRATE (terminal)
    """
    label_map: dict[str, Step] = {
        "agent:fred": Step.DECOMPOSE,
        "agent:kai": Step.DISPATCH,
        "agent:agy": Step.EXECUTE,
        "agent:jules": Step.REVIEW,
        "agent:codex": Step.REFINE,
        "agent:done": Step.INTEGRATE,
        # Alternate label formats
        "agent::fred": Step.DECOMPOSE,
        "agent::kai": Step.DISPATCH,
        "agent::agy": Step.EXECUTE,
        "agent::jules": Step.REVIEW,
        "agent::codex": Step.REFINE,
        "agent::done": Step.INTEGRATE,
    }
    return label_map.get(label.strip())


def agent_for_step(step: Step) -> str:
    """Map a pipeline step to the agent label that handles it."""
    step_map: dict[Step, str] = {
        Step.DECOMPOSE: "agent:fred",
        Step.DISPATCH: "agent:kai",
        Step.EXECUTE: "agent:agy",
        Step.REVIEW: "agent:jules",
        Step.FEEDBACK: "agent:fred",   # Feedback routed back to orchestrator
        Step.REFINE: "agent:codex",
        Step.INTEGRATE: "agent:done",
    }
    return step_map.get(step, "agent:fred")
