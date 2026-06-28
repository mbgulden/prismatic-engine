"""Prismatic review subsystem — Phase 2 Gap 4.

Public surface used by ``prismatic.quality.gates.trigger_ned_review``:

- :class:`PRReviewResult` — structured verdict returned by the reviewer
- :class:`PRReviewer` — pluggable reviewer; replace ``stub`` with the real
  GitHub-API-backed reviewer (built in tasks #1–5 of Gap 4).

This package is intentionally small: the trigger in ``quality/gates.py``
only needs the verdict, the inline comments, and the Linear-state routing
decisions. The heavy lifting (diff fetch, secret scan, lint, complexity,
coverage heuristics, GitHub API) lives in tasks #1–5 and plugs in here
without changing the trigger contract.
"""

from __future__ import annotations

from .pr_reviewer import (
    PRReviewResult,
    PRReviewer,
    StubPRReviewer,
    NED_REVIEW_LABEL,
    APPROVE,
    REQUEST_CHANGES,
    NEEDS_DISCUSSION,
)
from .pr_reviewer_impl import RealPRReviewer
from .pipeline import (
    ACTION_ADVANCE,
    ACTION_GIVE_UP,
    ACTION_HOLD,
    ACTION_REWORK,
    IMPACT_BLOCKER,
    IMPACT_MAJOR,
    IMPACT_MINOR,
    IMPACT_TRIVIAL,
    PipelineDecision,
    PipelineOrchestrator,
    ReworkPayload,
    build_rework_payload,
    classify_impact,
    decide_next_action,
)

__all__ = [
    "PRReviewResult",
    "PRReviewer",
    "StubPRReviewer",
    "RealPRReviewer",
    "PipelineDecision",
    "PipelineOrchestrator",
    "ReworkPayload",
    "build_rework_payload",
    "classify_impact",
    "decide_next_action",
    "NED_REVIEW_LABEL",
    "APPROVE",
    "REQUEST_CHANGES",
    "NEEDS_DISCUSSION",
    "ACTION_ADVANCE",
    "ACTION_GIVE_UP",
    "ACTION_HOLD",
    "ACTION_REWORK",
    "IMPACT_BLOCKER",
    "IMPACT_MAJOR",
    "IMPACT_MINOR",
    "IMPACT_TRIVIAL",
]
