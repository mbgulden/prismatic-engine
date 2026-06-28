"""Pure helpers for impact-rule dispatch and hook firing — Gap 11.

These functions are called by :class:`PipelineOrchestrator` and
:class:`RealPRReviewer` to apply registered impact/action override rules
and to fire named lifecycle hooks.

Design constraints:
- ``apply_impact_rules`` is pure: no I/O, no state mutation, no exceptions raised.
- ``fire_hook`` is exception-isolated: handler crashes are caught, logged at
  WARNING level, and skipped — the calling review is never aborted.
- Neither function calls ``registry.compose()``; callers must pass the
  already-composed :class:`ComposedReviewerSpec` (so compose() is called
  exactly once per review/process call).

Reference: okf/operations/gap11-wire-deferrals-spec.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Valid return values per channel. Strings outside these sets are ignored
# to prevent channel contamination (e.g., impact rule returning an action
# string would set decision.impact to a non-IMPACT_LEVEL value).
VALID_IMPACT_LEVELS = frozenset({"trivial", "minor", "major", "blocker"})
VALID_ACTIONS = frozenset({"advance", "hold", "rework", "give_up"})


def apply_impact_rules(
    result: Any,
    current_value: str,
    rules: tuple[Any, ...],
    *,
    channel: str = "impact",
) -> str:
    """Apply registered impact/action rules in order; first non-None wins.

    Each rule is ``Callable[[PRReviewResult, str], str | None]``.
    Rules fire in registration order. The first rule returning a non-None
    value overrides ``current_value``. If all rules return None,
    ``current_value`` is returned unchanged.

    This is a pure function: no I/O, no state mutation.  Handler
    exceptions are caught, a WARNING is logged, and the next rule is
    tried (does not abort the calling review).

    Args:
        result: The ``PRReviewResult`` passed to each rule as the first arg.
        current_value: The current impact/action string to potentially override.
        rules: Tuple of ``ImpactRule`` callables in registration order.
        channel: Either ``"impact"`` or ``"action"``. The returned override
            is validated against the corresponding VALID_* set. Returns
            outside the valid set are ignored (logged + skipped) to prevent
            channel contamination -- e.g., an impact rule returning an
            action string would otherwise set ``decision.impact`` to a
            non-IMPACT_LEVEL value.

    Returns:
        The first non-None override returned by a rule (after channel
        validation), or ``current_value`` if no rule produced a valid
        override.
    """
    valid_values = VALID_IMPACT_LEVELS if channel == "impact" else VALID_ACTIONS
    for rule in rules:
        try:
            override = rule(result, current_value)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "apply_impact_rules(%s): rule %r raised %s -- skipping",
                channel,
                rule,
                exc,
            )
            continue
        if override is None:
            continue
        if override not in valid_values:
            logger.warning(
                "apply_impact_rules(%s): rule %r returned %r which is not "
                "a valid %s value (expected one of %s) -- skipping",
                channel,
                rule,
                override,
                channel,
                sorted(valid_values),
            )
            continue
        return override
    return current_value


def fire_hook(
    hook_name: str,
    *,
    args: tuple[Any, ...],
    spec: Any,
) -> Any:
    """Fire a named hook against the registry's hook handlers.

    .. important::
        Hooks do NOT consume ``spec.checks`` or ``spec.impact_rules``.
        Those channels are already wired into the review loop separately
        (registered checks run as part of RealPRReviewer.review_pr(),
        registered impact_rules are applied by PipelineOrchestrator.process()).

        Hooks are a third independent channel. Plugin authors register
        hook handlers via ``ReviewerRegistry.register_hook(name, fn)``
        (added in Sprint 2). For Sprint 1, this function is a no-op
        stub that returns None and logs at DEBUG level.

    .. note::
        Calling this with ``spec.checks`` would double-invoke registered
        checks (once as hook, once as their normal review-loop dispatch),
        which was a real bug caught by the Lesson 10 anti-pattern check.
        The fix is to keep these channels independent.

    Args:
        hook_name: The hook constant string (e.g. ``HOOK_BEFORE_NED_REVIEW``).
        args: Positional arguments (reserved for future hook dispatch).
        spec: A :class:`ComposedReviewerSpec` (or None).

    Returns:
        None for now. Hook dispatch is a Sprint 2 follow-up.
    """
    logger.debug(
        "fire_hook(%r): hook channel not yet wired -- returning None. "
        "Sprint 2 will add register_hook() + dispatch.",
        hook_name,
    )
    return None


__all__ = [
    "apply_impact_rules",
    "fire_hook",
]
