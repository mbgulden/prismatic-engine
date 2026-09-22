"""DecisionClient: one decide() call, parallel typed questions, fail-closed."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from . import backends
from .backends import DEFAULT_TIMEOUT, BackendResult
from .errors import DecisionError
from .questions import Answer, Question

logger = logging.getLogger("prismatic.jev.client")


@dataclass(frozen=True)
class DecisionResult:
    """Validated outcome of one decide() call.

    ``deterministic`` marks results built from caller-supplied defaults
    (the fallback path) rather than a live backend. ``state_keys`` and
    ``state_sha256`` let the audit trail record what the decider saw without
    storing state values (which may carry customer data).
    """

    answers: dict[str, Answer]
    latency_ms: float
    backend: str
    deterministic: bool = False
    state_keys: tuple[str, ...] = ()
    state_sha256: str = ""

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "latency_ms": round(self.latency_ms, 2),
            "deterministic": self.deterministic,
            "state_keys": list(self.state_keys),
            "state_sha256": self.state_sha256,
            "answers": {
                name: ans.to_audit_dict() for name, ans in self.answers.items()
            },
        }


class DecisionClient:
    """Typed-decision client. Backend + auth resolved from the environment.

    Default backend is ``fallback`` (no network, no key) — zero-AI installs
    run exactly as today. Fail-closed: on any backend error, ``decide()``
    raises ``DecisionError`` unless the caller passed
    ``on_error="deterministic"`` with defaults.
    """

    def __init__(self, backend=None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._backend = backend or backends.resolve_backend(timeout=timeout)
        self.timeout = timeout

    def __repr__(self) -> str:  # never leak credentials
        return f"DecisionClient(backend={self._backend.backend_name!r})"

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    def decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        *,
        on_error: str = "raise",
        defaults: dict[str, Any] | None = None,
    ) -> DecisionResult:
        """Ask all questions in one backend call (one billed request).

        ``on_error="raise"`` (default): any backend failure raises
        DecisionError — the safety-gate pattern treats it as "do not proceed".
        ``on_error="deterministic"``: on backend failure, return the
        caller-supplied ``defaults`` as a deterministic result instead. A
        failed call never invents a decision: missing defaults is itself a
        DecisionError.
        """
        if on_error not in ("raise", "deterministic"):
            raise DecisionError(f"unknown on_error mode: {on_error!r}")
        if not isinstance(state, dict):
            raise DecisionError("state must be a dict")
        if not questions:
            raise DecisionError("at least one question is required")
        names = [q.name for q in questions]
        if len(set(names)) != len(names):
            raise DecisionError("question names must be unique")

        state_keys = tuple(sorted(str(k) for k in state))
        state_sha256 = hashlib.sha256(
            json.dumps(state, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        try:
            bresult: BackendResult = self._backend.decide(state, questions)
        except DecisionError as exc:
            logger.warning(
                "decide() backend %s failed (%s); on_error=%s",
                self._backend.backend_name,
                type(exc).__name__,
                on_error,
            )
            if on_error == "deterministic" and defaults is not None:
                return self._deterministic_result(
                    questions, defaults, state_keys, state_sha256
                )
            raise

        return DecisionResult(
            answers=bresult.answers,
            latency_ms=bresult.latency_ms,
            backend=bresult.backend,
            deterministic=False,
            state_keys=state_keys,
            state_sha256=state_sha256,
        )

    def _deterministic_result(
        self,
        questions: list[Question],
        defaults: dict[str, Any],
        state_keys: tuple[str, ...],
        state_sha256: str,
    ) -> DecisionResult:
        parsed: dict[str, Answer] = {}
        for q in questions:
            if q.name not in defaults:
                raise DecisionError(
                    f"no deterministic default for question {q.name!r}; "
                    f"refusing to invent a decision"
                )
            parsed[q.name] = q.default_answer(defaults[q.name])
        logger.debug(
            "decide() fell back to deterministic defaults (backend unreachable)"
        )
        return DecisionResult(
            answers=parsed,
            latency_ms=0.0,
            backend=self._backend.backend_name,
            deterministic=True,
            state_keys=state_keys,
            state_sha256=state_sha256,
        )
