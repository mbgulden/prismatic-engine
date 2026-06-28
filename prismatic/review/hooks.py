"""Plugin extension points for the Prismatic review subsystem.

Plugins (third-party Python packages, or first-party extras) can extend
the reviewer's behavior at well-defined hook points without modifying
core code. The plugin contract is intentionally narrow:

| Hook | When fired | Args | Returns |
|------|------------|------|---------|
| HOOK_BEFORE_SECRET_SCAN | Before built-in secret patterns run | diff | Optional list of extra (regex, kind, severity) tuples |
| HOOK_BEFORE_QUALITY_CHECKS | Before built-in code-quality checks | diff | Optional list of extra check callables |
| HOOK_BEFORE_CLASSIFY_IMPACT | Before classify_impact() runs | result | Optional impact string (overrides) |
| HOOK_BEFORE_DECIDE_ACTION | Before decide_next_action() runs | result, attempts | Optional action string (overrides) |
| HOOK_BEFORE_NED_REVIEW | In trigger_ned_review() before review | issue | None (side-effect only) |

Each hook is just a string name. RealPRReviewer and PipelineOrchestrator
fire them at the documented points; the plugin loader
(prismatic.core.registry.PluginLoader) dispatches them to registered
plugins.

The registry pattern (see prismatic.review.registry) is the *additive*
extension story -- it merges contributions from many plugins. The hook
pattern is the *declarative* extension story -- it surfaces specific
lifecycle moments. Both layers compose: a plugin can register a custom
check via the registry AND hook into a lifecycle moment.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 9 / Part B)
"""
from __future__ import annotations


# Reviewer-side hooks (fired by RealPRReviewer + PipelineOrchestrator)

HOOK_BEFORE_SECRET_SCAN = "before_secret_scan"
"""Before built-in secret pattern scan runs. Args: diff.

Plugins may return extra (regex, kind, severity) tuples that are
merged into the scan patterns for this review.
"""

HOOK_BEFORE_QUALITY_CHECKS = "before_quality_checks"
"""Before built-in code-quality checks run. Args: diff.

Plugins may return extra check callables that take a diff and return
a list of QualityFinding objects.
"""

HOOK_BEFORE_CLASSIFY_IMPACT = "before_classify_impact"
"""Before classify_impact() runs. Args: result.

Plugins may return an impact string (one of IMPACT_TRIVIAL, IMPACT_MINOR,
IMPACT_MAJOR, IMPACT_BLOCKER) that overrides the built-in classification.
"""

HOOK_BEFORE_DECIDE_ACTION = "before_decide_action"
"""Before decide_next_action() runs. Args: result, attempts.

Plugins may return an action string (one of ACTION_ADVANCE, ACTION_HOLD,
ACTION_REWORK, ACTION_GIVE_UP) that overrides the built-in decision.
"""


# Factory-side hook (fired by trigger_ned_review)

HOOK_BEFORE_NED_REVIEW = "before_ned_review"
"""In trigger_ned_review() before the reviewer runs. Args: issue.

Plugins may mutate the issue dict in-place (e.g. add metadata, attach
PR URLs, adjust labels) or short-circuit by raising. Side-effects only;
return value is ignored.
"""


ALL_HOOKS = (
    HOOK_BEFORE_SECRET_SCAN,
    HOOK_BEFORE_QUALITY_CHECKS,
    HOOK_BEFORE_CLASSIFY_IMPACT,
    HOOK_BEFORE_DECIDE_ACTION,
    HOOK_BEFORE_NED_REVIEW,
)
"""Tuple of all hook names exported by the review subsystem."""


__all__ = [
    "HOOK_BEFORE_SECRET_SCAN",
    "HOOK_BEFORE_QUALITY_CHECKS",
    "HOOK_BEFORE_CLASSIFY_IMPACT",
    "HOOK_BEFORE_DECIDE_ACTION",
    "HOOK_BEFORE_NED_REVIEW",
    "ALL_HOOKS",
]
