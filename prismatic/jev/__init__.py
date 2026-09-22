"""swarmjev — typed-decision primitive for Prismatic.

Jev is TypeSafe's "System One" model: it does not generate text. You send it
application state plus typed questions — Choice (pick from options you
define), Score (position on a scale), Noul (yes/no probability) — and it
answers all of them in parallel, returning calibrated probabilities.

Safety contract (build-sequence rule 4, spec §6):

- Jev is **exception-path only**: deterministic code owns the happy path;
  Jev owns failures, weird PRs, novel situations.
- Jev may **escalate** (pause for a human) but NEVER downgrades a
  deterministic REPAIR/REJECT — enforced mechanically by
  :func:`apply_jev_advice`.
- Jev calls **fail closed**: any backend error raises ``DecisionError``
  unless the caller supplied deterministic defaults.
- Every Jev call site is **individually gated, default-off** via
  :class:`CallSiteGate`. No call sites are wired in this package yet.
- Credentials come **only** from environment variables and are never
  logged, persisted, or included in audit signals or error messages.
- Default backend is ``fallback`` (no network, no key): zero-AI installs
  run exactly as today.

Extraction note: this package is stdlib-only and imports nothing else from
``prismatic.*``, so it lifts unchanged into the standalone ``swarmjev``
PyPI repo when Michael gives the word.
"""

from __future__ import annotations

from .backends import (
    FallbackBackend,
    OpenRouterDecisionsBackend,
    TypeSafeDecisionsBackend,
    resolve_backend,
)
from .client import DecisionClient, DecisionResult
from .errors import (
    DecisionError,
    MissingCredentialError,
    NoBackendError,
    SchemaViolationError,
)
from .gates import (
    VERDICT_CLEAN,
    VERDICT_ESCALATE,
    VERDICT_REJECT,
    VERDICT_REPAIR,
    CallSiteGate,
    apply_jev_advice,
)
from .questions import Choice, Noul, Question, Score

__all__ = [
    "DecisionClient",
    "DecisionResult",
    "Choice",
    "Score",
    "Noul",
    "Question",
    "FallbackBackend",
    "OpenRouterDecisionsBackend",
    "TypeSafeDecisionsBackend",
    "resolve_backend",
    "DecisionError",
    "NoBackendError",
    "MissingCredentialError",
    "SchemaViolationError",
    "CallSiteGate",
    "apply_jev_advice",
    "VERDICT_CLEAN",
    "VERDICT_REPAIR",
    "VERDICT_REJECT",
    "VERDICT_ESCALATE",
]
