"""Prismatic Quality Gates — Phase 1 + Phase 2.

Phase 1 (Gaps 1, 2, 3):
  Gap 1: Split ``agent:needs-human-review`` into ``task:shape-violation``
         and ``output:requires-verification`` (label routing).
  Gap 2: ``VerificationVerdict`` — 7-layer post-completion check.
  Gap 3: ``DriftGate`` — pre-commit drift detection.

Phase 2 (Gaps 4, 5, 7, 8):
  Gap 7: Failure classification + smart retry (this module).
  (Gaps 4, 5, 8 are in separate review/ modules.)

Reference: ``okf/operations/prismatic-quality-gates-comprehensive-plan.md``
"""
from __future__ import annotations

from .gates import (
    # Verdict
    VerificationVerdict,
    LayerResult,
    run_verification,
    save_verdict,
    # Individual layers (re-exported for unit testing)
    check_shape,
    check_workdir,
    check_files_changed,
    check_diff_meaningful,
    check_linked_pr,
    check_basic_syntax,
    check_goal_match,
    # Drift
    DriftReport,
    check_drift,
    save_drift_report,
    # Routing
    RoutingDecision,
    route_nhr_task,
    # Constants
    TASK_SHAPE_VIOLATION,
    OUTPUT_REQUIRES_VERIFICATION,
    ARCHIVED_NEEDS_HUMAN_REVIEW,
    MAX_FILES_CHANGED,
)
from .failure import (
    # Failure classification (Gap 7)
    FailureMode,
    RetryPolicy,
    POLICIES,
    FAILURE_PATTERNS,
    ClassificationResult,
    classify_failure,
    classify_with_policy,
    apply_failure_classification,
    should_retry,
    wait_for_retry,
    increment_failure,
    reset_failure,
    reset_after_success,
    get_failure_count,
    OUTPUT_REQUIRES_ATTENTION,
    COUNTER_PATH,
)

__all__ = [
    # Verdict
    "VerificationVerdict",
    "LayerResult",
    "run_verification",
    "save_verdict",
    "check_shape",
    "check_workdir",
    "check_files_changed",
    "check_diff_meaningful",
    "check_linked_pr",
    "check_basic_syntax",
    "check_goal_match",
    # Drift
    "DriftReport",
    "check_drift",
    "save_drift_report",
    # Routing
    "RoutingDecision",
    "route_nhr_task",
    # Constants
    "TASK_SHAPE_VIOLATION",
    "OUTPUT_REQUIRES_VERIFICATION",
    "ARCHIVED_NEEDS_HUMAN_REVIEW",
    "MAX_FILES_CHANGED",
    # Failure classification
    "FailureMode",
    "RetryPolicy",
    "POLICIES",
    "FAILURE_PATTERNS",
    "ClassificationResult",
    "classify_failure",
    "classify_with_policy",
    "apply_failure_classification",
    "should_retry",
    "wait_for_retry",
    "increment_failure",
    "reset_failure",
    "reset_after_success",
    "get_failure_count",
    "OUTPUT_REQUIRES_ATTENTION",
    "COUNTER_PATH",
]