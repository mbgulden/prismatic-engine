"""Plugin extension registry for the Prismatic review subsystem.

The :class:`ReviewerRegistry` is the additive composition layer that lets
plugins contribute to the reviewer's behavior without subclassing or
modifying core code. Multiple plugins can register patterns, checks, and
rules; ``compose()`` produces a frozen snapshot that the reviewer uses
for a single ``review_pr()`` call.

Three contribution channels:

1. Secret patterns -- extra (regex, kind, severity) tuples that
   augment the built-in 10 patterns. Useful for company-internal API
   tokens, project-specific keys, etc.

2. Quality checks -- callables that take a diff string and return a
   list of QualityFinding objects. Useful for project-specific
   linters (e.g. "no print statements", "no TODO comments", "every
   public function has a docstring").

3. Impact rules -- callables that take (PRReviewResult, current_impact)
   and return a new impact string (or None to keep the current one).
   First non-None wins. Useful for project-specific escalation rules
   (e.g. "in safety-critical paths, treat all warning-severity findings
   as major").

4. Action rules -- callables that take (PRReviewResult, current_action)
   and return a new action string (or None to keep the current one).
   First non-None wins. Separate from impact rules so a plugin can
   register action overrides without affecting impact classification.

The :class:`ComposedReviewerSpec` is a frozen dataclass that holds the
composed contributions. RealPRReviewer calls compose() once at the
start of each review_pr() to get a consistent view.

Subclassing RealPRReviewer is still supported for full-replacement
customization (e.g. custom diff fetching, custom secret store). The
registry pattern composes WITH subclassing, not against it.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 9 / Part B)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


# Public type aliases for plugin authors

SecretPattern = tuple[str, str, Literal["critical", "high", "medium", "warning"]]
"""A single secret-detection pattern.

Elements:
  - regex (str): the pattern string
  - kind (str): a human-readable identifier ("aws_access_key", etc.)
  - severity (str): one of "critical" / "high" / "medium" / "warning"
"""

QualityCheck = Callable[[str], list[Any]]
"""A code-quality check callable.

Args: diff (str) -- the unified diff text.
Returns: list of QualityFinding-like objects (path, line, severity, message).
"""

ImpactRule = Callable[[Any, str], str | None]
"""An impact-override rule callable.

Args:
  - result: PRReviewResult
  - current: the impact string computed by classify_impact()

Returns: new impact string (overrides), or None to keep current.
First non-None rule wins (registration order).
"""

ActionRule = Callable[[Any, str], str | None]
"""An action-override rule callable.

Args:
  - result: PRReviewResult
  - current: the action string computed by decide_next_action()

Returns: new action string (overrides), or None to keep current.
First non-None rule wins (registration order).

