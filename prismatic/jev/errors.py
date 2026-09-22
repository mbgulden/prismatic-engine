"""Typed-decision primitive errors.

All failures are explicit exceptions. A failed Jev call never invents a
decision — callers either get a validated answer or one of these.
"""

from __future__ import annotations


class DecisionError(Exception):
    """Base error for every swarmjev failure: transport, schema, config."""


class NoBackendError(DecisionError):
    """Raised by the fallback backend: no network access, no decision made."""


class MissingCredentialError(DecisionError):
    """A network backend was selected but its API key env var is unset.

    Carries the env var *name* only — never the value.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"missing credential: environment variable {env_var} is not set "
            f"(backend requires it; refusing to proceed)"
        )


class SchemaViolationError(DecisionError):
    """A backend returned a malformed or partial answer payload.

    Raised instead of coercing — Jev's fixed schema makes a malformed answer
    a hard error, never a guess.
    """


class TransportError(DecisionError):
    """An HTTP/transport failure talking to a network backend.

    Carries machine-readable classification so the retry loop can decide
    what to do:

    - ``status``: HTTP status code, or None for connection-level failures.
    - ``transient``: safe to retry (429/408/5xx, timeouts, connection errors).
      Never true for 400/401/403/404 or credential/config errors.
    - ``retry_after_s``: parsed ``Retry-After`` value (429s), or None.
    - ``trip_breaker``: counts toward opening the circuit breaker. True for
      5xx, timeouts, and connection errors; False for 429 (throttling is
      handled by retry-with-backoff, not by treating the provider as down)
      and for all 4xx.

    Never carries credentials or state values.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        transient: bool = False,
        retry_after_s: float | None = None,
        trip_breaker: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.retry_after_s = retry_after_s
        self.trip_breaker = trip_breaker


class CircuitOpenError(DecisionError):
    """The circuit breaker is open: fail fast to the deterministic path.

    Raised instead of attempting a network call the breaker has already
    judged futile. Callers handle it exactly like any backend failure
    (``on_error`` decides: raise or deterministic defaults).
    """


class BudgetExceededError(DecisionError):
    """A per-call cost or latency budget was exceeded.

    Routed through the same ``on_error`` handling as any backend failure:
    raise, or fall back to caller-supplied deterministic defaults.
    """
