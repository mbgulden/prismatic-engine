"""Comprehensive error handling for Prismatic Engine.

Provides four cooperating subsystems:

* :class:`PrismaticError` hierarchy — typed exceptions carrying structured
  ``ErrorContext`` so callers can recover, retry, or escalate based on
  the failure mode rather than the message string.

* :class:`RetryPolicy` — exponential backoff with full jitter, max-attempt
  caps, retryable-status allowlist, and per-call invocation counters. The
  policy is *pure* (no I/O) so it can be reused outside the network layer
  for agent retries, queue consumers, and the rfr loop.

* :class:`CircuitBreaker` — per-target (agent/host/lane) three-state
  breaker (closed/open/half-open) backed by a sliding failure window. When
  the failure rate exceeds a configured threshold the breaker opens and
  short-circuits further calls with :class:`CircuitOpenError` until a
  cool-down elapses; a single half-open probe decides whether to close
  the breaker again.

* :class:`DeadLetterQueue` — durable SQLite-backed queue for tasks that
  exhaust their retry budget or hit a non-retryable error. The DLQ is
  inspectable, requeueable, and emits :class:`DeadLetterEvent` records
  that integrate with the existing telemetry collector when present.

* :class:`ErrorReporter` — single entry point that converts a raised
  exception into a structured :class:`ErrorReport` (JSON-safe) and posts
  it back to the task submitter. In production this hits the IPC bridge
  or webhook; in tests it falls back to an in-memory sink.

The module is dependency-free at the core (stdlib only). Optional
integration with ``prismatic.telemetry.TelemetryCollector`` is best-effort
and gated by importability so tests can run in isolation.

Usage::

    from prismatic.errors import (
        RetryPolicy, CircuitBreaker, DeadLetterQueue,
        ErrorReporter, RetryableError, CircuitOpenError,
    )

    policy = RetryPolicy(max_attempts=5, base_delay=0.5, max_delay=8.0)
    breaker = CircuitBreaker(failure_threshold=3, cool_down_seconds=30)
    dlq = DeadLetterQueue(":memory:")
    reporter = ErrorReporter()

    try:
        with breaker.guard(target="agent:kai"):
            result = policy.call(lambda: dispatch_to_kai(task))
    except RetryableError as exc:
        dlq.enqueue(task, exc, policy.attempts)
    except CircuitOpenError as exc:
        reporter.report(task, exc)
"""

from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Deque, Iterable, Iterator, Mapping, TypeVar

__all__ = [
    "PrismaticError",
    "RetryableError",
    "NonRetryableError",
    "CircuitOpenError",
    "DeadLetterEvent",
    "ErrorContext",
    "ErrorReport",
    "RetryPolicy",
    "CircuitBreaker",
    "CircuitState",
    "DeadLetterQueue",
    "ErrorReporter",
    "default_reporter",
]


# ── Error hierarchy ───────────────────────────────────────────────


class PrismaticError(Exception):
    """Base class for all Prismatic-engine errors.

    Carries an :class:`ErrorContext` payload so callers can route,
    retry, or escalate based on structured fields instead of
    message-string parsing.
    """

    def __init__(
        self,
        message: str = "",
        *,
        context: "ErrorContext | None" = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or ErrorContext()
        if cause is not None:
            self.__cause__ = cause

    # Make the context accessible even when the exception is re-raised
    # across thread boundaries (Exception.args is the only standard
    # pickle-friendly slot).
    def __reduce__(self):  # pragma: no cover - pickle is best-effort
        return (type(self), (self.message,), {"context": self.context})

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "message": self.message,
            "context": self.context.to_dict(),
        }


class RetryableError(PrismaticError):
    """Transient failure that the caller may try again.

    Examples: timeout, 503, rate-limit, network reset.
    """


class NonRetryableError(PrismaticError):
    """Permanent failure. Retrying will not help.

    Examples: 4xx auth/validation errors, malformed payload.
    """


class CircuitOpenError(PrismaticError):
    """The breaker for the target is open; call short-circuited.

    Distinct from a :class:`RetryableError` so callers can render a
    "service unavailable, try later" UX without re-entering the
    retry budget.
    """


