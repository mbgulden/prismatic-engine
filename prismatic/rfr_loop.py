"""
prismatic/rfr_loop.py — Review-Feedback-Refine Loop Engine
===========================================================

The RFR Loop Engine sits between the EXECUTE and INTEGRATE steps of the
7-step pipeline. It implements the Review → Feedback → Refine cycle:

1. **REVIEW**   — Send executed output to AGY for automated review
2. **FEEDBACK** — Parse AGY review into structured feedback items
3. **REFINE**   — Loop back for refinement if quality gates not met

The loop repeats until quality gates pass or max retries are exceeded.
If max retries exceeded, the issue is escalated per the active ModePolicy.

Usage
-----
    from prismatic.rfr_loop import ReviewFeedbackRefineLoop
    from prismatic.state_machine import PipelineStateMachine, Step

    sm = PipelineStateMachine(issue_id="GRO-1234")
    loop = ReviewFeedbackRefineLoop(mode_switch)

    result = loop.run_full_cycle(
        issue_id="GRO-1234",
        execution_output="<code changes or content>",
        state_machine=sm,
    )

    if result.passed:
        sm.transition(Step.INTEGRATE)
    else:
        sm.fail(reason=result.escalation_reason)

Integration Points
------------------
- **AGY review integration**: Sends output to AGY for analysis via
  ``agy_live_parser`` or direct Claude/Gemini API call.
- **Quality gates**: Validates output against mode-specific thresholds
  defined in ``ModePolicy``.
- **Escalation**: Routes to ``agent:fred`` when max retries hit.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .state_machine import PipelineStateMachine, Step, TransitionEvent
from .mode_switch import ModeSwitch, OrchestrationMode, ModePolicy


# ═══════════════════════════════════════════════════════════════
# Review Severity
# ═══════════════════════════════════════════════════════════════

class ReviewSeverity(Enum):
    """Severity level for feedback items."""

    BLOCKER = "blocker"      # Must fix — blocks integration
    MAJOR = "major"           # Should fix — quality concern
    MINOR = "minor"           # Nice to fix — cosmetic
    SUGGESTION = "suggestion" # Optional improvement
    PASS = "pass"             # No issues found

    @property
    def is_blocking(self) -> bool:
        """Return True if this severity blocks pipeline progression."""
        return self in (ReviewSeverity.BLOCKER, ReviewSeverity.MAJOR)


# ═══════════════════════════════════════════════════════════════
# Review Verdict
# ═══════════════════════════════════════════════════════════════

class ReviewVerdict(Enum):
    """Overall review verdict for a pipeline cycle."""

    APPROVED = "approved"
    NEEDS_CHANGES = "needs_changes"
    NEEDS_REWORK = "needs_rework"
    BLOCKED = "blocked"


# ═══════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class FeedbackItem:
    """A single piece of feedback from review."""

    severity: ReviewSeverity
    message: str
    category: str = ""          # e.g. "security", "style", "logic"
    file_path: str = ""          # Target file, if applicable
    line_range: str = ""         # e.g. "L42-L65"
    suggestion: str = ""         # Suggested fix
    resolved: bool = False       # Whether it was addressed in refinement


@dataclass
class ReviewResult:
    """Complete review output for a single review cycle."""

    verdict: ReviewVerdict
    feedback_items: list[FeedbackItem] = field(default_factory=list)
    summary: str = ""
    reviewer: str = ""           # Agent that performed review (e.g. "agy")
    cycle_number: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocker_count(self) -> int:
        """Count of blocker-level feedback items."""
        return sum(1 for f in self.feedback_items if f.severity == ReviewSeverity.BLOCKER)

    @property
    def major_count(self) -> int:
        """Count of major-level feedback items."""
        return sum(1 for f in self.feedback_items if f.severity == ReviewSeverity.MAJOR)

    @property
    def has_blocking_feedback(self) -> bool:
        """Return True if there are blocking issues."""
        return any(f.severity.is_blocking for f in self.feedback_items)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""
        return {
            "verdict": self.verdict.value,
            "feedback_items": [
                {
                    "severity": f.severity.value,
                    "message": f.message,
                    "category": f.category,
                    "file_path": f.file_path,
                    "line_range": f.line_range,
                    "suggestion": f.suggestion,
                    "resolved": f.resolved,
                }
                for f in self.feedback_items
            ],
            "summary": self.summary,
            "reviewer": self.reviewer,
            "cycle_number": self.cycle_number,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class RFRCycleResult:
    """Result of a complete RFR cycle (Review → Feedback → Refine)."""

    issue_id: str
    passed: bool                   # True if quality gates satisfied
    cycles_completed: int
    final_verdict: ReviewVerdict
    all_reviews: list[ReviewResult] = field(default_factory=list)
    refinement_attempts: list[str] = field(default_factory=list)
    escalation_reason: str = ""
    completion_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ═══════════════════════════════════════════════════════════════
# Quality Gates
# ═══════════════════════════════════════════════════════════════

class QualityGate:
    """Evaluates whether review output passes quality thresholds.

    Quality gates are mode-aware: AUTONOMOUS mode has stricter gates
    than INTERACTIVE mode (since no human is reviewing).
    """

    def __init__(
        self,
        policy: ModePolicy | None = None,
        mode: OrchestrationMode | None = None,
    ):
        """
        Args:
            policy: The active ModePolicy. If None, defaults to
                COLLABORATIVE policy thresholds.
            mode: The active OrchestrationMode. If None, defaults to
                COLLABORATIVE.
        """
        if policy is None:
            from .mode_switch import COLLABORATIVE_POLICY
            policy = COLLABORATIVE_POLICY
        self._policy = policy
        self._mode = mode or OrchestrationMode.COLLABORATIVE

    def evaluate(self, review: ReviewResult) -> ReviewVerdict:
        """Evaluate a review result against quality thresholds.

        Returns:
            ReviewVerdict indicating pass/fail/needs-changes.
        """
        # No feedback items = clean pass
        if not review.feedback_items:
            return ReviewVerdict.APPROVED

        # Blocker count threshold — even 1 blocker = needs rework
        if review.blocker_count > 0:
            return ReviewVerdict.NEEDS_REWORK

        # Major count threshold — depends on mode
        major_threshold = self._get_major_threshold()
        if review.major_count > major_threshold:
            return ReviewVerdict.NEEDS_CHANGES

        # Minor + suggestion = approved with notes
        if any(
            f.severity in (ReviewSeverity.MINOR, ReviewSeverity.SUGGESTION)
            for f in review.feedback_items
        ):
            return ReviewVerdict.APPROVED

        return ReviewVerdict.APPROVED

    def _get_major_threshold(self) -> int:
        """Get the max allowable MAJOR issues for the current mode."""
        threshold_map = {
            OrchestrationMode.AUTONOMOUS: 0,     # Zero tolerance
            OrchestrationMode.COLLABORATIVE: 1,   # One major issue OK
            OrchestrationMode.INTERACTIVE: 3,     # Human will catch it
        }
        return threshold_map.get(self._mode, 1)

    def should_escalate(self, cycle_number: int) -> bool:
        """Determine if the current cycle count warrants escalation."""
        return cycle_number >= self._policy.max_retries

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of gate configuration."""
        return {
            "major_threshold": self._get_major_threshold(),
            "max_retries": self._policy.max_retries,
            "escalation_agent": self._policy.escalation_agent,
        }


