"""Earned-autonomy tier policy engine (Phase 2 of the earned-autonomy plan).

Answers one question: given a tier, a change class, and the factory's
deterministic verdict (plus optional judgment output and novelty flags),
may this change auto-merge?

Fail-closed rule order (``can_auto_merge``) — each rule is a hard
refusal; a change must clear every rule to be allowed:

1.  ``brake_engaged``           — the autonomy brake is pulled.
2.  ``unknown_tier``            — tier not in {0, 1, 2, 3}.
3.  ``t0_no_auto_merge``        — Tier 0 is PRs-only today; Michael merges.
4.  ``t3_unbuilt``              — Tier 3 is defined but UNBUILT by Michael's
    decision; it never allows, no matter what the spec file says.
5.  ``class_not_in_tier``       — change class outside the tier's classes.
6.  ``deterministic_not_clean`` — deterministic verdict is not CLEAN.
    Red is repair, never merge; the deterministic floor is authoritative.
7.  ``novelty_flagged``         — the novelty detector raised flags.
8.  ``judgment_pause``          — Jev returned PAUSE: pause is escalate-only,
    never approved, never merged through.
9.  ``zero_ai_conservative``    — zero-AI runs refuse everything except
    docs changes; docs allow with the ``zero_ai_docs_only`` note.
10. ``auto_merge_allowed``      — cleared every gate.

Lazy trust boundary
-------------------
Phase 1's trust ledger (``prismatic.review_factory.trust``) is NOT
imported at module level. :func:`check_graduation` does the import
lazily inside the function and degrades to ``None`` when phase 1 is
absent (unmerged) or its ledger cannot be constructed. This module —
and its whole test suite — stays green on main without phase 1 merged.

The mechanical revoke itself lives in phase 1's trust.py; this module
only offers :func:`revocation_triggers` (a pure read-out for the digest
and wiring) and :func:`load_tier_policy` (the frozen tier spec).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ─────────────────────────────────────────────────────────────────────
# Tier class definitions (frozen; mirrors spec/autonomy_tiers_v1.yaml)
# ─────────────────────────────────────────────────────────────────────

TIER_CLASSES: dict[int, tuple[str, ...]] = {
    0: (),
    1: ("docs", "chore", "dep_bump"),
    2: ("docs", "chore", "dep_bump", "agent_standard"),
    # Tier 3 classes are listed for documentation, but T3 is defined-but-
    # unbuilt by Michael's decision: can_auto_merge refuses tier 3 before
    # the class table is ever consulted (rule 4, "t3_unbuilt").
    3: ("docs", "chore", "dep_bump", "agent_standard"),
}

# ─────────────────────────────────────────────────────────────────────
# Brake
# ─────────────────────────────────────────────────────────────────────

_BRAKE_ENV_VAR = "PRISMATIC_AUTONOMY_ENABLED"
_BRAKE_ENGAGED_VALUES = frozenset({"0", "false", "no"})

_DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "spec" / "autonomy_tiers_v1.yaml"


# ─────────────────────────────────────────────────────────────────────
# Decision model
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AutonomyDecision:
    """The outcome of a single auto-merge policy evaluation.

    ``reason`` is a plain-string reason code (see the module docstring
    for the fail-closed rule order); ``notes`` carries supplementary
    markers such as ``"judgment_skipped"`` or ``"zero_ai_docs_only"``.
    """

    allowed: bool
    reason: str
    tier: int
    change_class: str
    notes: tuple[str, ...] = ()


# ─────────────────────────────────────────────────────────────────────
# Policy evaluation
# ─────────────────────────────────────────────────────────────────────


def can_auto_merge(
    *,
    tier: int,
    change_class: str,
    deterministic_verdict: str,
    judgment: Mapping[str, Any] | None = None,
    brake_engaged: bool = False,
    zero_ai: bool = False,
    novelty_flags: tuple[str, ...] = (),
) -> AutonomyDecision:
    """Evaluate whether a change may auto-merge. Fail-closed.

    Rules are evaluated in the fixed order documented in the module
    docstring; the first matching rule wins. ``judgment=None`` means no
    judge ran — allowed through with a ``"judgment_skipped"`` note (per
    the plan, Jev=CLEAR *or* judgment skipped with a verdict note).
    """

    def refuse(reason: str, notes: tuple[str, ...] = ()) -> AutonomyDecision:
        return AutonomyDecision(
            allowed=False,
            reason=reason,
            tier=tier,
            change_class=change_class,
            notes=notes,
        )

    # 1. Brake pulled: refuse everything.
    if brake_engaged:
        return refuse("brake_engaged")

    # 2. Unknown tier: refuse.
    if tier not in TIER_CLASSES:
        return refuse("unknown_tier")

    # 3. Tier 0: PRs only, Michael merges.
    if tier == 0:
        return refuse("t0_no_auto_merge")

    # 4. Tier 3: defined but unbuilt — never activated.
    if tier == 3:
        return refuse("t3_unbuilt")

    # 5. Change class outside the tier's classes: refuse.
    if change_class not in TIER_CLASSES[tier]:
        return refuse("class_not_in_tier")

    # 6. Deterministic floor is authoritative: red is repair, never merge.
    if deterministic_verdict != "CLEAN":
        return refuse("deterministic_not_clean")

    # 7. Novelty flags: refuse.
    if novelty_flags:
        return refuse("novelty_flagged")

    # 8. Jev pause: escalate-only, never approved, never merged through.
    if isinstance(judgment, Mapping) and judgment.get("decision") == "PAUSE":
        return refuse("judgment_pause")

    # 9. Zero-AI runs: docs-only, conservative by design.
    if zero_ai:
        if change_class == "docs":
            return AutonomyDecision(
                allowed=True,
                reason="auto_merge_allowed",
                tier=tier,
                change_class=change_class,
                notes=("zero_ai_docs_only",),
            )
        return refuse("zero_ai_conservative")

    # 10. Cleared every gate.
    notes = ("judgment_skipped",) if judgment is None else ()
    return AutonomyDecision(
        allowed=True,
        reason="auto_merge_allowed",
        tier=tier,
        change_class=change_class,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────
# Brake status
# ─────────────────────────────────────────────────────────────────────


def brake_status() -> dict[str, Any]:
    """Read the autonomy brake from the environment.

    Engaged when ``PRISMATIC_AUTONOMY_ENABLED`` is ``"0"``, ``"false"``,
    or ``"no"`` (case-insensitive). Unset (the default) means not engaged.
    """
    raw = os.environ.get(_BRAKE_ENV_VAR, "")
    engaged = raw.strip().lower() in _BRAKE_ENGAGED_VALUES
    return {"engaged": engaged, "source": f"env:{_BRAKE_ENV_VAR}"}


# ─────────────────────────────────────────────────────────────────────
# Tier policy spec loading (fail-closed)
# ─────────────────────────────────────────────────────────────────────


def _disabled_policy() -> dict[str, Any]:
    """The fail-closed default: autonomy off, no tiers."""
    return {"version": "autonomy-v1", "enabled": False, "tiers": {}}


def load_tier_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the frozen tier spec (``spec/autonomy_tiers_v1.yaml``).

    Fail-closed: a missing or unparseable file returns the disabled
    default instead of raising. Never raises on a missing file.
    """
    spec_path = Path(path) if path is not None else _DEFAULT_SPEC_PATH
    try:
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return _disabled_policy()
    if not isinstance(data, dict):
        return _disabled_policy()
    return data


