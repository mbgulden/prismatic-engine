"""Prismatic Quality Gates — Phase 2 / Gap 7: Failure Classification.

Classifies agent task failures into FailureMode categories and maps each
to a retry policy. Replaces the "retry once with same prompt, then drop"
behavior with smart retry that knows the difference between transient
errors, rate limits, shape violations, logic errors, and impossible tasks.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 7)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class FailureMode(Enum):
    """Classification of why a task failed."""
    TRANSIENT = "transient"           # Network blip, momentary I/O — retry
    RATE_LIMIT = "rate_limit"         # API quota exhausted — backoff + retry
    SHAPE_VIOLATION = "shape"         # Task body not AGY-safe — don't retry, escalate
    LOGIC_ERROR = "logic_error"       # Code error in agent output — retry with feedback
    IMPOSSIBLE = "impossible"         # Task cannot be completed — don't retry, escalate


# Linear label constants (referenced by policies below)
TASK_SHAPE_VIOLATION = "task:shape-violation"
OUTPUT_REQUIRES_ATTENTION = "output:requires-attention"
AGENT_FRED_LABEL = "agent:fred"


# Pattern → FailureMode mapping (order matters: first match wins)
# Notes:
#   - Use word boundaries (\\b) to avoid matching substrings like 'retry_handler'
#   - Match standard POSIX error messages verbatim
FAILURE_PATTERNS: list[tuple[str, FailureMode, str]] = [
    # RATE_LIMIT patterns — API throttling
    (r"rate.?limit|429.*too many|quota.*exhaust|RESOURCE_EXHAUSTED", FailureMode.RATE_LIMIT, "rate_limit_hit"),
    (r"AGY_QUOTA_EXHAUSTED|GEMINI_QUOTA|AI_ULTRA_QUOTA", FailureMode.RATE_LIMIT, "ai_ultra_quota"),

    # SHAPE_VIOLATION patterns — agent ran forbidden commands
    (r"FORBIDDEN_COMMAND|pytest.*not.*allowed|docker.*blocked|npm.*forbidden", FailureMode.SHAPE_VIOLATION, "forbidden_command"),
    (r"SHAPE_VIOLATION|task:shape-violation", FailureMode.SHAPE_VIOLATION, "shape_label"),

    # IMPOSSIBLE patterns — task cannot succeed
    (r"permission denied|EACCES|EPERM|access denied", FailureMode.IMPOSSIBLE, "permission_denied"),
    (r"does not exist|no such file or directory", FailureMode.IMPOSSIBLE, "missing_dependency"),
    (r"not implemented|unsupported feature|TODO.*implement", FailureMode.IMPOSSIBLE, "feature_missing"),

    # LOGIC_ERROR patterns — code bugs in agent output
    (r"TypeError|SyntaxError|NameError|AttributeError|ImportError", FailureMode.LOGIC_ERROR, "code_error"),
    (r"Traceback \(most recent call last\)", FailureMode.LOGIC_ERROR, "python_traceback"),
    (r"AssertionError|assert.*failed|expected.*got", FailureMode.LOGIC_ERROR, "assertion_failure"),

    # TRANSIENT patterns — network/blip/I/O
    # Word boundary on \bretry\b to avoid matching 'retry_handler', 'should_retry'
    (r"timed?.?out|timeout|connection refused|ECONNREFUSED", FailureMode.TRANSIENT, "network_timeout"),
    (r"connection reset|ECONNRESET|ETIMEDOUT|EPIPE", FailureMode.TRANSIENT, "connection_reset"),
    (r"503.*service unavailable|502.*bad gateway|504.*gateway timeout", FailureMode.TRANSIENT, "http_5xx"),
    (r"\btemporary failure|\btry again\b|\bretry\b", FailureMode.TRANSIENT, "transient_signal"),
]


# Maximum log size we'll classify. Larger logs get truncated to avoid
# pathological performance on multi-MB error logs.
MAX_LOG_SIZE = 16_384  # 16 KB


@dataclass
class RetryPolicy:
    """How to retry a failed task based on its FailureMode."""
    max_attempts: int
    backoff_seconds: float
    escalate_to: str  # Linear label to apply when retries exhausted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Mode → Policy mapping (the heart of smart retry)
# NOTE: TRANSIENT and RATE_LIMIT escalate to OUTPUT_REQUIRES_ATTENTION on
# exhaustion, NOT back to dispatch:ready. Per PR #35 review, re-adding
# dispatch:ready creates an unbounded retry loop because the factory would
# immediately re-pick the task.
POLICIES: dict[FailureMode, RetryPolicy] = {
    FailureMode.TRANSIENT:       RetryPolicy(max_attempts=3, backoff_seconds=5.0,  escalate_to=OUTPUT_REQUIRES_ATTENTION),
    FailureMode.RATE_LIMIT:      RetryPolicy(max_attempts=5, backoff_seconds=60.0, escalate_to=OUTPUT_REQUIRES_ATTENTION),
    FailureMode.SHAPE_VIOLATION: RetryPolicy(max_attempts=0, backoff_seconds=0.0,  escalate_to=TASK_SHAPE_VIOLATION),
    FailureMode.LOGIC_ERROR:     RetryPolicy(max_attempts=1, backoff_seconds=30.0, escalate_to=AGENT_FRED_LABEL),
    FailureMode.IMPOSSIBLE:      RetryPolicy(max_attempts=0, backoff_seconds=0.0,  escalate_to=OUTPUT_REQUIRES_ATTENTION),
}


@dataclass
class ClassificationResult:
    """Result of classifying a failure."""
    mode: FailureMode
    matched_pattern: str
    matched_label: str
    policy: RetryPolicy
    attempt: int
    should_retry: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "matched_pattern": self.matched_pattern,
            "matched_label": self.matched_label,
            "policy": self.policy.to_dict(),
            "attempt": self.attempt,
            "should_retry": self.should_retry,
            "reason": self.reason,
        }


def classify_failure(error_log: "str | None", attempt_count: int = 0) -> ClassificationResult:
    """Inspect an error log + current attempt count and return a ClassificationResult.

    Args:
        error_log: The agent's error log / stderr / transcript tail. None is tolerated.
        attempt_count: How many times this task has been attempted (0 = first try)

    Returns:
        ClassificationResult with mode, policy, and retry decision

    Notes:
        - error_log is truncated to MAX_LOG_SIZE (16 KB) for performance
        - None is treated as empty log (caller bug, but we don't crash)
    """
    # Type guard: None or non-string → empty log
    if error_log is None or not isinstance(error_log, str):
        policy = POLICIES[FailureMode.TRANSIENT]
        return ClassificationResult(
            mode=FailureMode.TRANSIENT,
            matched_pattern="",
            matched_label="invalid_log_type",
            policy=policy,
            attempt=attempt_count,
            should_retry=(attempt_count < policy.max_attempts),
            reason=f"Invalid log type ({type(error_log).__name__}) — treating as transient",
        )

    # Truncate huge logs (performance)
    if len(error_log) > MAX_LOG_SIZE:
        error_log = error_log[-MAX_LOG_SIZE:]  # Keep the most recent (where errors usually are)

    if not error_log:
        # Empty log = unknown failure mode — treat as transient (cheap to retry)
        policy = POLICIES[FailureMode.TRANSIENT]
        return ClassificationResult(
            mode=FailureMode.TRANSIENT,
            matched_pattern="",
            matched_label="empty_log",
            policy=policy,
            attempt=attempt_count,
            should_retry=(attempt_count < policy.max_attempts),
            reason="Empty error log — treating as transient",
        )

    # Try to match against known patterns (first match wins)
    for pattern, mode, label in FAILURE_PATTERNS:
        if re.search(pattern, error_log, re.IGNORECASE | re.MULTILINE):
            policy = POLICIES[mode]
            should_retry = attempt_count < policy.max_attempts
            return ClassificationResult(
                mode=mode,
                matched_pattern=pattern,
                matched_label=label,
                policy=policy,
                attempt=attempt_count,
                should_retry=should_retry,
                reason=f"Matched pattern '{label}' → {mode.value}",
            )

    # No pattern matched → default to TRANSIENT (safe retry)
    policy = POLICIES[FailureMode.TRANSIENT]
    return ClassificationResult(
        mode=FailureMode.TRANSIENT,
        matched_pattern="",
        matched_label="no_pattern_match",
        policy=policy,
        attempt=attempt_count,
        should_retry=(attempt_count < policy.max_attempts),
        reason="No failure pattern matched — defaulting to transient",
    )


def classify_with_policy(
    error_log: str,
    attempt_count: int = 0,
    fail_open: bool = True,
) -> ClassificationResult:
    """Classify and decide retry policy with safe defaults.

    Args:
        error_log: The agent's error log
        attempt_count: Current retry count
        fail_open: If True, unknown patterns default to TRANSIENT (safe to retry).
                   If False, unknown patterns default to LOGIC_ERROR (escalate).

    Returns:
        ClassificationResult
    """
    result = classify_failure(error_log, attempt_count)
    if result.matched_label == "no_pattern_match" and not fail_open:
        policy = POLICIES[FailureMode.LOGIC_ERROR]
        return ClassificationResult(
            mode=FailureMode.LOGIC_ERROR,
            matched_pattern="",
            matched_label="no_pattern_match_fail_closed",
            policy=policy,
            attempt=attempt_count,
            should_retry=(attempt_count < policy.max_attempts),
            reason="No pattern matched — fail-closed escalation",
        )
    return result


# ─────────────────────────────────────────────────────────────────────
# Failure counter — persistent retry tracking
# ─────────────────────────────────────────────────────────────────────

COUNTER_PATH = "/tmp/failure_counter.json"


def _load_counter() -> dict[str, int]:
    """Load failure counter from disk with file locking to prevent races."""
    if not os.path.exists(COUNTER_PATH):
        return {}
    try:
        with open(COUNTER_PATH) as f:
            # Try shared lock (non-blocking on Linux)
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                # No fcntl (Windows) or lock unavailable — best-effort read
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_counter(counter: dict[str, int]) -> None:
    """Save failure counter to disk with exclusive lock."""
    Path(COUNTER_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(COUNTER_PATH, "w") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(counter, f, indent=2)
                f.flush()
                return
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            # No fcntl — best-effort write
            json.dump(counter, f, indent=2)


def increment_failure(issue_id: str) -> int:
    """Increment failure count for a task. Returns new count."""
    counter = _load_counter()
    counter[issue_id] = counter.get(issue_id, 0) + 1
    _save_counter(counter)
    return counter[issue_id]


def reset_failure(issue_id: str) -> None:
    """Reset failure count for a task (called on success)."""
    counter = _load_counter()
    if issue_id in counter:
        del counter[issue_id]
        _save_counter(counter)


def get_failure_count(issue_id: str) -> int:
    """Get current failure count for a task."""
    return _load_counter().get(issue_id, 0)


# ─────────────────────────────────────────────────────────────────────
# Linear integration — apply classification to a Linear issue
# ─────────────────────────────────────────────────────────────────────


def apply_failure_classification(
    issue_id: str,
    error_log: str,
    linear_api_fn: Any | None = None,
) -> ClassificationResult:
    """Classify a failure and apply the result to a Linear issue.

    Args:
        issue_id: Linear issue ID or identifier (e.g. "GRO-123")
        error_log: The agent's error log
        linear_api_fn: Optional callable to post Linear comments. If None, only classify.

    Returns:
        ClassificationResult
    """
    attempt = get_failure_count(issue_id)
    result = classify_with_policy(error_log, attempt)

    # Increment counter on every failure
    increment_failure(issue_id)

    # If no more retries, apply escalation label
    if not result.should_retry:
        if linear_api_fn is not None:
            try:
                linear_api_fn(
                    issue_id=issue_id,
                    action="add_label",
                    label=result.policy.escalate_to,
                    comment=f"🚨 Failure classification: {result.mode.value}\n"
                            f"Reason: {result.reason}\n"
                            f"Attempts exhausted ({attempt}). Escalating to `{result.policy.escalate_to}`.",
                )
            except Exception:
                pass  # Linear errors should not block classification

    return result


def reset_after_success(issue_id: str, linear_api_fn: Any | None = None) -> None:
    """Reset failure counter after a task succeeds.

    NOTE: Linear label cleanup is the caller's responsibility — this function
    does NOT remove any escalation labels that were applied during retries.
    To clean up labels, pass a linear_api_fn that handles label removal.
    """
    reset_failure(issue_id)
    # Note: Linear label cleanup is the caller's responsibility


# ─────────────────────────────────────────────────────────────────────
# Convenience: get retry decision without Linear side effects
# ─────────────────────────────────────────────────────────────────────


def should_retry(error_log: str, attempt_count: int = 0) -> tuple[bool, FailureMode, float]:
    """Decide whether to retry, what mode, and how long to wait.

    Returns:
        (should_retry, mode, backoff_seconds)
    """
    result = classify_failure(error_log, attempt_count)
    return result.should_retry, result.mode, result.policy.backoff_seconds


def wait_for_retry(error_log: str, attempt_count: int = 0, max_sleep: float = 30.0) -> bool:
    """Sleep for the appropriate backoff, return True if should retry.

    Args:
        error_log: The agent's error log
        attempt_count: Current retry count
        max_sleep: Cap on sleep time (default 30s). Prevents blocking on
                   rate-limit backoffs that exceed this threshold.

    Usage:
        if wait_for_retry(error_log, attempt):
            # re-dispatch
        else:
            # escalate
    """
    should, mode, backoff = should_retry(error_log, attempt_count)
    if should and backoff > 0:
        time.sleep(min(backoff, max_sleep))
    return should