Action rules are SEPARATE from impact rules. A plugin can register an
action-only override (e.g. "always escalate to NEEDS_HUMAN_REVIEW when
a security-severity finding exists") without affecting impact
classification. The channel guard in apply_impact_rules() ensures
action rules returning an invalid impact value (or vice versa) are
silently ignored rather than corrupting the wrong channel.
"""


@dataclass(frozen=True)
class ComposedReviewerSpec:
    """Frozen snapshot of registry contributions.

    Returned by ReviewerRegistry.compose(). RealPRReviewer uses this for
    the duration of one review_pr() call, so concurrent registrations
    from other plugins do not affect an in-flight review.
    """

    secret_patterns: tuple[SecretPattern, ...] = ()
    checks: tuple[QualityCheck, ...] = ()
    impact_rules: tuple[ImpactRule, ...] = ()
    action_rules: tuple[ActionRule, ...] = ()


class ReviewerRegistry:
    """Additive composition layer for plugin contributions.

    Thread safety: not thread-safe. Construct one per worker thread, or
    guard externally. The registry is designed for setup-time use
    (during plugin init), not for hot-path mutation.

    Usage::

        registry = ReviewerRegistry()
        registry.register_secret_pattern(r"foo[0-9]{8}", "company_token", "high")
        registry.register_check(my_no_print_check, name="no_print")

        # Pass to the reviewer
        reviewer = RealPRReviewer(registry=registry)
    """

    def __init__(self) -> None:
        self._secret_patterns: list[SecretPattern] = []
        self._checks_by_name: dict[str, QualityCheck] = {}
        self._unnamed_checks: list[QualityCheck] = []
        self._impact_rules: list[ImpactRule] = []
        self._action_rules: list[ActionRule] = []
        self._seen_secret_keys: set[tuple[str, str]] = set()

    # Secret patterns

    def register_secret_pattern(
        self,
        regex: str,
        kind: str,
        severity: Literal["critical", "high", "medium", "warning"],
    ) -> None:
        """Register one extra secret-detection pattern.

        Duplicate (regex, kind) pairs are silently ignored (idempotent).
        """
        if severity not in ("critical", "high", "medium", "warning"):
            raise ValueError(
                f"Invalid severity {severity!r}; must be one of "
                "'critical', 'high', 'medium', 'warning'"
            )
        key = (regex, kind)
        if key in self._seen_secret_keys:
            return
        self._seen_secret_keys.add(key)
        self._secret_patterns.append((regex, kind, severity))

    # Quality checks

    def register_check(self, fn: QualityCheck, *, name: str | None = None) -> None:
        """Register one extra code-quality check callable.

        If ``name`` is given and was already registered, the new function
        silently replaces the old one. If ``name`` is None, the check is
        appended to the unnamed list (no dedup, no replace).
        """
        if name is None:
            self._unnamed_checks.append(fn)
        else:
            self._checks_by_name[name] = fn

    # Impact rules

    def register_impact_rule(self, fn: ImpactRule) -> None:
        """Register one impact-override rule.

        Rules fire in registration order; first non-None wins.

        Wired: see PipelineOrchestrator.process() (Gap 11). Registered
        rules are applied after ``classify_impact()`` returns; the first
        rule returning a non-None string overrides the built-in
        classification. Rules are also applied after
        ``decide_next_action()`` returns (for action overrides). Plugin
        authors registering safety-critical escalation rules (e.g. "in
        safety-critical paths, treat all warning-severity findings as
        major") will see their rules take effect in production reviews.
        """
        self._impact_rules.append(fn)

    # Action rules

    def register_action_rule(self, fn: ActionRule) -> None:
        """Register one action-override rule.

        Rules fire in registration order; first non-None wins.

        Wired: see PipelineOrchestrator.process() (Gap 11). Action
        rules are separate from impact rules -- a plugin author can
        register an action-only override without affecting impact
        classification. Use this for rules like "always escalate to
        NEEDS_HUMAN_REVIEW when security findings exist" or "force
        GIVE_UP on contracts with broken tests".
        """
        self._action_rules.append(fn)

    # Compose

    def compose(self) -> ComposedReviewerSpec:
        """Freeze current state into an immutable spec for one review.

        Returns:
            ComposedReviewerSpec with all current contributions. Safe to
            pass across threads; safe to hold for the duration of a review.
        """
        # Named checks first (in dict insertion order), then unnamed (in
        # list-append order). This matches the registration-order semantics
        # callers expect: first-registered fires first.
        all_checks: list[QualityCheck] = list(self._checks_by_name.values()) + list(
            self._unnamed_checks
        )
        return ComposedReviewerSpec(
            secret_patterns=tuple(self._secret_patterns),
            checks=tuple(all_checks),
            impact_rules=tuple(self._impact_rules),
            action_rules=tuple(self._action_rules),
        )

    # Introspection

    @property
    def secret_pattern_count(self) -> int:
        return len(self._secret_patterns)

    @property
    def check_count(self) -> int:
        return len(self._checks_by_name) + len(self._unnamed_checks)

    @property
    def impact_rule_count(self) -> int:
        return len(self._impact_rules)

    @property
    def action_rule_count(self) -> int:
        return len(self._action_rules)


__all__ = [
    "ActionRule",
    "ComposedReviewerSpec",
    "ImpactRule",
    "QualityCheck",
    "ReviewerRegistry",
    "SecretPattern",
]