# ═══════════════════════════════════════════════════════════════
# Feedback Parser
# ═══════════════════════════════════════════════════════════════

class FeedbackParser:
    """Parses raw AGY review output into structured FeedbackItems.

    Supports multiple AGY output formats:
    - JSON structured review
    - Markdown review with severity headers
    - Plain text with keyword detection
    """

    # Keywords that signal different severity levels in plain text
    _SEVERITY_PATTERNS: dict[str, ReviewSeverity] = {
        "BLOCKER": ReviewSeverity.BLOCKER,
        "CRITICAL": ReviewSeverity.BLOCKER,
        "MUST FIX": ReviewSeverity.BLOCKER,
        "MAJOR": ReviewSeverity.MAJOR,
        "BUG": ReviewSeverity.MAJOR,
        "SECURITY": ReviewSeverity.MAJOR,
        "MINOR": ReviewSeverity.MINOR,
        "NIT": ReviewSeverity.MINOR,
        "SUGGESTION": ReviewSeverity.SUGGESTION,
        "NICE TO HAVE": ReviewSeverity.SUGGESTION,
        "OPTIONAL": ReviewSeverity.SUGGESTION,
    }

    def parse(self, raw_output: str) -> list[FeedbackItem]:
        """Parse raw review output into feedback items.

        Attempts JSON parsing first, then falls back to markdown/text parsing.

        Args:
            raw_output: Raw review output from AGY.

        Returns:
            List of structured FeedbackItems.
        """
        # Try JSON first
        items = self._try_parse_json(raw_output)
        if items:
            return items

        # Fall back to markdown/text parsing
        return self._parse_markdown(raw_output)

    def _try_parse_json(self, raw: str) -> list[FeedbackItem]:
        """Attempt to parse JSON review output."""
        try:
            # AGY may wrap JSON in markdown code blocks
            if "```json" in raw:
                start = raw.index("```json") + 7
                end = raw.index("```", start)
                raw = raw[start:end].strip()
            elif "```" in raw:
                start = raw.index("```") + 3
                end = raw.index("```", start)
                raw = raw[start:end].strip()

            data = json.loads(raw)

            # Support both list and dict formats
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = data.get("feedback", data.get("items", data.get("findings", [])))
            else:
                return []

            items = []
            for entry in entries:
                sev_str = str(entry.get("severity", "minor")).upper()
                sev = self._SEVERITY_PATTERNS.get(sev_str, ReviewSeverity.MINOR)
                items.append(FeedbackItem(
                    severity=sev,
                    message=str(entry.get("message", entry.get("description", ""))),
                    category=str(entry.get("category", "")),
                    file_path=str(entry.get("file", entry.get("file_path", ""))),
                    line_range=str(entry.get("line_range", entry.get("lines", ""))),
                    suggestion=str(entry.get("suggestion", entry.get("fix", ""))),
                ))
            return items

        except (json.JSONDecodeError, ValueError, KeyError):
            return []

    def _parse_markdown(self, raw: str) -> list[FeedbackItem]:
        """Parse markdown or plain-text review output."""
        items = []
        lines = raw.split("\n")
        current_severity = ReviewSeverity.MINOR
        current_message: list[str] = []
        current_category = ""

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current_message:
                    items.append(FeedbackItem(
                        severity=current_severity,
                        message=" ".join(current_message),
                        category=current_category,
                    ))
                    current_message = []
                continue

            # Check for severity headers: ### BLOCKER: ...
            found_severity = False
            for keyword, sev in self._SEVERITY_PATTERNS.items():
                if keyword in stripped.upper():
                    if current_message:
                        items.append(FeedbackItem(
                            severity=current_severity,
                            message=" ".join(current_message),
                            category=current_category,
                        ))
                        current_message = []
                    current_severity = sev
                    # Extract message after the keyword
                    remainder = stripped.split(keyword, 1)[-1].strip(": -")
                    if remainder:
                        current_message.append(remainder)
                    found_severity = True
                    break

            if not found_severity:
                # Regular feedback line
                if stripped.startswith("- ") or stripped.startswith("* "):
                    stripped = stripped[2:]
                current_message.append(stripped)

        # Flush last item
        if current_message:
            items.append(FeedbackItem(
                severity=current_severity,
                message=" ".join(current_message),
                category=current_category,
            ))

        return items