# ── Structured payloads ──────────────────────────────────────────


@dataclass
class ErrorContext:
    """Structured metadata attached to every :class:`PrismaticError`."""

    target: str = ""
    lane: str = ""
    attempt: int = 0
    task_id: str = ""
    correlation_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "lane": self.lane,
            "attempt": self.attempt,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "extra": dict(self.extra),
        }


@dataclass
class ErrorReport:
    """JSON-safe error summary suitable for IPC / webhook / log lines."""

    report_id: str
    error_type: str
    message: str
    context: dict[str, Any]
    traceback: str
    created_at: str
    submitted_back: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class DeadLetterEvent:
    """One record persisted in the dead-letter queue."""

    event_id: str
    task_id: str
    error_type: str
    error_message: str
    attempts: int
    enqueued_at: str
    payload: dict[str, Any]
    context: dict[str, Any]
    traceback: str
    requeued: bool = False
    requeued_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Retry policy ────────────────────────────────────────────────


T = TypeVar("T")


class RetryPolicy:
    """Exponential backoff with full jitter.

    The policy wraps a callable and re-invokes it on
    :class:`RetryableError` up to ``max_attempts`` total invocations.
    :class:`NonRetryableError` and :class:`CircuitOpenError` propagate
    immediately (no retry). Any other exception is treated as
    retryable to preserve the previous best-effort behavior of the
    network-level ``guarded_request`` helper.

    ``attempts`` is exposed on the instance so callers can surface
    the retry count in dead-letter records or error reports.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        jitter: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_delay < 0 or max_delay < 0:
            raise ValueError("delays must be non-negative")
        if max_delay < base_delay:
            raise ValueError("max_delay must be >= base_delay")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self._sleep = sleep
        self._random = random_fn
        self._clock = clock
        self.attempts = 0
        self.total_delay_seconds = 0.0
        self._reset_lock = threading.Lock()

    # ── Public API ──

    def reset(self) -> None:
        with self._reset_lock:
            self.attempts = 0
            self.total_delay_seconds = 0.0

    def delay_for(self, attempt_index: int) -> float:
        """Return the backoff delay before retry *attempt_index* (0-based)."""
        if attempt_index < 0:
            return 0.0
        cap = max(self.base_delay, self.max_delay)
        # 2**attempt_index * base, capped
        raw = min(cap, self.base_delay * (2 ** attempt_index))
        if self.jitter:
            # "Full jitter": uniform in [0, raw].
            return self._random() * raw
        return raw

    def call(
        self,
        fn: Callable[..., T],
        *args: Any,
        target: str = "",
        lane: str = "",
        task_id: str = "",
        correlation_id: str = "",
        on_retry: Callable[[int, BaseException, float], None] | None = None,
        **kwargs: Any,
    ) -> T:
        """Invoke *fn* with retries. Returns the successful result or
        re-raises the final exception.
        """
        self.reset()
        last_exc: BaseException | None = None
        for attempt in range(self.max_attempts):
            self.attempts = attempt + 1
            try:
                return fn(*args, **kwargs)
            except NonRetryableError:
                raise
            except CircuitOpenError:
                raise
            except PrismaticError as exc:
                last_exc = exc
                # Attach context on the first failure so DLQ records
                # carry the originating target/lane even when the call
                # exhausts its budget on the first try.
                self._attach_attempt(exc, attempt + 1, target, lane, task_id, correlation_id)
                if not isinstance(exc, RetryableError):
                    # Treat unknown PrismaticErrors as non-retryable.
                    raise
                if attempt + 1 >= self.max_attempts:
                    raise
                delay = self.delay_for(attempt)
                self.total_delay_seconds += delay
                if on_retry is not None:
                    on_retry(attempt + 1, exc, delay)
                self._sleep(delay)
            except Exception as exc:  # noqa: BLE001 — legacy best-effort retry
                last_exc = exc
                if attempt + 1 >= self.max_attempts:
                    raise
                wrapped = RetryableError(str(exc), cause=exc)
                self._attach_attempt(
                    wrapped, attempt + 1, target, lane, task_id, correlation_id
                )
                delay = self.delay_for(attempt)
                self.total_delay_seconds += delay
                if on_retry is not None:
                    on_retry(attempt + 1, wrapped, delay)
                self._sleep(delay)
        # Defensive — loop should always either return or raise.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("RetryPolicy.call exited without result")  # pragma: no cover

    # ── Helpers ──

    def _attach_attempt(
        self,
        exc: PrismaticError,
        attempt: int,
        target: str,
        lane: str,
        task_id: str,
        correlation_id: str,
    ) -> None:
        # Caller-supplied context always wins on the FIRST retry attempt
        # so DLQ records carry the originating target. On subsequent
        # attempts we only fill blanks — the exception's own context
        # takes priority over the policy's defaults.
        if attempt <= 1:
            if target:
                exc.context.target = target
            if lane:
                exc.context.lane = lane
            if task_id and not exc.context.task_id:
                exc.context.task_id = task_id
            if correlation_id and not exc.context.correlation_id:
                exc.context.correlation_id = correlation_id
        else:
            if not exc.context.target and target:
                exc.context.target = target
            if not exc.context.lane and lane:
                exc.context.lane = lane
            if task_id and not exc.context.task_id:
                exc.context.task_id = task_id
            if correlation_id and not exc.context.correlation_id:
                exc.context.correlation_id = correlation_id
        exc.context.attempt = attempt


# ── Circuit breaker ─────────────────────────────────────────────


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-target three-state circuit breaker.

    Failure tracking uses a sliding window (``window_size`` most-recent
    outcomes). When the failure ratio in the window reaches
    ``failure_threshold`` (0.0–1.0) the breaker opens for
    ``cool_down_seconds``; subsequent :meth:`guard` calls raise
    :class:`CircuitOpenError` immediately. After the cool-down a single
    probe is allowed through in ``HALF_OPEN`` state; success closes the
    breaker, failure re-opens it.

    Half-open probe serialization: while a probe is in flight, additional
    callers are short-circuited with :class:`CircuitOpenError`. The probe
    is tracked via ``_probe_in_flight`` rather than via state to keep
    the state machine simple and the snapshot honest.
    """

    def __init__(
        self,
        *,
        failure_threshold: float = 0.5,
        window_size: int = 10,
        cool_down_seconds: float = 30.0,
        min_calls: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0.0 < failure_threshold <= 1.0:
            raise ValueError("failure_threshold must be in (0, 1]")
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if cool_down_seconds < 0:
            raise ValueError("cool_down_seconds must be >= 0")
        if min_calls < 1:
            raise ValueError("min_calls must be >= 1")
        self.failure_threshold = failure_threshold
        self.window_size = window_size
        self.cool_down_seconds = cool_down_seconds
        self.min_calls = min_calls
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.RLock()
        self._outcomes: Deque[bool] = deque(maxlen=window_size)
        self._state: CircuitState = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._probe_in_flight: bool = False
        self._trips = 0
        self._short_circuits = 0
        self._last_transition: dict[str, Any] = {
            "from": None,
            "to": CircuitState.CLOSED.value,
            "at": 0.0,
        }

    # ── State queries ──

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._maybe_recover_closed()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._maybe_recover_closed()
            window = list(self._outcomes)
            failures = sum(1 for ok in window if not ok)
            calls = len(window)
            ratio = (failures / calls) if calls else 0.0
            return {
                "state": state.value,
                "calls": calls,
                "failures": failures,
                "failure_ratio": ratio,
                "trips_total": self._trips,
                "short_circuits_total": self._short_circuits,
                "cool_down_remaining": self._cool_down_remaining_locked(state),
            }

    # ── Public guard ──

    @contextmanager
    def guard(self, *, target: str = "") -> Iterator[None]:
        state = self._enter(target)
        try:
            yield
        except BaseException as exc:
            self._record_failure(target, exc)
            raise
        else:
            self._record_success(target)

    def call(
        self,
        fn: Callable[..., T],
        *args: Any,
        target: str = "",
        **kwargs: Any,
    ) -> T:
        with self.guard(target=target):
            return fn(*args, **kwargs)

    # ── Manual reset (testing / admin) ──

    def reset(self) -> None:
        with self._lock:
            self._outcomes.clear()
            self._state = CircuitState.CLOSED
            self._opened_at = 0.0
            self._probe_in_flight = False
            self._trips = 0
            self._short_circuits = 0

    # ── Internals ──

    def _enter(self, target: str) -> CircuitState:
        with self._lock:
            state = self._maybe_recover_closed()
            if state is CircuitState.OPEN:
                self._short_circuits += 1
                raise CircuitOpenError(
                    f"circuit open for target={target!r}",
                    context=ErrorContext(
                        target=target,
                        correlation_id=uuid.uuid4().hex,
                    ),
                )
            if state is CircuitState.HALF_OPEN:
                # In HALF_OPEN, exactly ONE probe is allowed through.
                # If a probe is already in flight, short-circuit.
                if self._probe_in_flight:
                    self._short_circuits += 1
                    raise CircuitOpenError(
                        f"circuit half-open probe in flight for target={target!r}",
                        context=ErrorContext(
                            target=target,
                            correlation_id=uuid.uuid4().hex,
                        ),
                    )
                self._probe_in_flight = True
            return state

    def _record_success(self, target: str) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN and self._probe_in_flight:
                self._probe_in_flight = False
                self._transition(CircuitState.CLOSED, "probe_succeeded")
                self._outcomes.clear()
            else:
                self._outcomes.append(True)

    def _record_failure(self, target: str, exc: BaseException) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN and self._probe_in_flight:
                # Probe failed: re-open the breaker, release the flag.
                self._probe_in_flight = False
                self._open_locked(reason="probe_failed")
                return
            self._outcomes.append(False)
            failures = sum(1 for ok in self._outcomes if not ok)
            calls = len(self._outcomes)
            if (
                calls >= self.min_calls
                and (failures / calls) >= self.failure_threshold
            ):
                self._open_locked(reason=f"failure_ratio={failures}/{calls}")

    def _open_locked(self, *, reason: str) -> None:
        if self._state is CircuitState.OPEN:
            return
        self._trips += 1
        self._opened_at = self._clock()
        self._transition(CircuitState.OPEN, reason)

    def _maybe_recover_closed(self) -> CircuitState:
        if self._state is not CircuitState.OPEN:
            return self._state
        if (self._clock() - self._opened_at) >= self.cool_down_seconds:
            self._transition(CircuitState.HALF_OPEN, "cool_down_elapsed")
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def _cool_down_remaining_locked(self, state: CircuitState) -> float:
        if state is not CircuitState.OPEN:
            return 0.0
        elapsed = self._clock() - self._opened_at
        return max(0.0, self.cool_down_seconds - elapsed)

    def _transition(self, to: CircuitState, reason: str) -> None:
        previous = self._state
        self._state = to
        self._last_transition = {
            "from": previous.value,
            "to": to.value,
            "at": self._clock(),
            "reason": reason,
        }


# ── Dead-letter queue ──────────────────────────────────────────


class DeadLetterQueue:
    """SQLite-backed dead-letter queue.

    Records are append-only until requeued. The DLQ is intentionally
    tiny (no indexing, no streaming) — the queue is the auditor of
    last resort, not a hot path. The connection is owned by the
    instance and is safe for use from a single thread; callers that
    need cross-thread access should wrap calls in their own lock.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS dead_letters (
        event_id        TEXT PRIMARY KEY,
        task_id         TEXT NOT NULL,
        error_type      TEXT NOT NULL,
        error_message   TEXT NOT NULL,
        attempts        INTEGER NOT NULL,
        enqueued_at     TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        context_json    TEXT NOT NULL,
        traceback       TEXT NOT NULL,
        requeued        INTEGER NOT NULL DEFAULT 0,
        requeued_at     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_dead_letters_task
        ON dead_letters(task_id);
    CREATE INDEX IF NOT EXISTS idx_dead_letters_requeued
        ON dead_letters(requeued);
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or ":memory:"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(
        self,
        task: Mapping[str, Any] | Any,
        exc: BaseException,
        attempts: int,
        *,
        context: ErrorContext | None = None,
    ) -> DeadLetterEvent:
        ctx = context or (
            exc.context if isinstance(exc, PrismaticError) else ErrorContext()
        )
        event = DeadLetterEvent(
            event_id=uuid.uuid4().hex,
            task_id=str(ctx.task_id or (task.get("id") if isinstance(task, Mapping) else getattr(task, "id", ""))),
            error_type=type(exc).__name__,
            error_message=str(exc),
            attempts=attempts,
            enqueued_at=datetime.now(timezone.utc).isoformat(),
            payload=_coerce_payload(task),
            context=ctx.to_dict(),
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO dead_letters(
                    event_id, task_id, error_type, error_message,
                    attempts, enqueued_at, payload_json, context_json,
                    traceback, requeued
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.error_type,
                    event.error_message,
                    event.attempts,
                    event.enqueued_at,
                    json.dumps(event.payload),
                    json.dumps(event.context),
                    event.traceback,
                ),
            )
            self._conn.commit()
        return event

    def list(
        self,
        *,
        include_requeued: bool = False,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[DeadLetterEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_requeued:
            clauses.append("requeued = 0")
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM dead_letters {where} ORDER BY enqueued_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def requeue(self, event_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE dead_letters
                SET requeued = 1,
                    requeued_at = ?
                WHERE event_id = ? AND requeued = 0
                """,
                (datetime.now(timezone.utc).isoformat(), event_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def purge(self, *, include_requeued: bool = True) -> int:
        with self._lock:
            if include_requeued:
                cur = self._conn.execute("DELETE FROM dead_letters")
            else:
                cur = self._conn.execute("DELETE FROM dead_letters WHERE requeued = 0")
            self._conn.commit()
            return cur.rowcount

    def count(self, *, include_requeued: bool = True) -> int:
        with self._lock:
            if include_requeued:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM dead_letters").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM dead_letters WHERE requeued = 0"
                ).fetchone()
            return int(row["n"])

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DeadLetterEvent:
        return DeadLetterEvent(
            event_id=row["event_id"],
            task_id=row["task_id"],
            error_type=row["error_type"],
            error_message=row["error_message"],
            attempts=int(row["attempts"]),
            enqueued_at=row["enqueued_at"],
            payload=json.loads(row["payload_json"]),
            context=json.loads(row["context_json"]),
            traceback=row["traceback"],
            requeued=bool(row["requeued"]),
            requeued_at=row["requeued_at"],
        )


def _coerce_payload(task: Any) -> dict[str, Any]:
    if task is None:
        return {}
    if isinstance(task, Mapping):
        return {k: _safe(v) for k, v in task.items()}
    if hasattr(task, "__dict__"):
        return {k: _safe(v) for k, v in vars(task).items()}
    return {"repr": repr(task)}


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return repr(value)


# ── Error reporter ─────────────────────────────────────────────


class ErrorReporter:
    """Convert raised exceptions into structured :class:`ErrorReport`s
    and (optionally) post them back to the task submitter.

    The reporter never raises. Webhook delivery is best-effort: a
    failure to deliver is recorded in the report's ``submitted_back``
    flag and never propagated, since the caller is already in an
    error-handling path.
    """

    def __init__(
        self,
        *,
        sink: list[ErrorReport] | None = None,
        submit: Callable[[ErrorReport], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink if sink is not None else []
        self._submit = submit
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def reports(self) -> list[ErrorReport]:
        return list(self._sink)

    def report(
        self,
        task: Any,
        exc: BaseException,
        *,
        context: ErrorContext | None = None,
    ) -> ErrorReport:
        ctx = context or (
            exc.context if isinstance(exc, PrismaticError) else ErrorContext()
        )
        if not ctx.task_id and isinstance(task, Mapping):
            ctx.task_id = str(task.get("id", ""))
        report = ErrorReport(
            report_id=uuid.uuid4().hex,
            error_type=type(exc).__name__,
            message=str(exc),
            context=ctx.to_dict(),
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if self._submit is not None:
            try:
                report.submitted_back = bool(self._submit(report))
            except Exception:  # noqa: BLE001 — never let reporter raise
                report.submitted_back = False
        with self._lock:
            self._sink.append(report)
        return report

    def clear(self) -> None:
        with self._lock:
            self._sink.clear()


default_reporter = ErrorReporter()
"""Module-level reporter used when callers don't provide their own."""
