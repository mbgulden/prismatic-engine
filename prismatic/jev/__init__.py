"""SwarmJev: the typed decision primitive for Prismatic swarm agents.

Jev is exception-path only: deterministic code owns the happy path; Jev owns
failures and novel situations. ``decide()`` takes agent state (provenance
delimited, PII redacted) and typed questions, sends them to the OpenRouter
decisions API (or the TypeSafe direct API), and returns strictly validated
answers. It never returns raw text; it never invents a decision.

.. code-block:: python

    from prismatic.jev import DecisionClient, Choice, Noul, apply_jev_advice, advice_choice

    client = DecisionClient()  # resolves backend from SWARMJEV_BACKEND
    result = client.decide(
        state,
        [Choice("verdict", "Triage this failure.", options=["CLEAN", "REPAIR", "ESCALATE"])],
        on_error="deterministic",
        defaults={"verdict": "CLEAN"},
    )
    final = apply_jev_advice("CLEAN", advice_choice(result.answers["verdict"]))
    # final: CLEAN | ESCALATE — never a downgrade of a deterministic REPAIR/REJECT

Every decision emits one trace record (no raw state). Telemetry is the one
fail-open seam; everything else fails closed.
"""

from .backends import (
    BackendResult,
    CallParams,
    FallbackBackend,
    OpenRouterDecisionsBackend,
    TypeSafeDecisionsBackend,
    resolve_backend,
)
from .client import DecisionClient, DecisionResult, PreCheck
from .config import JevConfig
from .errors import (
    BudgetExceededError,
    CircuitOpenError,
    DecisionError,
    MissingCredentialError,
    NoBackendError,
    SchemaViolationError,
    TransportError,
)
from .gates import (
    VERDICT_CLEAN,
    VERDICT_ESCALATE,
    VERDICT_REJECT,
    VERDICT_REPAIR,
    CallSiteGate,
    advice_choice,
    apply_jev_advice,
)
from .memo import MemoCache, memo_key
from .prompts import PromptRef, PromptRegistry
from .questions import Choice, Noul, Question, Score, apply_abstain_floor
from .redact import Untrusted, serialize_state
from .trace import TraceRecord, emit_trace

__all__ = [
    "BackendResult",
    "CallParams",
    "FallbackBackend",
    "OpenRouterDecisionsBackend",
    "TypeSafeDecisionsBackend",
    "resolve_backend",
    "DecisionClient",
    "DecisionResult",
    "PreCheck",
    "JevConfig",
    "DecisionError",
    "MissingCredentialError",
    "NoBackendError",
    "SchemaViolationError",
    "TransportError",
    "CircuitOpenError",
    "BudgetExceededError",
    "CallSiteGate",
    "advice_choice",
    "apply_jev_advice",
    "VERDICT_CLEAN",
    "VERDICT_ESCALATE",
    "VERDICT_REJECT",
    "VERDICT_REPAIR",
    "MemoCache",
    "memo_key",
    "PromptRef",
    "PromptRegistry",
    "Question",
    "Choice",
    "Noul",
    "Score",
    "apply_abstain_floor",
    "Untrusted",
    "serialize_state",
    "TraceRecord",
    "emit_trace",
]
