"""Typed-decision primitive errors.

All failures are explicit exceptions. A failed Jev call never invents a
decision — callers either get a validated answer or one of these.
"""

from __future__ import annotations


class DecisionError(Exception):
    """Base error for every swarmjev failure: transport, schema, config."""


class NoBackendError(DecisionError):
    """Raised by the fallback backend when no deterministic defaults were given."""


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