# ─────────────────────────────────────────────────────────────────────
# Graduation (lazy trust boundary)
# ─────────────────────────────────────────────────────────────────────


def check_graduation(ledger: Any = None) -> Any:
    """Evaluate tier graduation against the phase-1 trust ledger.

    Lazy trust boundary: ``prismatic.review_factory.trust`` is imported
    INSIDE this function, never at module level, so this module stays
    green without phase 1 merged. If ``ledger`` is None, a
    ``TrustLedger`` is constructed from the phase-1 module; on ANY
    failure (phase 1 absent, DB unavailable) this returns ``None`` —
    documented degradation: graduation evaluation needs the phase-1
    ledger. The returned proposal is advisory; promotion itself stays
    Michael's explicit decision (phase 1's ``record_tier_promoted``).
    """
    if ledger is None:
        try:
            from prismatic.review_factory import trust

            ledger = trust.TrustLedger()
        except Exception:
            # Phase 1 unmerged (ImportError) or ledger construction
            # failed (DB error, ...): cannot evaluate graduation.
            return None
    return ledger.check_graduation()


# ─────────────────────────────────────────────────────────────────────
# Revocation read-out (pure)
# ─────────────────────────────────────────────────────────────────────


def revocation_triggers(status: Mapping[str, Any]) -> list[str]:
    """Return the revocation trigger names currently firing.

    Pure function over a tier-status-shaped mapping. The mechanical
    revoke itself lives in phase 1's trust.py; this is the read-out for
    the digest and wiring.
    """
    triggers: list[str] = []
    if status.get("rollback_count_30d", 0) > 0:
        triggers.append("rollback_in_30d")
    precision = status.get("pause_precision")
    if (
        precision is not None
        and precision < 0.50
        and status.get("pauses_trailing_30", 0) >= 20
    ):
        triggers.append("precision_collapse")
    return triggers
