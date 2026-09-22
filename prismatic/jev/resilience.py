"""Serving resilience: bulkhead, circuit breaker, classified retries.

Fixed layering (outermost to innermost):

    bulkhead (concurrency cap) → circuit breaker (fail fast) →
    retry with backoff+jitter (transient errors only) → timeout

Retrying a model call is never free — it burns cost and latency — so retries
are bounded, classified, and share the caller's idempotency key. A circuit
breaker's "open" state maps directly onto the zero-AI deterministic fallback:
the primitive behaves as if there were no backend at all.

Breaker registry is per (backend, model), never global: one provider's outage
must not trip another vendor's fallback path. State is per-process and
thread-safe; multi-process deployments get per-process breakers, which is
the safe direction (each process fails over independently).
"""

from __future__ import annotations

import random
import threading
import time
from email.utils import parsedate_to_datetime

from .errors import TransportError

# Statuses safe to retry. Everything else 4xx (400/401/403/404/…) is a
# deterministic failure: retrying cannot help and only burns budget.
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def classify_http_error(status: int, headers: dict[str, str] | None) -> TransportError:
    """Classify an HTTP failure for the retry loop."""
    headers = headers or {}
    transient = status in _RETRYABLE_STATUS
    retry_after = _parse_retry_after(headers.get("Retry-After"))
    # 5xx: provider is sick → counts toward the breaker. 429: throttling →
    # handled by backoff, not by treating the provider as down. 4xx: caller
    # error → never retried, never trips the breaker.
    trip_breaker = status >= 500
    return TransportError(
        f"backend returned HTTP {status}",
        status=status,
        transient=transient,
        retry_after_s=retry_after,
        trip_breaker=trip_breaker,
    )


def classify_connection_error(exc: BaseException) -> TransportError:
    """Timeouts and connection failures are transient and trip the breaker."""
    return TransportError(
        f"backend connection failed: {type(exc).__name__}: {exc}",
        status=None,
        transient=True,
        trip_breaker=True,
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = int(value)
    except ValueError:
        try:
            delta = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
        return max(0.0, delta)
    return max(0.0, float(seconds))


def backoff_delay(
    *, attempt: int, base_s: float, cap_s: float, retry_after_s: float | None
) -> float:
    """Equal-jitter exponential backoff. attempt is 1-based (first retry = 1)."""
    if retry_after_s is not None:
        return min(retry_after_s, cap_s)
    exp = min(cap_s, base_s * (2.0 ** (attempt - 1)))
    return exp / 2.0 + random.uniform(0, exp / 2.0)


class CircuitBreaker:
    """closed → open → half-open → closed, per backend+model.

    - closed: requests flow; ``failure_threshold`` trip-eligible failures
      open the breaker.
    - open: ``should_allow()`` returns False (fail fast) until
      ``reset_timeout_s`` elapses.
    - half-open: exactly one probe request is admitted; success closes the
      breaker, failure re-opens it immediately.
    """

    def __init__(self, *, failure_threshold: int = 5, reset_timeout_s: float = 30.0):
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout_s
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def should_allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self._reset_timeout:
                    self._state = "half-open"
                    return True
                return False
            return False  # half-open: a probe is already in flight

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def record_transport_error(self, exc: TransportError) -> None:
        if not exc.trip_breaker:
            return
        with self._lock:
            self._failures += 1
            if self._state == "half-open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(
    backend_name: str,
    model: str | None,
    *,
    failure_threshold: int,
    reset_timeout_s: float,
) -> CircuitBreaker:
    """Process-wide breaker registry, keyed per backend+model."""
    key = f"{backend_name}:{model or '-'}"
    with _breakers_lock:
        breaker = _breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=failure_threshold,
                reset_timeout_s=reset_timeout_s,
            )
            _breakers[key] = breaker
        return breaker


def reset_breakers_for_tests() -> None:
    """Clear the process-wide breaker registry. Tests only."""
    with _breakers_lock:
        _breakers.clear()


_bulkheads: dict[str, threading.Semaphore] = {}
_bulkheads_lock = threading.Lock()


def get_bulkhead(backend_name: str, max_concurrent: int) -> threading.Semaphore:
    """Process-wide per-backend concurrency cap (the bulkhead)."""
    with _bulkheads_lock:
        semaphore = _bulkheads.get(backend_name)
        if semaphore is None:
            semaphore = threading.Semaphore(max_concurrent)
            _bulkheads[backend_name] = semaphore
        return semaphore


def reset_bulkheads_for_tests() -> None:
    """Clear the process-wide bulkhead registry. Tests only."""
    with _bulkheads_lock:
        _bulkheads.clear()
