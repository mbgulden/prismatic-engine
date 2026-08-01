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

from .apply_impact_rules import apply_impact_rules, fire_hook
from .hooks import (
    ALL_HOOKS,
    HOOK_BEFORE_CLASSIFY_IMPACT,
    HOOK_BEFORE_DECIDE_ACTION,
    HOOK_BEFORE_NED_REVIEW,
    HOOK_BEFORE_QUALITY_CHECKS,
    HOOK_BEFORE_SECRET_SCAN,
)
from .pipeline import (
    ACTION_ADVANCE,
    ACTION_GIVE_UP,
    ACTION_HOLD,
    ACTION_REWORK,
    ACTIONS,
    DEFAULT_MAX_REWORK_ATTEMPTS,
    IMPACT_BLOCKER,
    IMPACT_LEVELS,
    IMPACT_MAJOR,
    IMPACT_MINOR,
    IMPACT_RANK,
    IMPACT_TRIVIAL,
    PipelineDecision,
    PipelineOrchestrator,
    ReworkPayload,
    build_rework_payload,
    classify_impact,
    decide_next_action,
)
from .pr_reviewer import (
    APPROVE,
    NED_REVIEW_LABEL,
    NEEDS_DISCUSSION,
    REQUEST_CHANGES,
    PRReviewer,
    PRReviewResult,
    StubPRReviewer,
)
from .pr_reviewer_impl import QualityFinding, RealPRReviewer
from .registry import (
    ComposedReviewerSpec,
    ImpactRule,
    QualityCheck,
    ReviewerRegistry,
    SecretPattern,
)

__all__ = [
    "ACTIONS",
    "ACTION_ADVANCE",
    "ACTION_GIVE_UP",
    "ACTION_HOLD",
    "ACTION_REWORK",
    "ALL_HOOKS",
    "APPROVE",
    "DEFAULT_MAX_REWORK_ATTEMPTS",
    "HOOK_BEFORE_CLASSIFY_IMPACT",
    "HOOK_BEFORE_DECIDE_ACTION",
    "HOOK_BEFORE_NED_REVIEW",
    "HOOK_BEFORE_QUALITY_CHECKS",
    "HOOK_BEFORE_SECRET_SCAN",
    "IMPACT_BLOCKER",
    "IMPACT_LEVELS",
    "IMPACT_MAJOR",
    "IMPACT_MINOR",
    "IMPACT_RANK",
    "IMPACT_TRIVIAL",
    "NED_REVIEW_LABEL",
    "NEEDS_DISCUSSION",
    "REQUEST_CHANGES",
    "ComposedReviewerSpec",
    "ImpactRule",
    "PRReviewResult",
    "PRReviewer",
    "PipelineDecision",
    "PipelineOrchestrator",
    "QualityCheck",
    "QualityFinding",
    "RealPRReviewer",
    "ReviewerRegistry",
    "ReworkPayload",
    "SecretPattern",
    "StubPRReviewer",
    "apply_impact_rules",
    "build_rework_payload",
    "classify_impact",
    "decide_next_action",
    "fire_hook",
]
