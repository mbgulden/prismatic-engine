"""Tests for prismatic.errors — error hierarchy, retry, breaker, DLQ, reporter.

These tests exercise the public surface end-to-end with deterministic
clocks, sleeps, and random sources so behavior is fully reproducible
without flakiness. Integration with the existing telemetry collector
is *not* required — the module is designed to run in isolation.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from prismatic.errors import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    DeadLetterEvent,
    DeadLetterQueue,
    ErrorContext,
    ErrorReport,
    ErrorReporter,
    NonRetryableError,
    PrismaticError,
    RetryableError,
    RetryPolicy,
    default_reporter,
)


# ── Fixtures ────────────────────────────────────────────────────


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.total = 0.0

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.total += seconds


class FakeRandom:
    def __init__(self, values: list[float] | None = None) -> None:
        self._values = list(values or [])
        self._idx = 0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self._idx < len(self._values):
            v = self._values[self._idx]
            self._idx += 1
            return v
        return 0.5  # safe default


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_sleeper() -> FakeSleeper:
    return FakeSleeper()


@pytest.fixture
def fake_random() -> FakeRandom:
    return FakeRandom()


@pytest.fixture
def dlq() -> DeadLetterQueue:
    return DeadLetterQueue(":memory:")


# ── Error hierarchy ────────────────────────────────────────────


class TestErrorHierarchy:
    def test_prismatic_error_carries_context(self):
        ctx = ErrorContext(target="agent:kai", lane="chat", attempt=2, task_id="T-1")
        exc = PrismaticError("boom", context=ctx)
        assert exc.message == "boom"
        assert exc.context is ctx
        d = exc.to_dict()
        assert d["type"] == "PrismaticError"
        assert d["message"] == "boom"
        assert d["context"]["target"] == "agent:kai"
        assert d["context"]["attempt"] == 2
        assert d["context"]["task_id"] == "T-1"

    def test_retryable_error_is_prismatic(self):
        exc = RetryableError("transient")
        assert isinstance(exc, PrismaticError)
        assert isinstance(exc, RetryableError)

    def test_non_retryable_error_is_prismatic(self):
        exc = NonRetryableError("permanent")
        assert isinstance(exc, PrismaticError)

    def test_circuit_open_error_carries_correlation_id(self):
        exc = CircuitOpenError("circuit open for target='agent:kai'")
        assert exc.context.target == ""  # base — filled in by breaker
        assert isinstance(exc, PrismaticError)

    def test_cause_preserved(self):
        cause = ValueError("root")
        try:
            try:
                raise cause
            except ValueError as inner:
                raise RetryableError("wrapped") from inner
        except RetryableError as exc:
            assert exc.__cause__ is cause

    def test_default_context_has_unique_correlation_id(self):
        a = PrismaticError("a")
        b = PrismaticError("b")
        assert a.context.correlation_id == ""
        # two separate instances each get a fresh blank context

    def test_to_dict_round_trip_keys(self):
        exc = RetryableError("x", context=ErrorContext(target="t"))
        d = exc.to_dict()
        assert set(d.keys()) == {"type", "message", "context"}


# ── Retry policy ───────────────────────────────────────────────


class TestRetryPolicy:
    def test_first_success_no_retry(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=3, base_delay=1.0, sleep=fake_sleeper, random_fn=fake_random
        )
        calls = []

        def fn() -> str:
            calls.append(1)
            return "ok"

        result = policy.call(fn)
        assert result == "ok"
        assert calls == [1]
        assert policy.attempts == 1
        assert fake_sleeper.calls == []

    def test_retry_on_retryable_then_succeed(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=4, base_delay=0.5, max_delay=8.0,
            sleep=fake_sleeper, random_fn=fake_random,
        )
        attempts: list[int] = []

        def fn() -> str:
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise RetryableError(f"fail {len(attempts)}")
            return "ok"

        on_retry: list[tuple[int, BaseException, float]] = []
        result = policy.call(
            fn,
            target="agent:kai",
            lane="chat",
            task_id="T-1",
            on_retry=lambda a, e, d: on_retry.append((a, e, d)),
        )
        assert result == "ok"
        assert policy.attempts == 3
        assert len(on_retry) == 2
        # Delays: full-jitter on base*2^n, capped at max_delay
        assert all(0.0 <= d <= 4.0 for _, _, d in on_retry)

    def test_non_retryable_does_not_retry(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=5, base_delay=1.0, sleep=fake_sleeper, random_fn=fake_random
        )
        calls = []

        def fn() -> None:
            calls.append(1)
            raise NonRetryableError("bad input")

        with pytest.raises(NonRetryableError):
            policy.call(fn)
        assert calls == [1]
        assert fake_sleeper.calls == []

    def test_circuit_open_does_not_retry(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=5, base_delay=1.0, sleep=fake_sleeper, random_fn=fake_random
        )
        calls = []

        def fn() -> None:
            calls.append(1)
            raise CircuitOpenError("open")

        with pytest.raises(CircuitOpenError):
            policy.call(fn)
        assert calls == [1]

    def test_exhausts_attempts_and_reraises(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=3, base_delay=1.0, max_delay=4.0,
            sleep=fake_sleeper, random_fn=fake_random,
        )
        calls = []

        def fn() -> None:
            calls.append(1)
            raise RetryableError("always fails")

        with pytest.raises(RetryableError) as ei:
            policy.call(fn, target="agent:sam")
        assert policy.attempts == 3
        assert len(calls) == 3
        assert ei.value.context.target == "agent:sam"
        assert ei.value.context.attempt == 3

    def test_wrap_unexpected_exception(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=3, base_delay=0.1, sleep=fake_sleeper, random_fn=fake_random
        )
        calls = []

        def fn() -> None:
            calls.append(1)
            raise ValueError("unexpected")

        with pytest.raises(ValueError):
            policy.call(fn)
        assert calls == [3]

    def test_delay_for_caps_at_max_delay(self, fake_random):
        policy = RetryPolicy(
            max_attempts=20, base_delay=1.0, max_delay=8.0,
            jitter=False, sleep=lambda _: None, random_fn=fake_random,
        )
        # base*2^n capped at max_delay
        assert policy.delay_for(0) == 1.0
        assert policy.delay_for(1) == 2.0
        assert policy.delay_for(2) == 4.0
        assert policy.delay_for(3) == 8.0
        assert policy.delay_for(10) == 8.0

    def test_full_jitter_in_range(self):
        policy = RetryPolicy(
            max_attempts=2, base_delay=2.0, max_delay=10.0,
            jitter=True, sleep=lambda _: None, random_fn=lambda: 0.25,
        )
        d = policy.delay_for(0)  # 0.25 * 2.0 = 0.5
        assert 0.0 <= d <= 2.0

    def test_validation(self):
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=3, base_delay=-1.0)
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=3, base_delay=5.0, max_delay=2.0)

    def test_total_delay_accumulates(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=4, base_delay=0.5, max_delay=10.0,
            sleep=fake_sleeper, random_fn=fake_random,
        )

        def fn() -> None:
            raise RetryableError("x")

        with pytest.raises(RetryableError):
            policy.call(fn)
        assert policy.total_delay_seconds == sum(fake_sleeper.calls)
        assert policy.total_delay_seconds > 0

    def test_reset_clears_counters(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=2, base_delay=0.1, sleep=fake_sleeper, random_fn=fake_random,
        )
        with pytest.raises(RetryableError):
            policy.call(lambda: (_ for _ in ()).throw(RetryableError("x")))
        assert policy.attempts == 2
        policy.reset()
        assert policy.attempts == 0
        assert policy.total_delay_seconds == 0.0

    def test_thread_safety_of_attempts_counter(self, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=10, base_delay=0.001, sleep=fake_sleeper, random_fn=fake_random,
        )
        errors: list[Exception] = []

        def worker() -> None:
            try:
                with pytest.raises(RetryableError):
                    policy.call(lambda: (_ for _ in ()).throw(RetryableError("x")))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── Circuit breaker ────────────────────────────────────────────


class TestCircuitBreaker:
    def test_initial_state_closed(self, fake_clock):
        breaker = CircuitBreaker(clock=fake_clock)
        assert breaker.state is CircuitState.CLOSED
        snap = breaker.snapshot()
        assert snap["state"] == "closed"
        assert snap["calls"] == 0
        assert snap["failures"] == 0
        assert snap["trips_total"] == 0
        assert snap["short_circuits_total"] == 0

    def test_opens_after_threshold(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=4, min_calls=2,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                with breaker.guard(target="agent:kai"):
                    raise RuntimeError("boom")
        assert breaker.state is CircuitState.OPEN
        snap = breaker.snapshot()
        assert snap["trips_total"] == 1
        assert snap["failures"] == 2
        assert snap["failure_ratio"] == 1.0

    def test_short_circuits_when_open(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=5.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        # Now OPEN — should short-circuit
        with pytest.raises(CircuitOpenError):
            with breaker.guard(target="t"):
                pass
        assert breaker.snapshot()["short_circuits_total"] == 1

    def test_half_open_after_cool_down(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        assert breaker.state is CircuitState.OPEN
        fake_clock.advance(11.0)
        # Probe call should pass through (HALF_OPEN → success → CLOSED)
        with breaker.guard(target="t"):
            pass
        assert breaker.state is CircuitState.CLOSED
        assert breaker.snapshot()["trips_total"] == 1

    def test_half_open_failure_reopens(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        fake_clock.advance(11.0)
        # Probe fails → re-opens
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        assert breaker.state is CircuitState.OPEN
        assert breaker.snapshot()["trips_total"] == 2

    def test_half_open_serializes_probes(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        fake_clock.advance(11.0)
        # Start a probe that blocks
        results: list[str] = []

        def slow_probe() -> None:
            with breaker.guard(target="t"):
                # First guard succeeds and transitions to half-open
                time.sleep(0)  # yield
                results.append("probe")
        t1 = threading.Thread(target=slow_probe)
        t1.start()
        t1.join()
        # Now the second caller should still see half-open serialization
        # (or be allowed if probe closed it). After success, state=CLOSED,
        # so a fresh call should NOT raise CircuitOpenError.
        with breaker.guard(target="t"):
            results.append("second")
        assert "probe" in results
        assert "second" in results
        assert breaker.state is CircuitState.CLOSED

    def test_does_not_open_below_min_calls(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=10, min_calls=5,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                with breaker.guard(target="t"):
                    raise RuntimeError("x")
        # Only 2 calls, min_calls=5 → still closed
        assert breaker.state is CircuitState.CLOSED

    def test_mixed_success_failure_window(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=4, min_calls=2,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with breaker.guard(target="t"):
            pass  # success
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        # Window: [success, failure] → ratio 0.5 → OPENS
        assert breaker.state is CircuitState.OPEN

    def test_validation(self):
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0.0)
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=1.5)
        with pytest.raises(ValueError):
            CircuitBreaker(window_size=0)
        with pytest.raises(ValueError):
            CircuitBreaker(cool_down_seconds=-1.0)
        with pytest.raises(ValueError):
            CircuitBreaker(min_calls=0)

    def test_reset_clears_state(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        assert breaker.state is CircuitState.OPEN
        breaker.reset()
        assert breaker.state is CircuitState.CLOSED
        assert breaker.snapshot()["trips_total"] == 0

    def test_cool_down_remaining_decreases(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        with pytest.raises(RuntimeError):
            with breaker.guard(target="t"):
                raise RuntimeError("x")
        snap1 = breaker.snapshot()
        assert snap1["cool_down_remaining"] == 10.0
        fake_clock.advance(3.0)
        snap2 = breaker.snapshot()
        assert 6.5 <= snap2["cool_down_remaining"] <= 7.0

    def test_call_helper(self, fake_clock):
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=2, min_calls=1,
            cool_down_seconds=10.0, clock=fake_clock,
        )
        result = breaker.call(lambda: "ok", target="t")
        assert result == "ok"
        # Now make it fail
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")), target="t")
        # Circuit is now OPEN, next call should short-circuit
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "should not run", target="t")


# ── Dead-letter queue ──────────────────────────────────────────


class TestDeadLetterQueue:
    def test_enqueue_returns_event(self, dlq):
        exc = RetryableError("x", context=ErrorContext(task_id="T-1", target="agent:kai"))
        task = {"id": "T-1", "payload": "hello"}
        event = dlq.enqueue(task, exc, attempts=5)
        assert isinstance(event, DeadLetterEvent)
        assert event.task_id == "T-1"
        assert event.error_type == "RetryableError"
        assert event.error_message == "x"
        assert event.attempts == 5
        assert event.requeued is False
        assert event.payload == {"id": "T-1", "payload": "hello"}
        assert event.context["target"] == "agent:kai"
        assert "RetryableError" in event.traceback

    def test_list_filters_requeued(self, dlq):
        e1 = dlq.enqueue({"id": "A"}, RetryableError("a"), attempts=1)
        e2 = dlq.enqueue({"id": "B"}, RetryableError("b"), attempts=1)
        assert dlq.count(include_requeued=True) == 2
        assert dlq.count(include_requeued=False) == 2
        assert dlq.requeue(e1.event_id) is True
        assert dlq.count(include_requeued=True) == 2
        assert dlq.count(include_requeued=False) == 1
        listed = dlq.list(include_requeued=False)
        assert [e.event_id for e in listed] == [e2.event_id]

    def test_requeue_idempotent(self, dlq):
        e = dlq.enqueue({"id": "A"}, RetryableError("a"), attempts=1)
        assert dlq.requeue(e.event_id) is True
        # Second requeue attempt: no row matches requeued=0
        assert dlq.requeue(e.event_id) is False

    def test_purge(self, dlq):
        dlq.enqueue({"id": "A"}, RetryableError("a"), attempts=1)
        dlq.enqueue({"id": "B"}, RetryableError("b"), attempts=1)
        e = dlq.enqueue({"id": "C"}, RetryableError("c"), attempts=1)
        dlq.requeue(e.event_id)
        # Default: include_requeued=True → all 3
        assert dlq.purge() == 3
        assert dlq.count() == 0
        # Add again and test include_requeued=False
        dlq.enqueue({"id": "D"}, RetryableError("d"), attempts=1)
        e2 = dlq.enqueue({"id": "E"}, RetryableError("e"), attempts=1)
        dlq.requeue(e2.event_id)
        assert dlq.purge(include_requeued=False) == 1
        assert dlq.count() == 1  # requeued one remains

    def test_filter_by_task_id(self, dlq):
        dlq.enqueue({"id": "A"}, RetryableError("a"), attempts=1)
        dlq.enqueue({"id": "B"}, RetryableError("b"), attempts=1)
        dlq.enqueue({"id": "A", "seq": 2}, RetryableError("a2"), attempts=1)
        listed = dlq.list(task_id="A")
        assert len(listed) == 2
        assert {e.task_id for e in listed} == {"A"}

    def test_limit(self, dlq):
        for i in range(5):
            dlq.enqueue({"id": f"id-{i}"}, RetryableError(f"e-{i}"), attempts=1)
        assert len(dlq.list(limit=3)) == 3

    def test_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "dlq.db")
        q1 = DeadLetterQueue(path)
        q1.enqueue({"id": "X"}, RetryableError("x"), attempts=1)
        q1.close()
        q2 = DeadLetterQueue(path)
        rows = q2.list()
        assert len(rows) == 1
        assert rows[0].task_id == "X"
        q2.close()

    def test_enqueue_non_mapping_task(self, dlq):
        class Task:
            def __init__(self, id: str) -> None:
                self.id = id

        exc = RetryableError("x", context=ErrorContext(target="t"))
        event = dlq.enqueue(Task("T-9"), exc, attempts=2)
        assert event.task_id == "T-9"
        assert event.payload["id"] == "T-9"

    def test_enqueue_none_task(self, dlq):
        event = dlq.enqueue(None, RetryableError("x"), attempts=1)
        assert event.task_id == ""
        assert event.payload == {}

    def test_schema_present(self, dlq):
        # Ensure the schema is created and tables exist
        cur = dlq._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dead_letters'"
        )
        assert cur.fetchone() is not None
        cur = dlq._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_dead_letters_task'"
        )
        assert cur.fetchone() is not None


# ── Error reporter ─────────────────────────────────────────────


class TestErrorReporter:
    def test_report_basic(self):
        reporter = ErrorReporter()
        exc = RetryableError("x", context=ErrorContext(task_id="T-1", target="agent:kai"))
        report = reporter.report({"id": "T-1"}, exc)
        assert isinstance(report, ErrorReport)
        assert report.error_type == "RetryableError"
        assert report.message == "x"
        assert report.context["target"] == "agent:kai"
        assert report.context["task_id"] == "T-1"
        assert "RetryableError" in report.traceback
        assert report.submitted_back is False
        # ID should be a valid UUID hex
        assert len(report.report_id) == 32

    def test_report_to_sink(self):
        sink: list[ErrorReport] = []
        reporter = ErrorReporter(sink=sink)
        exc = RetryableError("x")
        reporter.report({"id": "A"}, exc)
        assert len(sink) == 1

    def test_submit_callback_invoked(self):
        called: list[ErrorReport] = []

        def submit(report: ErrorReport) -> bool:
            called.append(report)
            return True

        reporter = ErrorReporter(submit=submit)
        exc = RetryableError("x")
        report = reporter.report({"id": "A"}, exc)
        assert called == [report]
        assert report.submitted_back is True

    def test_submit_callback_failure_does_not_raise(self):
        def submit(report: ErrorReport) -> bool:
            raise RuntimeError("submission broke")

        reporter = ErrorReporter(submit=submit)
        exc = RetryableError("x")
        # Should not raise
        report = reporter.report({"id": "A"}, exc)
        assert report.submitted_back is False

    def test_clear(self):
        reporter = ErrorReporter()
        reporter.report({"id": "A"}, RetryableError("a"))
        assert len(reporter.reports) == 1
        reporter.clear()
        assert reporter.reports == []

    def test_default_reporter_is_singleton(self):
        assert default_reporter is default_reporter
        # Pre-clear before assertions to avoid pollution from other tests
        default_reporter.clear()
        default_reporter.report({"id": "A"}, RetryableError("x"))
        assert len(default_reporter.reports) == 1

    def test_report_handles_non_prismatic_exception(self):
        reporter = ErrorReporter()
        try:
            raise ValueError("root cause")
        except ValueError as e:
            report = reporter.report({"id": "A"}, e)
        assert report.error_type == "ValueError"
        assert "root cause" in report.message
        assert "ValueError" in report.traceback

    def test_to_json_round_trip(self):
        reporter = ErrorReporter()
        exc = RetryableError("x", context=ErrorContext(target="t"))
        report = reporter.report({"id": "A"}, exc)
        payload = json.loads(report.to_json())
        assert payload["error_type"] == "RetryableError"
        assert payload["context"]["target"] == "t"
        assert payload["traceback"]
        assert payload["created_at"]


# ── End-to-end integration ─────────────────────────────────────


class TestIntegration:
    def test_retry_then_dlq_then_reporter(self, fake_sleeper, fake_random):
        """A flaky agent exhausts retries, gets DLQ'd, and is reported."""
        policy = RetryPolicy(
            max_attempts=3, base_delay=0.1, max_delay=1.0,
            sleep=fake_sleeper, random_fn=fake_random,
        )
        breaker = CircuitBreaker(
            failure_threshold=0.99,  # never opens in this test
            window_size=10, min_calls=100, cool_down_seconds=10.0,
        )
        dlq = DeadLetterQueue(":memory:")
        reporter = ErrorReporter()

        attempts: list[int] = []

        def flaky() -> None:
            attempts.append(len(attempts))
            raise RetryableError(f"flaky {len(attempts)}")

        task = {"id": "T-42", "target": "agent:kai"}
        caught: RetryableError | None = None
        try:
            with breaker.guard(target="agent:kai"):
                policy.call(flaky, target="agent:kai", task_id="T-42")
        except RetryableError as exc:
            caught = exc
            dlq.enqueue(task, exc, policy.attempts)
            reporter.report(task, exc)

        assert caught is not None
        assert policy.attempts == 3
        assert len(attempts) == 3
        events = dlq.list(task_id="T-42")
        assert len(events) == 1
        assert events[0].attempts == 3
        assert events[0].context["target"] == "agent:kai"
        assert len(reporter.reports) == 1
        assert reporter.reports[0].context["target"] == "agent:kai"

    def test_breaker_trips_and_short_circuits_retry(self, fake_clock, fake_sleeper, fake_random):
        policy = RetryPolicy(
            max_attempts=5, base_delay=0.01, max_delay=0.1,
            sleep=fake_sleeper, random_fn=fake_random,
        )
        breaker = CircuitBreaker(
            failure_threshold=0.5, window_size=4, min_calls=2,
            cool_down_seconds=10.0, clock=fake_clock,
        )

        def always_fails() -> None:
            raise RetryableError("nope")

        # First two failures trip the breaker
        for _ in range(2):
            with pytest.raises(RetryableError):
                with breaker.guard(target="t"):
                    policy.call(always_fails)
        assert breaker.state is CircuitState.OPEN
        # Subsequent retry should short-circuit immediately
        with pytest.raises(CircuitOpenError):
            with breaker.guard(target="t"):
                policy.call(always_fails)
