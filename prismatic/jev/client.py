"""DecisionClient: one decide() call, parallel typed questions, fail-closed.

Jev is exception-path only: deterministic code owns the happy path; Jev owns
failures and novel situations. decide() never returns raw text — it returns
typed answers that parse strictly against the question schema.

v2 (production-grade): one :class:`TraceRecord` per decision (telemetry is
the single fail-open seam), per-call cost and latency budgets, caller
idempotency keys, opt-in exact-hash memoization, first-class abstain, and a
zero-network :meth:`maybe_decide` pre-check.

The deterministic path remains exception-only: it runs only when the network
path raised, and every call site that enables it routes verdict handling
through :func:`prismatic.jev.gates.apply_jev_advice`, which enforces the
no-downgrade invariant mechanically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, replace
from typing import Any

from . import backends
from .backends import CallParams
from .config import JevConfig
from .errors import BudgetExceededError, DecisionError
from .gates import CallSiteGate
from .memo import MemoCache, memo_key
from .prompts import PromptRegistry
from .questions import Answer, Question, apply_abstain_floor
from .resilience import get_breaker
from .schema import SCHEMA_VERSION
from .trace import TraceRecord, emit_trace, utc_now_iso

logger = logging.getLogger("prismatic.jev.client")


@dataclass(frozen=True)
class DecisionResult:
    """Typed answers from one decision. ``trace_id`` links to the trace."""

    answers: dict[str, Answer]
    latency_ms: float
    backend: str
    deterministic: bool = False
    state_keys: tuple[str, ...] = ()
    state_sha256: str = ""
    trace_id: str = ""
    cost_usd: float | None = None
    attempts: int = 1
    repair_attempts: int = 0

    @property
    def abstained(self) -> bool:
        """True when any answer hit its abstain floor."""
        return any(a.abstained for a in self.answers.values())

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "answers": {name: a.to_audit_dict() for name, a in self.answers.items()},
            "latency_ms": self.latency_ms,
            "backend": self.backend,
            "deterministic": self.deterministic,
            "state_keys": list(self.state_keys),
            "state_sha256": self.state_sha256,
            "abstained": self.abstained,
        }


@dataclass(frozen=True)
class PreCheck:
    """Result of :meth:`DecisionClient.maybe_decide` — the zero-cost pre-check.

    ``proceed=False`` means "do not call the network; take the caller's
    deterministic path now". ``proceed=True`` carries the executed decision.
    """

    proceed: bool
    reason: str
    result: DecisionResult | None = None


class DecisionClient:
    """Coordinates one Jev decision across the questions in parallel.

    Usage::

        client = DecisionClient()  # resolves backend from SWARMJEV_BACKEND
        result = client.decide(
            state,
            [Choice("verdict", "Triage this event.", options=["CLEAN", "REPAIR"])],
            on_error="deterministic",
            defaults={"verdict": "CLEAN"},
        )
        final = apply_jev_advice("CLEAN", advice_choice(result.answers["verdict"]))

    Every decision emits one trace record (stdout or JSONL). If the trace
    sink fails, the decision still stands — telemetry is the one fail-open
    seam.
    """

    def __init__(
        self,
        backend: Any | None = None,
        timeout: float | None = None,
        config: JevConfig | None = None,
    ) -> None:
        self._config = config or JevConfig.from_env()
        self._backend = backend or backends.resolve_backend(
            timeout=timeout, config=self._config
        )
        self.timeout = timeout if timeout is not None else self._config.timeout_s
        self._prompts = PromptRegistry()
        self._memo = MemoCache() if self._config.memoize else None

    def __repr__(self) -> str:  # never leak credentials
        return f"DecisionClient(backend={self._backend.backend_name!r})"

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    # -- primary entry point ---------------------------------------------

    def decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        *,
        on_error: str = "raise",
        defaults: dict[str, Any] | None = None,
        cost_budget: float | None = None,
        latency_budget_ms: float | None = None,
        idempotency_key: str | None = None,
    ) -> DecisionResult:
        """Run the decision; fail-closed unless ``on_error="deterministic"``.

        Raises :class:`DecisionError` on any failure (schema violation,
        transport, timeout, budget, open circuit) when ``on_error="raise"``.
        With ``on_error="deterministic"`` a failure routes to the
        caller-supplied ``defaults`` — the primitive holds no opinions.
        """
        self._validate_call(state, questions, on_error, cost_budget, latency_budget_ms)

        trace_id = uuid.uuid4().hex
        state_keys = tuple(sorted(str(k) for k in state))
        canonical = json.dumps(state, sort_keys=True, default=str)
        state_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        # Prompt resolution fails closed (unknown prompt ids, empty prompts).
        rendered, prompt_refs = self._resolve_prompts(questions)

        call = CallParams(
            idempotency_key=idempotency_key,
            cost_budget=cost_budget,
            latency_budget_ms=latency_budget_ms,
            max_repair_attempts=self._config.max_repair_attempts,
            include_meta=self._config.include_meta,
            rendered_prompts=rendered,
        )

        # Opt-in exact-hash memoization (never the deterministic path).
        memo_lookup_key: str | None = None
        if self._memo is not None:
            memo_lookup_key = self._memo_key(questions, rendered, canonical)
            hit = self._memo.lookup(memo_lookup_key)
            if hit is not None:
                result = replace(hit, trace_id=trace_id)
                self._emit_trace(
                    trace_id=trace_id,
                    state_keys=state_keys,
                    state_sha256=state_sha256,
                    prompt_refs=prompt_refs,
                    latency_ms=result.latency_ms,
                    cost_usd=result.cost_usd,
                    attempts=result.attempts,
                    repair_attempts=result.repair_attempts,
                    backend=result.backend,
                    flags={"memo_hit": True, "abstained": result.abstained},
                    answers=result.answers,
                )
                return result

        try:
            bresult = self._backend.decide(state, questions, call=call)
        except DecisionError as exc:
            logger.warning(
                "decide() backend %s failed (%s); on_error=%s",
                self._backend.backend_name,
                type(exc).__name__,
                on_error,
            )
            self._emit_trace(
                trace_id=trace_id,
                state_keys=state_keys,
                state_sha256=state_sha256,
                prompt_refs=prompt_refs,
                error=f"{type(exc).__name__}: {exc}",
                backend=self._backend.backend_name,
                flags={
                    "fallback_used": False,
                    "deterministic": False,
                    "budget_exceeded": isinstance(exc, BudgetExceededError),
                },
            )
            if on_error == "deterministic" and defaults is not None:
                return self._deterministic_result(
                    questions, defaults, state_keys, state_sha256, trace_id, prompt_refs
                )
            raise

        answers = {
            q.name: apply_abstain_floor(q, bresult.answers[q.name]) for q in questions
        }
        result = DecisionResult(
            answers=answers,
            latency_ms=bresult.latency_ms,
            backend=bresult.backend,
            deterministic=False,
            state_keys=state_keys,
            state_sha256=state_sha256,
            trace_id=trace_id,
            cost_usd=bresult.cost_usd,
            attempts=bresult.attempts,
            repair_attempts=bresult.repair_attempts,
        )
        if self._memo is not None and memo_lookup_key is not None:
            self._memo.store(memo_lookup_key, result)

        self._emit_trace(
            trace_id=trace_id,
            state_keys=state_keys,
            state_sha256=state_sha256,
            prompt_refs=prompt_refs,
            latency_ms=bresult.latency_ms,
            cost_usd=bresult.cost_usd,
            attempts=bresult.attempts,
            repair_attempts=bresult.repair_attempts,
            tokens_in=bresult.tokens_in,
            tokens_out=bresult.tokens_out,
            backend=bresult.backend,
            flags={
                "fallback_used": False,
                "deterministic": False,
                "repaired": bresult.repair_attempts > 0,
                "abstained": result.abstained,
                "budget_exceeded": (
                    cost_budget is not None
                    and bresult.cost_usd is not None
                    and bresult.cost_usd > cost_budget
                ),
            },
            answers=answers,
        )
        return result

    # -- zero-network pre-check -------------------------------------------

    def maybe_decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        *,
        gate: CallSiteGate | None = None,
        on_error: str = "raise",
        defaults: dict[str, Any] | None = None,
        cost_budget: float | None = None,
        latency_budget_ms: float | None = None,
        idempotency_key: str | None = None,
    ) -> PreCheck:
        """Local pre-check before a network decision: gate + breaker state.

        Returns ``proceed=False`` with a reason (call-site gate closed,
        circuit breaker open) without touching the network. Otherwise runs
        :meth:`decide` and returns ``proceed=True`` with the result.
        """
        self._validate_call(state, questions, on_error, cost_budget, latency_budget_ms)
        if gate is not None and not gate.allow():
            return PreCheck(
                proceed=False,
                reason=f"call-site gate closed for {gate.site!r}",
            )
        backend_name = self._backend.backend_name
        model = getattr(self._backend, "model", None)
        if model is not None:  # a real network backend, not fallback
            breaker = get_breaker(
                backend_name,
                model,
                failure_threshold=self._config.breaker_failure_threshold,
                reset_timeout_s=self._config.breaker_reset_timeout_s,
            )
            if not breaker.should_allow():
                return PreCheck(
                    proceed=False,
                    reason=f"circuit breaker open for {backend_name}:{model}",
                )
        result = self.decide(
            state,
            questions,
            on_error=on_error,
            defaults=defaults,
            cost_budget=cost_budget,
            latency_budget_ms=latency_budget_ms,
            idempotency_key=idempotency_key,
        )
        return PreCheck(proceed=True, reason="local checks passed", result=result)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _validate_call(
        state: Any,
        questions: Any,
        on_error: str,
        cost_budget: float | None,
        latency_budget_ms: float | None,
    ) -> None:
        if on_error not in ("raise", "deterministic"):
            raise DecisionError(f"unknown on_error mode: {on_error!r}")
        if not isinstance(state, dict):
            raise DecisionError("state must be a dict")
        if not questions:
            raise DecisionError("at least one question is required")
        names = [q.name for q in questions]
        if len(set(names)) != len(names):
            raise DecisionError("question names must be unique")
        if cost_budget is not None and cost_budget < 0:
            raise DecisionError("cost_budget must be >= 0")
        if latency_budget_ms is not None and latency_budget_ms <= 0:
            raise DecisionError("latency_budget_ms must be > 0")

    def _resolve_prompts(
        self, questions: list[Question]
    ) -> tuple[dict[str, str], dict[str, str]]:
        rendered: dict[str, str] = {}
        refs: dict[str, str] = {}
        for q in questions:
            text, ref = q.effective_prompt(self._prompts)
            rendered[q.name] = text
            if ref is not None:
                refs[q.name] = ref.label()
        return rendered, refs

    def _memo_key(
        self,
        questions: list[Question],
        rendered: dict[str, str],
        canonical_state_json: str,
    ) -> str:
        questions_wire = {
            q.name: q.to_wire(prompt_text=rendered.get(q.name)) for q in questions
        }
        return memo_key(
            schema_version=SCHEMA_VERSION,
            backend_name=self._backend.backend_name,
            model=getattr(self._backend, "model", None),
            questions_wire=questions_wire,
            state_canonical_json=canonical_state_json,
        )

    def _deterministic_result(
        self,
        questions: list[Question],
        defaults: dict[str, Any],
        state_keys: tuple[str, ...],
        state_sha256: str,
        trace_id: str,
        prompt_refs: dict[str, str],
    ) -> DecisionResult:
        where = "decide()"
        answers: dict[str, Answer] = {}
        for q in questions:
            if q.name not in defaults:
                raise DecisionError(
                    f"{where}: no default provided for question {q.name!r}"
                )
            answers[q.name] = q.default_answer(defaults[q.name])
        result = DecisionResult(
            answers=answers,
            latency_ms=0.0,
            backend="deterministic",
            deterministic=True,
            state_keys=state_keys,
            state_sha256=state_sha256,
            trace_id=trace_id,
        )
        self._emit_trace(
            trace_id=trace_id,
            state_keys=state_keys,
            state_sha256=state_sha256,
            prompt_refs=prompt_refs,
            latency_ms=0.0,
            backend="deterministic",
            flags={"fallback_used": True, "deterministic": True},
            answers=answers,
        )
        return result

    def _emit_trace(
        self,
        *,
        trace_id: str,
        state_keys: tuple[str, ...],
        state_sha256: str,
        prompt_refs: dict[str, str],
        latency_ms: float | None = None,
        cost_usd: float | None = None,
        attempts: int | None = None,
        repair_attempts: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        backend: str,
        error: str | None = None,
        flags: dict[str, bool],
        answers: dict[str, Answer] | None = None,
    ) -> None:
        record = TraceRecord(
            trace_id=trace_id,
            ts=utc_now_iso(),
            backend=backend,
            model=getattr(self._backend, "model", None),
            schema_version=SCHEMA_VERSION,
            prompt_refs=prompt_refs,
            state_hash=state_sha256,
            state_keys=list(state_keys),
            phases_ms=({"total_ms": latency_ms} if latency_ms is not None else {}),
            attempts=attempts if attempts is not None else 1,
            repair_attempts=repair_attempts if repair_attempts is not None else 0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            answers={name: a.to_audit_dict() for name, a in (answers or {}).items()},
            error=error,
            flags=flags,
        )
        emit_trace(record, path=self._config.trace_path)  # never raises by design
