"""Decision backends: OpenRouter decisions, TypeSafe direct, and fallback.

Transport only — stdlib ``urllib``, no proprietary SDK. Every failure raises
``DecisionError`` (fail-closed); a failed call never invents a decision.

Credential rules: keys come from the environment only, are stored on the
backend instance for the request, and are never logged, persisted, or
included in error messages, audit dicts, or ``repr`` output.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .errors import DecisionError, MissingCredentialError, NoBackendError
from .questions import Answer, Question

logger = logging.getLogger("prismatic.jev.backends")

DEFAULT_TIMEOUT = 10.0
DEFAULT_MODEL = "typesafe/jev-1.13"
OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_URL = "https://api.typesafe.ai/v1/decisions"  # provisional; see README


@dataclass(frozen=True)
class BackendResult:
    backend: str
    answers: dict[str, Answer]
    latency_ms: float


class _HttpDecisionsBackend:
    """Shared transport for the decisions-API shape (state + questions dict)."""

    backend_name = "http"
    credential_env_var = ""
    default_url = ""

    def __init__(
        self,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        env_var = self.credential_env_var
        key = os.environ.get(env_var) if env_var else None
        if not key:
            raise MissingCredentialError(env_var)
        self._api_key = key
        self.url = url or self.default_url
        self.model = model or os.environ.get("SWARMJEV_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def __repr__(self) -> str:  # never leak the key
        return (
            f"{type(self).__name__}(url={self.url!r}, model={self.model!r}, key=<set>)"
        )

    def decide(self, state: dict[str, Any], questions: list[Question]) -> BackendResult:
        payload = {
            "model": self.model,
            "state": state,
            "questions": {q.name: q.to_wire() for q in questions},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except Exception as exc:  # URLError, HTTPError, timeout, socket errors
            raise DecisionError(
                f"{self.backend_name} backend request failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise DecisionError(
                f"{self.backend_name} backend returned invalid JSON: {type(exc).__name__}"
            ) from exc
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            raise DecisionError(
                f"{self.backend_name} backend response missing 'answers' object"
            )
        parsed: dict[str, Answer] = {}
        for q in questions:
            if q.name not in answers:
                raise DecisionError(
                    f"{self.backend_name} backend response missing answer for "
                    f"question {q.name!r}"
                )
            # Strict validation; SchemaViolationError subclasses DecisionError.
            parsed[q.name] = q.parse_answer(answers[q.name])
        logger.debug(
            "%s backend answered %d question(s) in %.0f ms",
            self.backend_name,
            len(parsed),
            latency_ms,
        )
        return BackendResult(
            backend=self.backend_name, answers=parsed, latency_ms=latency_ms
        )


class OpenRouterDecisionsBackend(_HttpDecisionsBackend):
    """Jev via OpenRouter's decisions endpoint (not chat completions)."""

    backend_name = "openrouter"
    credential_env_var = "OPENROUTER_API_KEY"
    default_url = OPENROUTER_URL


class TypeSafeDecisionsBackend(_HttpDecisionsBackend):
    """Jev via TypeSafe's direct API.

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
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(
            url=url or os.environ.get("JEV_API_URL") or self.default_url,
            model=model,
            timeout=timeout,
        )


class FallbackBackend:
    """No network, no key. Returns caller-supplied deterministic defaults or
    raises NoBackendError. This is the default backend: zero-AI installs work
    exactly as before."""

    backend_name = "fallback"

    def __repr__(self) -> str:
        return "FallbackBackend()"

    def decide(
        self,
        state: dict[str, Any],
        questions: list[Question],
        defaults: dict[str, Any] | None = None,
    ) -> BackendResult:
        if defaults is None:
            raise NoBackendError(
                "fallback backend has no network access and no deterministic "
                "defaults were provided; refusing to invent a decision"
            )
        parsed: dict[str, Answer] = {}
        for q in questions:
            if q.name not in defaults:
                raise DecisionError(
                    f"no deterministic default for question {q.name!r}; "
                    f"refusing to invent a decision"
                )
            parsed[q.name] = q.default_answer(defaults[q.name])
        return BackendResult(backend=self.backend_name, answers=parsed, latency_ms=0.0)


def resolve_backend(name: str | None = None, timeout: float = DEFAULT_TIMEOUT):
    """Select a backend from SWARMJEV_BACKEND (default: fallback).

    Unknown names resolve to the fallback backend — an unrecognized selection
    must never silently enable a network backend (fail-closed).
    """
    selected = (name or os.environ.get("SWARMJEV_BACKEND", "fallback")).strip().lower()
    if selected == "openrouter":
        return OpenRouterDecisionsBackend(timeout=timeout)
    if selected == "typesafe":
        return TypeSafeDecisionsBackend(timeout=timeout)
    if selected != "fallback":
        logger.warning("unknown SWARMJEV_BACKEND=%r; using fallback backend", selected)
    return FallbackBackend()