# ═══════════════════════════════════════════════════════════════
# Review Agent Interface (AGY Integration)
# ═══════════════════════════════════════════════════════════════

class ReviewAgent:
    """Interface for performing automated review via AGY or other reviewers.

    This is the integration point for AGY. In production, this delegates to
    AGY via the agy_live_parser or direct API call. In test/stub mode, it
    returns simulated reviews.
    """

    def __init__(self, reviewer_label: str = "agy", store_dir: str | None = None):
        """
        Args:
            reviewer_label: Agent performing review (default: "agy").
            store_dir: Directory for review persistence.
        """
        self.reviewer_label = reviewer_label
        self._store_dir = store_dir or os.path.join(
            os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
            "reviews",
        )
        os.makedirs(self._store_dir, exist_ok=True)
        self._parser = FeedbackParser()

    def review(
        self,
        issue_id: str,
        execution_output: str,
        cycle_number: int = 1,
        context: dict[str, Any] | None = None,
    ) -> ReviewResult:
        """Perform review of *execution_output*.

        Args:
            issue_id: Linear issue identifier.
            execution_output: The output from the EXECUTE phase.
            cycle_number: Which review cycle this is (1-based).
            context: Additional context (prior reviews, refinement notes).

        Returns:
            A ReviewResult with verdict and feedback items.
        """
        # In production, this would call AGY via agy_live_parser.
        # For now, perform a local heuristic review as a fallback.
        # The self_review method provides basic quality checks
        # until full AGY integration is wired.
        return self._local_review(issue_id, execution_output, cycle_number, context or {})

    def _local_review(
        self,
        issue_id: str,
        execution_output: str,
        cycle_number: int,
        context: dict[str, Any],
    ) -> ReviewResult:
        """Perform a local heuristic review (AGY stub/fallback)."""
        items: list[FeedbackItem] = []

        # Basic quality checks
        if not execution_output or len(execution_output.strip()) < 10:
            items.append(FeedbackItem(
                severity=ReviewSeverity.BLOCKER,
                message="Execution output is too short or empty",
                category="completeness",
            ))

        # Check for common issues
        if "TODO" in execution_output:
            items.append(FeedbackItem(
                severity=ReviewSeverity.MAJOR,
                message="Output contains unresolved TODO markers",
                category="completeness",
            ))

        if "FIXME" in execution_output:
            items.append(FeedbackItem(
                severity=ReviewSeverity.MAJOR,
                message="Output contains FIXME markers",
                category="completeness",
            ))

        if execution_output.count("print(") > 5:
            items.append(FeedbackItem(
                severity=ReviewSeverity.MINOR,
                message="Output may contain debug print statements",
                category="code_quality",
            ))

        if "import pdb" in execution_output or "breakpoint()" in execution_output:
            items.append(FeedbackItem(
                severity=ReviewSeverity.BLOCKER,
                message="Output contains debugger breakpoints",
                category="security",
            ))

        # Check for hardcoded absolute home paths
        home = os.environ.get("HOME", "")
        if home and home in execution_output:
            items.append(FeedbackItem(
                severity=ReviewSeverity.BLOCKER,
                message="Output contains hardcoded absolute home paths",
                category="portability",
            ))

        # Determine verdict
        blockers = sum(1 for f in items if f.severity == ReviewSeverity.BLOCKER)
        majors = sum(1 for f in items if f.severity == ReviewSeverity.MAJOR)

        if blockers > 0:
            verdict = ReviewVerdict.NEEDS_REWORK
            summary = f"Found {blockers} blocker(s) and {majors} major issue(s)"
        elif majors > 0:
            verdict = ReviewVerdict.NEEDS_CHANGES
            summary = f"Found {majors} major issue(s)"
        elif items:
            verdict = ReviewVerdict.APPROVED
            summary = f"Found {len(items)} minor issue(s) — approved with notes"
        else:
            verdict = ReviewVerdict.APPROVED
            summary = "No issues found — clean review"

        result = ReviewResult(
            verdict=verdict,
            feedback_items=items,
            summary=summary,
            reviewer=self.reviewer_label,
            cycle_number=cycle_number,
        )

        # Persist review
        self._save_review(issue_id, cycle_number, result)
        return result

    def _save_review(self, issue_id: str, cycle_number: int, result: ReviewResult) -> None:
        """Persist review result to disk."""
        try:
            path = os.path.join(
                self._store_dir,
                f"{issue_id.replace('/', '_')}_cycle{cycle_number}.json",
            )
            with open(path, "w") as f:
                json.dump(result.snapshot(), f, indent=2)
        except (OSError, IOError) as exc:
            print(f"[rfr_loop] Failed to persist review {issue_id}: {exc}")

    def load_review(self, issue_id: str, cycle_number: int) -> ReviewResult | None:
        """Load a previously persisted review."""
        path = os.path.join(
            self._store_dir,
            f"{issue_id.replace('/', '_')}_cycle{cycle_number}.json",
        )
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            items = [
                FeedbackItem(
                    severity=ReviewSeverity(f["severity"]),
                    message=f["message"],
                    category=f.get("category", ""),
                    file_path=f.get("file_path", ""),
                    line_range=f.get("line_range", ""),
                    suggestion=f.get("suggestion", ""),
                    resolved=f.get("resolved", False),
                )
                for f in data.get("feedback_items", [])
            ]
            return ReviewResult(
                verdict=ReviewVerdict(data["verdict"]),
                feedback_items=items,
                summary=data.get("summary", ""),
                reviewer=data.get("reviewer", ""),
                cycle_number=data.get("cycle_number", 0),
                timestamp=data.get("timestamp", ""),
                metadata=data.get("metadata", {}),
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            print(f"[rfr_loop] Failed to load review {issue_id}: {exc}")
            return None


# ═══════════════════════════════════════════════════════════════
# RFR Loop Engine
# ═══════════════════════════════════════════════════════════════

class ReviewFeedbackRefineLoop:
    """Orchestrates the Review → Feedback → Refine cycle.

    Integrates with the PipelineStateMachine, ModeSwitch, and ReviewAgent
    to run iterative review cycles with quality gates and retry limits.

    Usage::

        rfr = ReviewFeedbackRefineLoop(mode_switch)
        result = rfr.run_full_cycle(
            issue_id="GRO-1234",
            execution_output=output_text,
            state_machine=sm,
        )
        if result.passed:
            sm.advance()  # → INTEGRATE
        else:
            sm.fail(reason=result.escalation_reason)
    """

    def __init__(
        self,
        mode_switch: ModeSwitch | None = None,
        review_agent: ReviewAgent | None = None,
    ):
        """
        Args:
            mode_switch: Active mode switch for policy decisions.
                If None, uses the global singleton.
            review_agent: Review agent for performing reviews.
                If None, creates a default ReviewAgent.
        """
        from .mode_switch import get_mode_switch

        self._mode_switch = mode_switch or get_mode_switch()
        self._review_agent = review_agent or ReviewAgent()
        self._quality_gate = QualityGate(
            policy=self._mode_switch.policy,
            mode=self._mode_switch.mode,
        )
        self._parser = FeedbackParser()
        self._lock = threading.RLock()

    # ── Public API ────────────────────────────────────────────

    def run_full_cycle(
        self,
        issue_id: str,
        execution_output: str,
        state_machine: PipelineStateMachine | None = None,
        context: dict[str, Any] | None = None,
    ) -> RFRCycleResult:
        """Run the full Review-Feedback-Refine loop until pass or max retries.

        Args:
            issue_id: Linear issue identifier.
            execution_output: Output from the EXECUTE phase.
            state_machine: Optional state machine to update during the loop.
            context: Additional context for the review agent.

        Returns:
            RFRCycleResult with final verdict and cycle details.
        """
        cycle_result = RFRCycleResult(issue_id=issue_id, passed=False, cycles_completed=0, final_verdict=ReviewVerdict.BLOCKED)
        current_output = execution_output
        all_reviews: list[ReviewResult] = []
        refinement_attempts: list[str] = []

        with self._lock:
            for cycle in range(1, self._mode_switch.policy.max_retries + 2):
                # Step 1: REVIEW
                if state_machine and state_machine.current_step != Step.REVIEW:
                    state_machine.transition(Step.REVIEW, agent="agy")

                review = self._review_agent.review(
                    issue_id=issue_id,
                    execution_output=current_output,
                    cycle_number=cycle,
                    context=context,
                )
                all_reviews.append(review)

                # Step 2: FEEDBACK
                if state_machine:
                    state_machine.transition(
                        Step.FEEDBACK,
                        agent="fred",
                        metadata={"cycle": cycle, "verdict": review.verdict.value},
                    )

                # Step 3: Evaluate quality gates
                verdict = self._quality_gate.evaluate(review)

                if verdict == ReviewVerdict.APPROVED:
                    cycle_result.passed = True
                    cycle_result.cycles_completed = cycle
                    cycle_result.final_verdict = verdict
                    cycle_result.all_reviews = all_reviews
                    cycle_result.refinement_attempts = refinement_attempts
                    cycle_result.completion_time = datetime.now(timezone.utc).isoformat()
                    return cycle_result

                # Step 4: Check if we should escalate
                if self._quality_gate.should_escalate(cycle):
                    cycle_result.passed = False
                    cycle_result.cycles_completed = cycle
                    cycle_result.final_verdict = verdict
                    cycle_result.all_reviews = all_reviews
                    cycle_result.refinement_attempts = refinement_attempts
                    cycle_result.escalation_reason = (
                        f"Max retries ({self._mode_switch.policy.max_retries}) exceeded. "
                        f"Last verdict: {verdict.value}. "
                        f"Escalate to: {self._mode_switch.policy.escalation_agent}."
                    )
                    cycle_result.completion_time = datetime.now(timezone.utc).isoformat()
                    return cycle_result

                # Step 5: REFINE — prepare for next iteration
                if state_machine:
                    state_machine.transition(
                        Step.REFINE,
                        agent="codex",
                        metadata={"cycle": cycle, "feedback_count": len(review.feedback_items)},
                    )

                refinement_note = self._build_refinement_context(review)
                refinement_attempts.append(refinement_note)

                # Re-enter REVIEW loop (via state machine's review loop capability)
                if state_machine and state_machine.allow_review_loop:
                    state_machine.transition(
                        Step.REVIEW,
                        agent="rfr",
                        metadata={"review_cycle": cycle},
                    )

            # Should not reach here, but just in case
            cycle_result.passed = False
            cycle_result.cycles_completed = self._mode_switch.policy.max_retries
            cycle_result.all_reviews = all_reviews
            cycle_result.refinement_attempts = refinement_attempts
            cycle_result.escalation_reason = "Loop exhausted all cycles"
            cycle_result.completion_time = datetime.now(timezone.utc).isoformat()
            return cycle_result

    def run_single_cycle(
        self,
        issue_id: str,
        execution_output: str,
        cycle_number: int = 1,
        context: dict[str, Any] | None = None,
    ) -> ReviewResult:
        """Run a single REVIEW step without the full loop.

        Useful when the caller wants to control the loop externally.

        Args:
            issue_id: Linear issue identifier.
            execution_output: Output from EXECUTE phase (or refined output).
            cycle_number: Which review cycle this is.
            context: Additional context.

        Returns:
            ReviewResult with feedback items and verdict.
        """
        return self._review_agent.review(
            issue_id=issue_id,
            execution_output=execution_output,
            cycle_number=cycle_number,
            context=context,
        )

    def evaluate_pass_fail(self, review: ReviewResult) -> ReviewVerdict:
        """Evaluate a single review result against quality gates.

        Args:
            review: Review result to evaluate.

        Returns:
            Quality gate verdict.
        """
        return self._quality_gate.evaluate(review)

    def should_escalate(self, cycle_number: int) -> bool:
        """Check if the given cycle count warrants escalation."""
        return self._quality_gate.should_escalate(cycle_number)

    # ── Internal ──────────────────────────────────────────────

    def _build_refinement_context(self, review: ReviewResult) -> str:
        """Build a refinement context string from review feedback.

        This context is fed to the refinement agent (codex) so it knows
        what to fix in the next iteration.
        """
        lines = [f"## Refinement Context (Cycle {review.cycle_number})"]
        lines.append(f"Verdict: {review.verdict.value}")
        lines.append(f"Summary: {review.summary}")
        lines.append("")

        if review.feedback_items:
            lines.append("### Issues to Address")
            for item in review.feedback_items:
                prefix = {
                    ReviewSeverity.BLOCKER: "🔴",
                    ReviewSeverity.MAJOR: "🟠",
                    ReviewSeverity.MINOR: "🟡",
                    ReviewSeverity.SUGGESTION: "💡",
                    ReviewSeverity.PASS: "✅",
                }.get(item.severity, "•")
                lines.append(f"{prefix} [{item.severity.value.upper()}] {item.message}")
                if item.suggestion:
                    lines.append(f"   → Fix: {item.suggestion}")
                if item.file_path:
                    loc = f" ({item.file_path}"
                    if item.line_range:
                        loc += f" {item.line_range}"
                    loc += ")"
                    lines.append(f"   📁 {loc}")

        lines.append("")
        lines.append("### Action Required")
        lines.append("Address all BLOCKER and MAJOR issues before the next review cycle.")

        return "\n".join(lines)

    # ── Diagnostics ───────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the RFR loop state."""
        return {
            "mode": self._mode_switch.mode.value,
            "quality_gate": self._quality_gate.snapshot(),
            "reviewer": self._review_agent.reviewer_label,
        }


# ═══════════════════════════════════════════════════════════════
# Convenience: Integration with the pipeline dispatcher
# ═══════════════════════════════════════════════════════════════

def create_rfr_loop_for_issue(
    issue_id: str,
    mode: OrchestrationMode | str = OrchestrationMode.COLLABORATIVE,
) -> ReviewFeedbackRefineLoop:
    """Create an RFR loop instance pre-configured for an issue.

    This is the standard entry point for the pipeline dispatcher.
    """
    if isinstance(mode, str):
        mode = OrchestrationMode.from_string(mode)
    switch = ModeSwitch(mode)
    return ReviewFeedbackRefineLoop(mode_switch=switch)


def parse_review_output(raw: str) -> list[FeedbackItem]:
    """Convenience function: parse raw AGY output into feedback items."""
    return FeedbackParser().parse(raw)
