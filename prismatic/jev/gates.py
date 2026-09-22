"""Safety plumbing for Jev call sites: per-call-site gates and the
mechanical no-downgrade enforcer.

Jev is exception-path only: deterministic code owns the happy path, Jev owns
failures and novel situations. Jev may escalate (pause for a human) but NEVER
downgrades a deterministic REPAIR/REJECT. That invariant is enforced here, in
pure code, so no call site can get it wrong.
"""

from __future__ import annotations

import os
import re

from .errors import DecisionError

VERDICT_CLEAN = "CLEAN"
VERDICT_REPAIR = "REPAIR"
VERDICT_REJECT = "REJECT"
VERDICT_ESCALATE = "ESCALATE"  # Jev-only upward move: pause for human review

_VERDICTS = (VERDICT_CLEAN, VERDICT_REPAIR, VERDICT_REJECT)
_SEVERITY = {VERDICT_CLEAN: 0, VERDICT_REPAIR: 1, VERDICT_REJECT: 2}
_JEV_ADVISORY = (VERDICT_CLEAN, VERDICT_REPAIR, VERDICT_REJECT, VERDICT_ESCALATE)


def apply_jev_advice(deterministic: str, jev_choice: str | None) -> str:
    """Combine a deterministic verdict with Jev's advisory choice.

    Mechanical no-downgrade enforcement:

    - A deterministic REPAIR or REJECT is final — any Jev advice is ignored,
      including Jev answering CLEAN at p=0.99.
    - A deterministic CLEAN may be escalated by Jev (REPAIR/REJECT/ESCALATE
      advice all become ESCALATE: pause, never clear) but never downgraded.
    - Jev CLEAN advice (or no advice) on a CLEAN verdict stays CLEAN.
    - An unrecognized deterministic verdict is a DecisionError (fail-closed:
      never guess what an unknown verdict means).
    - Unrecognized Jev advice is ignored (the deterministic verdict stands) —
      ignoring is the fail-closed direction, inventing escalation is not.

    Every future call site routes through this function; the adversarial
    cases are unit-tested explicitly.
    """
    det = (deterministic or "").strip().upper()
    if det not in _SEVERITY:
        raise DecisionError(f"unknown deterministic verdict: {deterministic!r}")
    if jev_choice is None:
        return det
    jev = str(jev_choice).strip().upper()
    if jev not in _JEV_ADVISORY:
        return det  # unrecognized advice: deterministic verdict stands
    if det == VERDICT_CLEAN and jev in (
        VERDICT_REPAIR,
        VERDICT_REJECT,
        VERDICT_ESCALATE,
    ):
        return VERDICT_ESCALATE
    return det


_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


class CallSiteGate:
    """Per-call-site enablement for Jev: individually gated, default-off.

    Both the master switch (``SWARMJEV_ENABLED``) and the per-site switch
    (``SWARMJEV_CALLSITE_<NAME>_ENABLED``) must be truthy. ``allow()`` never
    raises: any env parse problem fails closed (returns False).

    No call sites are wired yet — this gate exists so each future
    integration (spec §7 B/C/D) gets its own individually-gated, default-off
    switch, matching the RF-3 posture.
    """

    MASTER_ENV = "SWARMJEV_ENABLED"

    def __init__(self, site: str) -> None:
        self.site = site

    @property
    def site_env(self) -> str:
        norm = re.sub(r"[^A-Z0-9]", "_", self.site.upper())
        return f"SWARMJEV_CALLSITE_{norm}_ENABLED"

    def allow(self) -> bool:
        try:
            return _truthy(os.environ.get(self.MASTER_ENV, "")) and _truthy(
                os.environ.get(self.site_env, "")
            )
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"CallSiteGate(site={self.site!r})"
