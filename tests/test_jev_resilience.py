"""Tests for resilience: classification, backoff, breaker, bulkhead."""

import threading
import time

import pytest

from prismatic.jev.errors import TransportError
from prismatic.jev.resilience import (
    backoff_delay,
    classify_connection_error,
    classify_http_error,
    get_breaker,
    get_bulkhead,
    reset_breakers_for_tests,
    reset_bulkheads_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_registries():
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()
    yield
    reset_breakers_for_tests()
    reset_bulkheads_for_tests()


def _unique(prefix: str) -> str:
    return f"{prefix}-{time.monotonic_ns()}"


# -- classification ------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses(status):
    err = classify_http_error(status, {})
    assert err.transient is True
    assert isinstance(err, TransportError)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_statuses(status):
    err = classify_http_error(status, {})
    assert err.transient is False


def test_5xx_trips_breaker_but_429_does_not():
    assert classify_http_error(500, {}).trip_breaker is True
    assert classify_http_error(429, {}).trip_breaker is False
    assert classify_http_error(400, {}).trip_breaker is False


def test_retry_after_seconds_parsed():
    err = classify_http_error(429, {"Retry-After": "2"})
    assert err.retry_after_s == 2.0


def test_retry_after_http_date_parsed():
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 30))
    err = classify_http_error(429, {"Retry-After": future})
    assert err.retry_after_s is not None
    assert 0 < err.retry_after_s <= 35


def test_retry_after_garbage_is_none():
    err = classify_http_error(429, {"Retry-After": "not-a-time"})
    assert err.retry_after_s is None


def test_connection_errors_are_transient_and_trip_breaker():
    for exc in (TimeoutError("t"), ConnectionError("c"), OSError("o")):
        err = classify_connection_error(exc)
        assert err.transient is True
        assert err.trip_breaker is True
        assert err.status is None


# -- backoff --------------------------------------------------------------


def test_backoff_retry_after_honored_within_cap():
    assert backoff_delay(attempt=1, base_s=0.2, cap_s=5.0, retry_after_s=2.0) == 2.0


def test_backoff_retry_after_capped():
    assert backoff_delay(attempt=1, base_s=0.2, cap_s=5.0, retry_after_s=120.0) == 5.0


def test_backoff_grows_with_attempts_and_respects_cap():
    small = max(
        backoff_delay(attempt=1, base_s=0.2, cap_s=5.0, retry_after_s=None)
        for _ in range(50)
    )
    assert 0.0 <= small <= 0.2
    for _ in range(50):
        d = backoff_delay(attempt=10, base_s=0.2, cap_s=5.0, retry_after_s=None)
        assert 0.0 <= d <= 5.0


# -- circuit breaker -------------------------------------------------------


def test_breaker_closed_allows():
    b = get_breaker("be", _unique("m"), failure_threshold=2, reset_timeout_s=30.0)
    assert b.should_allow() is True
    assert b.state == "closed"


def test_breaker_opens_after_threshold_trip_failures():
    b = get_breaker("be", _unique("m"), failure_threshold=2, reset_timeout_s=30.0)
    b.record_transport_error(classify_http_error(500, {}))
    assert b.state == "closed"
    b.record_transport_error(classify_http_error(500, {}))
    assert b.state == "open"
    assert b.should_allow() is False


def test_breaker_ignores_non_trip_errors():
    b = get_breaker("be", _unique("m"), failure_threshold=2, reset_timeout_s=30.0)
    for _ in range(10):
        b.record_transport_error(classify_http_error(400, {}))
        b.record_transport_error(classify_http_error(429, {}))
    assert b.state == "closed"
    assert b.should_allow() is True


def test_breaker_half_open_probe_and_close():
    b = get_breaker("be", _unique("m"), failure_threshold=1, reset_timeout_s=0.05)
    b.record_transport_error(classify_http_error(500, {}))
    assert b.state == "open"
    time.sleep(0.06)
    assert b.should_allow() is True  # half-open probe admitted
    assert b.should_allow() is False  # second call while probing: not admitted
    b.record_success()
    assert b.state == "closed"
    assert b.should_allow() is True


def test_breaker_probe_failure_reopens():
    b = get_breaker("be", _unique("m"), failure_threshold=1, reset_timeout_s=0.05)
    b.record_transport_error(classify_http_error(500, {}))
    time.sleep(0.06)
    assert b.should_allow() is True
    b.record_transport_error(classify_http_error(500, {}))
    assert b.state == "open"
    assert b.should_allow() is False


def test_breakers_are_per_backend_model():
    b1 = get_breaker("openrouter", "m1", failure_threshold=1, reset_timeout_s=30.0)
    b2 = get_breaker("typesafe", "m1", failure_threshold=1, reset_timeout_s=30.0)
    assert b1 is not b2
    b1.record_transport_error(classify_http_error(500, {}))
    assert b1.state == "open"
    assert b2.state == "closed"


def test_get_breaker_returns_same_instance():
    name = _unique("m")
    b1 = get_breaker("be", name, failure_threshold=3, reset_timeout_s=30.0)
    b2 = get_breaker("be", name, failure_threshold=3, reset_timeout_s=30.0)
    assert b1 is b2


# -- bulkhead ---------------------------------------------------------------


def test_bulkhead_caps_concurrency():
    sem = get_bulkhead(_unique("be"), 2)
    assert sem.acquire(blocking=False) is True
    assert sem.acquire(blocking=False) is True
    assert sem.acquire(blocking=False) is False
    sem.release()
    assert sem.acquire(blocking=False) is True
    sem.release()
    sem.release()


def test_bulkhead_is_per_backend():
    s1 = get_bulkhead(_unique("a"), 1)
    s2 = get_bulkhead(_unique("b"), 1)
    assert s1.acquire(blocking=False) is True
    assert s2.acquire(blocking=False) is True
    s1.release()
    s2.release()


def test_bulkhead_blocks_threads_when_full():
    sem = get_bulkhead(_unique("be"), 1)
    sem.acquire()
    entered = threading.Event()

    def worker():
        if sem.acquire(timeout=5):
            entered.set()
            sem.release()

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    assert not entered.is_set()
    sem.release()
    t.join(timeout=5)
    assert entered.is_set()
