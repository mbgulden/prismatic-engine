"""
VLLMClient — Chat against a vLLM OpenAI-compatible inference endpoint.
============================================================

Used by the Review Factory Phase 3 LLM deep-review stage as an
alternative to ``OllamaClient``. Michael's local inference (Ned / George)
runs on vLLM, not Ollama, so this client speaks the OpenAI-compatible
``/v1`` protocol instead of Ollama's ``/api`` protocol.

The interface intentionally mirrors ``OllamaClient`` so the adapter can
swap protocols without changing its failure-handling logic:

- ``check_health()`` -> bool
- ``model_available(name)`` -> bool
- ``chat(model, messages, *, format=None, options=None, timeout=None)``
  -> dict | None

``chat()`` normalizes the OpenAI response into the Ollama shape
``{"message": {"content": ...}}`` so downstream content extraction
(``_extract_content`` in ``llm_deep_review``) works unchanged.

The API key comes from the environment — ``VLLM_API_KEY``, falling back
to ``VLLM_NED_API_KEY`` — or from the constructor. It is never hardcoded
and never written to logs, errors, or files.

Uses only the standard library (urllib.request, urllib.error) to remain
dependency-free, matching ``OllamaClient``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_ENV_API_KEY = "VLLM_API_KEY"
_ENV_API_KEY_FALLBACK = "VLLM_NED_API_KEY"


def _redacted_url(url: str) -> str:
    """URL without any query string — safe for logs."""
    return url.split("?", 1)[0]


class VLLMClient:
    """Client for a vLLM OpenAI-compatible server (``/v1``)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str | None = None,
        timeout: float = 10.0,
    ):
        """Initialize VLLMClient.

        Args:
            base_url: The vLLM server base URL, including the ``/v1``
                prefix (e.g. ``http://192.168.1.230:8003/v1``).
            api_key: Bearer key for the server. When None, read from the
                ``VLLM_API_KEY`` environment variable, then
                ``VLLM_NED_API_KEY``. The value is never logged.
            timeout: HTTP request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        if api_key is None:
            api_key = (
                os.environ.get(_ENV_API_KEY, "").strip()
                or os.environ.get(_ENV_API_KEY_FALLBACK, "").strip()
            )
        self._api_key = api_key or ""
        self.timeout = timeout

    # -- internals ------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            # Key goes only into the header dict; never into logs/errors.
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make a JSON request to the vLLM server.

        Returns the parsed JSON dict, or None when the call failed for
        any reason (connection error, timeout, HTTP error, bad JSON).
        Error details are logged without the API key.
        """
        url = f"{self.base_url}{path}"
        serialized = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            url, data=serialized, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            logger.warning(
                "vllm HTTP error %s for %s %s",
                exc.code,
                method,
                _redacted_url(url),
            )
            return None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.warning(
                "vllm connection error for %s %s: %s",
                method,
                _redacted_url(url),
                exc,
            )
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "vllm invalid JSON response for %s %s: %s",
                method,
                _redacted_url(url),
                exc,
            )
            return None

    # -- health / models ------------------------------------------------

    def check_health(self) -> bool:
        """True when the server answers GET /v1/models with HTTP 200.

        A missing or wrong API key yields 401 here, which correctly
        reports unhealthy so the review stage skips itself (fail-closed).
        """
        res = self._request("/models", "GET")
        return res is not None

    def get_available_models(self) -> list[dict[str, Any]] | None:
        """List models served by the endpoint (GET /v1/models).

        Returns the ``data`` list (entries carry an ``id`` field), or
        None when the request failed.
        """
        res = self._request("/models", "GET")
        if res is not None and isinstance(res.get("data"), list):
            return res["data"]
        return None

    def model_available(self, model_name: str) -> bool:
        """Return True when *model_name* is served by the endpoint.

        Matches the exact served id, or a prefix match (e.g.
        ``"qwen3.8-27b-ned"`` matches ``"qwen3.8-27b-ned-q5"``).
        """
        models = self.get_available_models()
        if not models:
            return False
        want = (model_name or "").strip()
        if not want:
            return False
        for entry in models:
            served = str(entry.get("id") or entry.get("name") or "")
            if served == want or served.startswith(want) or want.startswith(served):
                return True
        return False

    # -- chat -----------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        format: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Send a chat completion (POST /v1/chat/completions, non-streaming).

        Args:
            model: Served model id. Never hardcoded here — callers
                supply it from configuration (e.g. the Phase 3 LLM
                deep-review stage reads it from env).
            messages: Chat messages, e.g. [{"role": "system", ...},
                {"role": "user", ...}].
            format: Pass "json" to request JSON-mode output
                (``response_format: {"type": "json_object"}``).
            options: Extra options; ``temperature`` is forwarded.
            timeout: Per-call timeout in seconds. Falls back to the
                client default when None. Deep-review calls can take
                minutes.

        Returns:
            A dict shaped like the Ollama chat response —
            ``{"message": {"content": ...}}`` — or None when the call
            failed. The normalized shape keeps the adapter's content
            extraction working across protocols.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
        if format == "json":
            payload["response_format"] = {"type": "json_object"}
        if timeout is not None:
            previous_timeout = self.timeout
            self.timeout = timeout
            try:
                res = self._request("/chat/completions", "POST", payload)
            finally:
                self.timeout = previous_timeout
        else:
            res = self._request("/chat/completions", "POST", payload)
        if not isinstance(res, dict):
            return None
        content = ""
        try:
            choices = res.get("choices") or []
            if choices:
                content = str((choices[0].get("message") or {}).get("content") or "")
        except (AttributeError, IndexError, TypeError):
            content = ""
        if not content.strip():
            return None
        return {"message": {"role": "assistant", "content": content}}
