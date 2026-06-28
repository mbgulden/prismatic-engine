"""Prismatic Quality Gates — Phase 1 of the Quality Gates Plan.

Implements the three critical gaps from the plan:

  Gap 1: Split ``agent:needs-human-review`` into ``task:shape-violation``
         and ``output:requires-verification`` (label routing).

  Gap 2: ``VerificationVerdict`` — 7-layer post-completion check that
         actually verifies output instead of trusting agent self-reports.

  Gap 3: ``DriftGate`` — pre-commit drift detection that catches the
         300+ file PR pollution pattern before PRs are opened.

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

__all__ = [
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
    "DriftReport",
    "check_drift",
    "save_drift_report",
    "RoutingDecision",
    "route_nhr_task",
    "TASK_SHAPE_VIOLATION",
    "OUTPUT_REQUIRES_VERIFICATION",
    "ARCHIVED_NEEDS_HUMAN_REVIEW",
    "MAX_FILES_CHANGED",
]