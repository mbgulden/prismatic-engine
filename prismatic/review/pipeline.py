"""Peer-review pipeline orchestrator — Phase 2 / Gap 8.

Wraps the bare :class:`PRReviewResult` from the reviewer (Gap 4) and
turns it into concrete next-step actions for the factory:

1. **Impact classification** — map verdict + severity counts to a single
   impact level (``trivial`` / ``minor`` / ``major`` / ``blocker``).
2. **Action decision** — given the impact and the prior attempt count,
   decide whether to advance, hold for human review, or dispatch rework.
3. **Rework payload** — when rework is the right action, build a dispatch
   payload that the factory can pick up via the standard queue.

The orchestrator is deliberately side-effect-free: it returns a
:class:`PipelineDecision` describing what should happen, plus a
:class:`ReworkPayload` if rework is needed. Callers (the factory's
``trigger_ned_review`` consumer, or a future ``pr_reviewer`` server) wire
the I/O.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 8)
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from .pr_reviewer import (
    APPROVE,
    NEEDS_DISCUSSION,
    PRReviewResult,
    REQUEST_CHANGES,
)


# ─────────────────────────────────────────────────────────────────────
# Impact levels
# ─────────────────────────────────────────────────────────────────────


IMPACT_TRIVIAL = "trivial"
IMPACT_MINOR = "minor"
IMPACT_MAJOR = "major"
IMPACT_BLOCKER = "blocker"

IMPACT_LEVELS = {IMPACT_TRIVIAL, IMPACT_MINOR, IMPACT_MAJOR, IMPACT_BLOCKER}

# Ordered low → high; useful for comparisons.
IMPACT_RANK = {
    IMPACT_TRIVIAL: 0,
    IMPACT_MINOR: 1,
    IMPACT_MAJOR: 2,
    IMPACT_BLOCKER: 3,
}


# ─────────────────────────────────────────────────────────────────────
# Next-action decisions
# ─────────────────────────────────────────────────────────────────────


ACTION_ADVANCE = "advance"  # verdict is APPROVE — move issue to Done
ACTION_HOLD = "hold"  # NEEDS_DISCUSSION — flag for Michael
ACTION_REWORK = "rework"  # REQUEST_CHANGES — dispatch a fix task
ACTION_GIVE_UP = "give_up"  # rework budget exhausted — escalate to Michael

ACTIONS = {ACTION_ADVANCE, ACTION_HOLD, ACTION_REWORK, ACTION_GIVE_UP}


# Maximum times the orchestrator will dispatch rework for the same PR.
# After this many failures, we stop spamming the factory and escalate.
DEFAULT_MAX_REWORK_ATTEMPTS = 2


# ─────────────────────────────────────────────────────────────────────
# Impact classification
# ─────────────────────────────────────────────────────────────────────


def classify_impact(result: PRReviewResult) -> str:
    """Map a PRReviewResult to a single impact level.

    The mapping is intentionally simple — the goal is to give the
    orchestrator a single scalar to compare, not to invent new rules.

    | Verdict          | Critical | High | Warning | Medium | Impact  |
    |------------------|----------|------|---------|--------|---------|
    | APPROVE          | 0        | 0    | 0       | 0      | trivial |
    | NEEDS_DISCUSSION | 0        | 0    | 1+      | 0      | minor   |
    | NEEDS_DISCUSSION | 0        | 0    | 0       | 1+     | minor   |
    | REQUEST_CHANGES  | 0        | 1+   | *       | *      | major   |
    | REQUEST_CHANGES  | 1+       | *    | *       | *      | blocker |
    | REQUEST_CHANGES  | 0        | 0    | 1+      | *      | major   |  # defensive fallback
    | NEEDS_DISCUSSION | 0        | 1+   | *       | *      | major   |
    """
    critical = result.metadata.get("critical_count", 0)
    high = result.metadata.get("high_count", 0)
    warning = result.metadata.get("warning_count", 0)
    medium = _count_medium(result)

    # Critical findings always escalate to blocker regardless of verdict.
    if critical > 0:
        return IMPACT_BLOCKER

    if result.verdict == APPROVE:
        return IMPACT_TRIVIAL

    if result.verdict == REQUEST_CHANGES:
        if high > 0:
            return IMPACT_MAJOR
        # REQUEST_CHANGES with no critical/high is unusual (the reviewer
        # shouldn't return REQUEST_CHANGES for only warnings) but handle it.
        return IMPACT_MAJOR

    # NEEDS_DISCUSSION
    if high > 0:
        return IMPACT_MAJOR
    if warning > 0 or medium > 0:
        return IMPACT_MINOR
    # NEEDS_DISCUSSION with zero findings is the diff-fetch-failed case.
    return IMPACT_MINOR


def _count_medium(result: PRReviewResult) -> int:
    """Count medium-severity findings in the result.

    The real reviewer (Gap 4) doesn't include medium_count in metadata
    by default — only critical/high/warning. This helper scans inline
    comments as a fallback. If the metadata ever grows to include it,
    prefer that.
    """
    if "medium_count" in result.metadata:
        return result.metadata["medium_count"]
    # Fall back to scanning inline_comments. The reviewer writes the
    # finding message into the comment body; severity isn't preserved
    # there, so we approximate by checking the summary string.
    summary = result.summary.lower()
    if "medium" in summary:
        # Best-effort: extract a count from the "N medium-severity" header.
        m = re.search(r"(\d+)\s+medium", summary)
        if m:
            return int(m.group(1))
    return 0


# ─────────────────────────────────────────────────────────────────────
# Action decision
# ─────────────────────────────────────────────────────────────────────


def decide_next_action(
    result: PRReviewResult,
    *,
    rework_attempts: int = 0,
    max_rework_attempts: int = DEFAULT_MAX_REWORK_ATTEMPTS,
) -> str:
    """Decide what to do next given the review result and prior attempts.

    Returns one of ``ACTION_ADVANCE`` / ``ACTION_HOLD`` / ``ACTION_REWORK``
    / ``ACTION_GIVE_UP``.

    Rules:
    - APPROVE → advance (regardless of attempts)
    - REQUEST_CHANGES + rework_attempts < max → rework
    - REQUEST_CHANGES + rework_attempts >= max → give_up
    - NEEDS_DISCUSSION → hold (never auto-dispatches rework)
    """
    if result.verdict == APPROVE:
        return ACTION_ADVANCE

    if result.verdict == REQUEST_CHANGES:
        if rework_attempts < max_rework_attempts:
            return ACTION_REWORK
        return ACTION_GIVE_UP

    if result.verdict == NEEDS_DISCUSSION:
        return ACTION_HOLD

    # Unknown verdict — be conservative.
    return ACTION_HOLD


# ─────────────────────────────────────────────────────────────────────
# Rework payload
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ReworkPayload:
    """Payload for the factory to pick up when dispatching rework.

    The factory's existing dispatch loop (prismatic.dispatcher) consumes
    this directly: it routes the issue back to the original worker with
    the reviewer's findings attached as context.
    """

    issue_identifier: str
    pr_url: str
    verdict: str
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    rework_attempt: int = 1
    max_rework_attempts: int = DEFAULT_MAX_REWORK_ATTEMPTS
    rework_label: str = "agent:rework"


def build_rework_payload(
    issue_identifier: str,
    pr_url: str,
    result: PRReviewResult,
    *,
    rework_attempt: int = 1,
    max_rework_attempts: int = DEFAULT_MAX_REWORK_ATTEMPTS,
) -> ReworkPayload:
    """Build a :class:`ReworkPayload` from a review result.

    The ``findings`` field is a list of dicts (not ``QualityFinding``
    objects) so the payload can be JSON-serialized for the factory queue.
    """
    findings_dicts: list[dict[str, Any]] = []
    for comment in result.inline_comments:
        findings_dicts.append(
            {
                "path": comment.path,
                "line": comment.line,
                "body": comment.body,
            }
        )
    return ReworkPayload(
        issue_identifier=issue_identifier,
        pr_url=pr_url,
        verdict=result.verdict,
        summary=result.summary,
        findings=findings_dicts,
        rework_attempt=rework_attempt,
        max_rework_attempts=max_rework_attempts,
    )


# ─────────────────────────────────────────────────────────────────────
# Pipeline decision
# ─────────────────────────────────────────────────────────────────────


@dataclass
class PipelineDecision:
    """The orchestrator's verdict on what should happen next.

    Combines the impact classification + action decision + (optional)
    rework payload into a single return value for the caller.
    """

    identifier: str
    verdict: str  # original PRReviewResult.verdict
    impact: str  # one of IMPACT_LEVELS
    action: str  # one of ACTIONS
    rework_payload: ReworkPayload | None = None
    rationale: str = ""  # human-readable explanation
    metadata: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────


class PipelineOrchestrator:
    """Coordinates the impact-classify → action-decide → rework-dispatch loop.

    State is held in instance attributes so the same orchestrator can
    process multiple issues sequentially (the factory's normal pattern).

    Thread safety:
        The orchestrator is thread-safe. ``process()`` holds an internal
        ``threading.Lock`` across the full read-modify-write sequence
        (read counter → decide → conditionally bump counter), so
        concurrent calls with the same identifier cannot both dispatch
        rework at the same counter snapshot. The ``attempts_for``,
        ``record_rework``, and ``reset`` helpers are also independently
        thread-safe for callers that bypass ``process()``.

    Example::

        orch = PipelineOrchestrator()
        decision = orch.process(identifier="GRO-1234", pr_url="...", result=...)
        if decision.action == ACTION_REWORK:
            factory.dispatch(decision.rework_payload)
    """

    def __init__(self, max_rework_attempts: int = DEFAULT_MAX_REWORK_ATTEMPTS) -> None:
        self.max_rework_attempts = max_rework_attempts
        self._attempt_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def attempts_for(self, identifier: str) -> int:
        """How many rework attempts have been issued for this issue?

        Returns 0 if the issue has never been processed.
        """
        with self._lock:
            return self._attempt_counts.get(identifier, 0)

    def record_rework(self, identifier: str) -> None:
        """Bump the attempt counter after dispatching rework."""
        with self._lock:
            self._attempt_counts[identifier] = (
                self._attempt_counts.get(identifier, 0) + 1
            )

    def reset(self, identifier: str) -> None:
        """Clear the attempt counter (e.g. after APPROVE)."""
        with self._lock:
            self._attempt_counts.pop(identifier, None)

    def process(
        self,
        *,
        identifier: str,
        pr_url: str,
        result: PRReviewResult,
    ) -> PipelineDecision:
        """Run the full classify → decide pipeline for one issue.

        Returns a :class:`PipelineDecision`. If ``action == ACTION_REWORK``,
        the ``rework_payload`` field is populated and ready to dispatch.
        """
        # Hold the lock across the full read-modify-write so concurrent
        # process() calls with the same identifier cannot both dispatch
        # at the same counter snapshot.
        with self._lock:
            impact = classify_impact(result)
            attempts = self._attempt_counts.get(identifier, 0)
            action = decide_next_action(
                result,
                rework_attempts=attempts,
                max_rework_attempts=self.max_rework_attempts,
            )

            rationale = (
                f"verdict={result.verdict} impact={impact} "
                f"attempts={attempts}/{self.max_rework_attempts} → action={action}"
            )

            rework_payload: ReworkPayload | None = None
            if action == ACTION_REWORK:
                rework_payload = build_rework_payload(
                    identifier,
                    pr_url,
                    result,
                    rework_attempt=attempts + 1,
                    max_rework_attempts=self.max_rework_attempts,
                )
                self._attempt_counts[identifier] = attempts + 1

            if action == ACTION_ADVANCE:
                self._attempt_counts.pop(identifier, None)

        return PipelineDecision(
            identifier=identifier,
            verdict=result.verdict,
            impact=impact,
            action=action,
            rework_payload=rework_payload,
            rationale=rationale,
            metadata={
                "attempts": attempts,
                "max_attempts": self.max_rework_attempts,
                "critical_count": result.metadata.get("critical_count", 0),
                "high_count": result.metadata.get("high_count", 0),
                "warning_count": result.metadata.get("warning_count", 0),
            },
        )
