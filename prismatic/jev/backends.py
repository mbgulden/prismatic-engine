"""Decision backends: OpenRouter decisions, TypeSafe direct, and fallback.

Transport only — stdlib ``urllib``, no proprietary SDK. Every failure raises
``DecisionError`` (fail-closed); a failed call never invents a decision.

v2 resilience (spec §6): every network call runs inside
bulkhead → circuit breaker → classified retry (transient errors only,
exponential backoff with full jitter, ``Retry-After`` honored) → timeout.
State passes through the single provenance/redaction chokepoint
(:func:`prismatic.jev.redact.serialize_state`) immediately before the HTTP
call.

Credential rules: keys come from the environment only, are held on the
backend instance for the request, and are never logged, persisted, included
in error messages, audit dicts, trace records, or ``repr`` output.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

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
from .questions import Question
from .redact import serialize_state
from .resilience import (
    backoff_delay,
    classify_connection_error,
    classify_http_error,
    get_breaker,
    get_bulkhead,
)
from .schema import SCHEMA_VERSION, check_response_schema_version

logger = logging.getLogger("prismatic.jev.backends")

DEFAULT_TIMEOUT = 10.0
DEFAULT_MODEL = "typesafe/jev-1.13"
OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_URL = "https://api.typesafe.ai/v1/decisions"

# Input pricing in USD per million tokens. Output is free on the decisions
# API shape. Unknown (None) means cost budgets cannot be verified before a
# call → a cost_budget on such a backend fails closed (see _check_cost_budget).
INPUT_PRICE_PER_MTOK: dict[str, float | None] = {
    "openrouter": 0.042,
    "typesafe": None,
}


@dataclass(frozen=True)
class CallParams:
    """Per-call knobs. Budgets are enforced across retries AND repairs."""

    idempotency_key: str | None = None
    cost_budget: float | None = None  # USD, enforced pre-call and checked post-hoc
    latency_budget_ms: float | None = None  # wall-clock across the whole call
    max_repair_attempts: int = 1  # bounded schema repair; 0 disables, 2 max
    include_meta: bool = True
    rendered_prompts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendResult:
    backend: str
    answers: dict[str, Any]
    latency_ms: float
    attempts: int = 1
    repair_attempts: int = 0
    phases_ms: dict[str, float] = field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class _HttpDecisionsBackend:
    backend_name = "http"
    credential_env_var = ""
    default_url = ""

    def __init__(
        self,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        config: JevConfig | None = None,
    ) -> None:
        env_var = self.credential_env_var
        key = os.environ.get(env_var) if env_var else None
        if not key:
            raise MissingCredentialError(env_var)
        self._api_key = key  # held for the request; never logged or persisted
        self._config = config or JevConfig()
        self.url = url or self.default_url
        self.model = model or os.environ.get("SWARMJEV_MODEL", DEFAULT_MODEL)
        self.timeout = timeout if timeout is not None else self._config.timeout_s

    def __repr__(self) -> str:
        # Never include the API key.
        return (
            f"{type(self).__name__}(url={self.url!r}, model={self.model!r}, "
            f"timeout={self.timeout!r})"
        )

    # -- public API ------------------------------------------------------

    def decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        *,
        call: CallParams | None = None,
    ) -> BackendResult:
        call = call or CallParams()
        if not isinstance(state, dict):
            raise DecisionError("state must be a dict")
        if not questions:
            raise DecisionError("at least one question is required")

        cfg = self._config
        nonce = secrets.token_hex(8)
        t_start = time.perf_counter()
        phases: dict[str, float] = {}

        # Single chokepoint: provenance delimiting + PII redaction, applied
        # to ALL questions, immediately before the HTTP call. Trace records
        # never see raw state values.
        t = time.perf_counter()
        wire_state, untrusted_fields = serialize_state(state, nonce)
        phases["serialize_ms"] = (time.perf_counter() - t) * 1000.0

        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "model": self.model,
            "state": wire_state,
            "questions": {
                q.name: q.to_wire(prompt_text=self._rendered_prompt(call, q))
                for q in questions
            },
        }
        if call.idempotency_key:
            payload["idempotency_key"] = call.idempotency_key
        if call.include_meta:
            payload["meta"] = {
                "untrusted_is_data": True,
                "untrusted_fields": untrusted_fields,
                "delimiter": f"data_{nonce}",
            }
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self._check_cost_budget(body, call)  # pre-call gate; raises if over

        breaker = get_breaker(
            self.backend_name,
            self.model,
            failure_threshold=cfg.breaker_failure_threshold,
            reset_timeout_s=cfg.breaker_reset_timeout_s,
        )
        bulkhead = get_bulkhead(self.backend_name, cfg.max_concurrent)
        if not breaker.should_allow():
            raise CircuitOpenError(
                f"circuit breaker open for {self.backend_name}:{self.model}; "
                "failing fast to the deterministic path"
            )

        deadline = (
            time.monotonic() + call.latency_budget_ms / 1000.0
            if call.latency_budget_ms is not None
            else None
        )

        repair_attempts = 0
        current_body = body
        transport_attempts = 0
        http_ms_total = 0.0
        while True:
            t = time.perf_counter()
            raw, attempts_made = self._transport(
                current_body, call, breaker, bulkhead, deadline
            )
            transport_attempts += attempts_made
            http_ms_total += (time.perf_counter() - t) * 1000.0
            t = time.perf_counter()
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise DecisionError(
                    f"{self.backend_name} backend returned invalid JSON: "
                    f"{type(exc).__name__}"
                ) from exc
            phases["parse_ms"] = phases.get("parse_ms", 0.0) + (
                (time.perf_counter() - t) * 1000.0
            )
            check_response_schema_version(data)
            try:
                answers = self._parse_answers(data, questions)
                break
            except SchemaViolationError as exc:
                if repair_attempts >= call.max_repair_attempts:
                    raise
                if not self._within_budget(deadline):
                    raise BudgetExceededError(
                        "latency budget exhausted before schema-repair attempt"
                    ) from exc
                repair_attempts += 1
                repaired = dict(payload)
                repaired["repair_context"] = {
                    "attempt": repair_attempts,
                    "validation_error": str(exc),
                }
                current_body = json.dumps(repaired, sort_keys=True).encode("utf-8")
                logger.info(
                    "%s backend schema violation, repair attempt %d/%d",
                    self.backend_name,
                    repair_attempts,
                    call.max_repair_attempts,
                )

        phases["http_ms"] = http_ms_total
        phases["total_ms"] = (time.perf_counter() - t_start) * 1000.0
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        tokens_in = _as_int(usage.get("input_tokens")) if usage else None
        tokens_out = _as_int(usage.get("output_tokens")) if usage else None
        cost_usd = self._compute_cost(tokens_in)
        if (
            call.cost_budget is not None
            and cost_usd is not None
            and cost_usd > call.cost_budget
        ):
            logger.warning(
                "jev decision cost %.6f exceeded budget %.6f "
                "(post-hoc accounting; the call was already made)",
                cost_usd,
                call.cost_budget,
            )
        return BackendResult(
            backend=self.backend_name,
            answers=answers,
            latency_ms=phases["total_ms"],
            attempts=transport_attempts,
            repair_attempts=repair_attempts,
            phases_ms=phases,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    # -- resilience pipeline ---------------------------------------------

    def _transport(
        self,
        body: bytes,
        call: CallParams,
        breaker: Any,
        bulkhead: Any,
        deadline: float | None,
    ) -> tuple[bytes, int]:
        """bulkhead → breaker → classified retry. Returns (raw, attempts)."""
        cfg = self._config
        attempts = 0
        while True:
            attempts += 1
            if not breaker.should_allow():
                raise CircuitOpenError(
                    f"circuit breaker open for {self.backend_name}:{self.model}"
                )
            remaining = self._remaining_s(deadline)
            if remaining is not None and remaining <= 0:
                raise BudgetExceededError("latency budget exceeded before attempt")
            timeout_s = (
                min(self.timeout, remaining) if remaining is not None else self.timeout
            )
            if not bulkhead.acquire(
                timeout=(
                    min(self.timeout, remaining)
                    if remaining is not None
                    else self.timeout
                )
            ):
                raise DecisionError(
                    f"{self.backend_name} backend bulkhead saturated "
                    f"({cfg.max_concurrent} concurrent); refusing to queue"
                )
            try:
                raw = self._post(
                    body,
                    timeout_s=timeout_s,
                    idempotency_key=call.idempotency_key,
                )
            except TransportError as exc:
                breaker.record_transport_error(exc)
                retry_ok = (
                    exc.transient
                    and attempts < cfg.max_attempts
                    and self._within_budget(deadline)
                )
                if not retry_ok:
                    raise
                delay = backoff_delay(
                    attempt=attempts,
                    base_s=cfg.backoff_base_s,
                    cap_s=cfg.backoff_cap_s,
                    retry_after_s=exc.retry_after_s,
                )
                if remaining is not None:
                    delay = min(delay, max(0.0, remaining))
                logger.info(
                    "%s backend transient %s, retrying in %.3fs (attempt %d/%d)",
                    self.backend_name,
                    exc,
                    delay,
                    attempts,
                    cfg.max_attempts,
                )
                time.sleep(delay)
                continue
            finally:
                bulkhead.release()
            breaker.record_success()
            return raw, attempts

    def _post(
        self, body: bytes, *, timeout_s: float, idempotency_key: str | None
    ) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(
            self.url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            try:
                hdrs = dict(exc.headers.items()) if exc.headers else {}
            except Exception:
                hdrs = {}
            raise classify_http_error(exc.code, hdrs) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise classify_connection_error(exc) from exc
        except Exception as exc:
            raise TransportError(
                f"{self.backend_name} backend request failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # -- parsing ----------------------------------------------------------

    def _parse_answers(self, data: Any, questions: list[Question]) -> dict[str, Any]:
        where = f"{self.backend_name}.response"
        if not isinstance(data, dict):
            raise SchemaViolationError(
                f"{where}: expected JSON object, got {type(data).__name__}"
            )
        answers_raw = data.get("answers")
        if not isinstance(answers_raw, dict):
            raise SchemaViolationError(f"{where}: missing 'answers' object")
        answers: dict[str, Any] = {}
        for q in questions:
            if q.name not in answers_raw:
                raise SchemaViolationError(
                    f"{where}: missing answer for question {q.name!r}"
                )
            answers[q.name] = q.parse_answer(answers_raw[q.name])
        return answers

    # -- budgets -----------------------------------------------------------

    @staticmethod
    def _remaining_s(deadline: float | None) -> float | None:
        return None if deadline is None else deadline - time.monotonic()

    @staticmethod
    def _within_budget(deadline: float | None) -> bool:
        return deadline is None or (deadline - time.monotonic()) > 0

    def _rendered_prompt(self, call: CallParams, question: Question) -> str | None:
        text = call.rendered_prompts.get(question.name)
        if text is None:
            return None
        return text

    def _check_cost_budget(self, body: bytes, call: CallParams) -> None:
        """Pre-call cost gate: fail closed if the worst-case estimate is over."""
        if call.cost_budget is None:
            return
        price = INPUT_PRICE_PER_MTOK.get(self.backend_name)
        if price is None:
            raise DecisionError(
                f"cannot verify cost_budget: no input price for backend "
                f"{self.backend_name!r}; refusing to proceed blind"
            )
        est_tokens = max(1, len(body) // 4)  # ~4 chars per token heuristic
        max_sends = self._config.max_attempts + call.max_repair_attempts
        est_cost = est_tokens / 1_000_000 * price * max_sends
        if est_cost > call.cost_budget:
            raise BudgetExceededError(
                f"estimated max cost ${est_cost:.6f} exceeds cost_budget "
                f"${call.cost_budget:.6f}"
            )

    def _compute_cost(self, tokens_in: int | None) -> float | None:
        """Post-call cost accounting (input tokens only; output is free)."""
        price = INPUT_PRICE_PER_MTOK.get(self.backend_name)
        if price is None or tokens_in is None:
            return None
        return tokens_in / 1_000_000 * price


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


class OpenRouterDecisionsBackend(_HttpDecisionsBackend):
    """OpenRouter hosted decisions API backend."""

    backend_name = "openrouter"
    credential_env_var = "OPENROUTER_API_KEY"
    default_url = OPENROUTER_URL


class TypeSafeDecisionsBackend(_HttpDecisionsBackend):
    """TypeSafe direct decisions API backend.

    TypeSafe's direct API was waitlist-only at implementation time; the URL
    defaults to a provisional value and is overridable via JEV_API_URL until
    vendor docs publish.
    """

    backend_name = "typesafe"
    credential_env_var = "JEV_API_KEY"
    default_url = TYPESAFE_URL

    def __init__(
        self,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        config: JevConfig | None = None,
    ) -> None:
        super().__init__(
            url=url or os.environ.get("JEV_API_URL") or self.default_url,
            model=model,
            timeout=timeout,
            config=config,
        )


class FallbackBackend:
    """No-network backend: raises so decide() takes the deterministic path.

    This keeps zero-AI installs working: there is no default answer living
    inside the primitive, only the failure signal the caller routes to its
    own defaults.
    """

    backend_name = "fallback"

    def decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        *,
        call: CallParams | None = None,
    ) -> BackendResult:
        raise NoBackendError(
            "fallback backend has no network access; "
            "use on_error='deterministic' with caller-supplied defaults"
        )

    def __repr__(self) -> str:
        return "FallbackBackend()"


def resolve_backend(
    name: str | None = None,
    timeout: float | None = None,
    *,
    config: JevConfig | None = None,
) -> _HttpDecisionsBackend | FallbackBackend:
    """Pick a backend by name or ``SWARMJEV_BACKEND``; default ``fallback``.

    ``timeout`` overrides ``config.timeout_s`` when given; otherwise the
    config (env ``SWARMJEV_TIMEOUT_S``) wins. Any configured (non-fallback)
    backend raises ``MissingCredentialError`` at construction time —
    misconfiguration fails closed at build time, not at first request.
    """
    cfg = config or JevConfig()
    selected = (name or os.environ.get("SWARMJEV_BACKEND", "fallback")).strip().lower()
    if selected == "openrouter":
        return OpenRouterDecisionsBackend(timeout=timeout, config=cfg)
    if selected == "typesafe":
        return TypeSafeDecisionsBackend(timeout=timeout, config=cfg)
    if selected != "fallback":
        logger.warning("unknown SWARMJEV_BACKEND=%r; using fallback backend", selected)
    return FallbackBackend()
