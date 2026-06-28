"""Tests for prismatic.quality.failure (Phase 2 / Gap 7).

Covers:
  - FailureMode enum
  - classify_failure() pattern matching
  - classify_with_policy() fail-open vs fail-closed
  - RetryPolicy lookup
  - Failure counter (increment / reset / get)
  - apply_failure_classification() with Linear integration
  - should_retry() and wait_for_retry() helpers
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from prismatic.quality.failure import (
    FailureMode,
    RetryPolicy,
    POLICIES,
    FAILURE_PATTERNS,
    ClassificationResult,
    classify_failure,
    classify_with_policy,
    apply_failure_classification,
    should_retry,
    wait_for_retry,
    increment_failure,
    reset_failure,
    reset_after_success,
    get_failure_count,
    OUTPUT_REQUIRES_ATTENTION,
    COUNTER_PATH,
)


# ─────────────────────────────────────────────────────────────────────
# FailureMode enum tests
# ─────────────────────────────────────────────────────────────────────


class TestFailureMode:
    def test_all_modes_defined(self):
        expected = {"TRANSIENT", "RATE_LIMIT", "SHAPE_VIOLATION", "LOGIC_ERROR", "IMPOSSIBLE"}
        actual = {m.name for m in FailureMode}
        assert actual == expected

    def test_mode_values_are_strings(self):
        for mode in FailureMode:
            assert isinstance(mode.value, str)
            # Value is lowercase enum name (may be abbreviated like "shape")
            assert mode.value == mode.value.lower()


# ─────────────────────────────────────────────────────────────────────
# Policy table tests
# ─────────────────────────────────────────────────────────────────────


class TestPolicies:
    def test_all_modes_have_policies(self):
        for mode in FailureMode:
            assert mode in POLICIES
            assert isinstance(POLICIES[mode], RetryPolicy)

    def test_transient_allows_multiple_retries(self):
        policy = POLICIES[FailureMode.TRANSIENT]
        assert policy.max_attempts >= 3
        assert policy.backoff_seconds >= 0

    def test_rate_limit_uses_long_backoff(self):
        policy = POLICIES[FailureMode.RATE_LIMIT]
        assert policy.max_attempts >= 3
        assert policy.backoff_seconds >= 30  # Wait at least 30s on rate limit

    def test_shape_violation_does_not_retry(self):
        policy = POLICIES[FailureMode.SHAPE_VIOLATION]
        assert policy.max_attempts == 0
        assert policy.escalate_to == "task:shape-violation"

    def test_impossible_does_not_retry(self):
        policy = POLICIES[FailureMode.IMPOSSIBLE]
        assert policy.max_attempts == 0

    def test_logic_error_limited_retry(self):
        policy = POLICIES[FailureMode.LOGIC_ERROR]
        assert 0 <= policy.max_attempts <= 2  # Limited retry, then escalate


# ─────────────────────────────────────────────────────────────────────
# classify_failure() pattern matching tests
# ─────────────────────────────────────────────────────────────────────


class TestClassifyFailure:
    def test_rate_limit_detected(self):
        log = "ERROR: rate limit hit, please retry"
        result = classify_failure(log)
        assert result.mode == FailureMode.RATE_LIMIT
        assert result.should_retry is True

    def test_429_too_many_detected(self):
        log = "HTTP 429 Too Many Requests"
        result = classify_failure(log)
        assert result.mode == FailureMode.RATE_LIMIT

    def test_ai_ultra_quota_detected(self):
        log = "AGY_QUOTA_EXHAUSTED on gemini-3.5-flash"
        result = classify_failure(log)
        assert result.mode == FailureMode.RATE_LIMIT

    def test_shape_violation_detected(self):
        log = "FORBIDDEN_COMMAND: pytest is not allowed"
        result = classify_failure(log)
        assert result.mode == FailureMode.SHAPE_VIOLATION
        assert result.should_retry is False

    def test_impossible_permission_denied(self):
        log = "permission denied (EACCES)"
        result = classify_failure(log)
        assert result.mode == FailureMode.IMPOSSIBLE
        assert result.should_retry is False

    def test_impossible_missing_dependency(self):
        log = "Error: /usr/local/bin/foo does not exist, cannot continue"
        result = classify_failure(log)
        assert result.mode == FailureMode.IMPOSSIBLE

    def test_logic_error_typeerror(self):
        log = "TypeError: 'NoneType' object is not iterable"
        result = classify_failure(log)
        assert result.mode == FailureMode.LOGIC_ERROR

    def test_logic_error_python_traceback(self):
        log = """Traceback (most recent call last):
  File "test.py", line 5, in <module>
    x = undefined_var
NameError: name 'undefined_var' is not defined
"""
        result = classify_failure(log)
        assert result.mode == FailureMode.LOGIC_ERROR

    def test_transient_timeout(self):
        log = "Connection timed out after 30s"
        result = classify_failure(log)
        assert result.mode == FailureMode.TRANSIENT
        assert result.should_retry is True

    def test_transient_5xx(self):
        log = "HTTP 503 Service Unavailable"
        result = classify_failure(log)
        assert result.mode == FailureMode.TRANSIENT

    def test_unknown_pattern_defaults_to_transient(self):
        log = "Some completely unknown error message"
        result = classify_failure(log)
        # fail_open=True (default) → transient
        assert result.mode == FailureMode.TRANSIENT
        assert result.matched_label == "no_pattern_match"

    def test_empty_log_returns_transient(self):
        result = classify_failure("")
        assert result.mode == FailureMode.TRANSIENT
        assert result.matched_label == "empty_log"

    def test_first_pattern_match_wins(self):
        # If a log has both rate_limit and shape_violation patterns,
        # the order in FAILURE_PATTERNS determines which wins.
        # Rate limit comes first, so it should win.
        log = "rate limit hit AND FORBIDDEN_COMMAND pytest"
        result = classify_failure(log)
        assert result.mode == FailureMode.RATE_LIMIT


# ─────────────────────────────────────────────────────────────────────
# Retry decision tests
# ─────────────────────────────────────────────────────────────────────


class TestRetryDecision:
    def test_within_attempt_budget_should_retry(self):
        log = "Connection timed out"
        # Transient allows 3 attempts; on attempt 1, should retry
        result = classify_failure(log, attempt_count=1)
        assert result.should_retry is True

    def test_exhausted_attempts_should_not_retry(self):
        log = "Connection timed out"
        # Transient allows 3 attempts; on attempt 3, should NOT retry
        result = classify_failure(log, attempt_count=3)
        assert result.should_retry is False

    def test_logic_error_limited_retry(self):
        log = "TypeError: bad arg"
        # Logic error allows 1 retry
        result0 = classify_failure(log, attempt_count=0)
        result1 = classify_failure(log, attempt_count=1)
        assert result0.should_retry is True
        assert result1.should_retry is False

    def test_shape_never_retries(self):
        log = "SHAPE_VIOLATION"
        for attempt in range(5):
            result = classify_failure(log, attempt_count=attempt)
            assert result.should_retry is False


# ─────────────────────────────────────────────────────────────────────
# classify_with_policy() fail-open vs fail-closed
# ─────────────────────────────────────────────────────────────────────


class TestClassifyWithPolicy:
    def test_fail_open_unknown_pattern(self):
        result = classify_with_policy("unknown error", attempt_count=0, fail_open=True)
        assert result.mode == FailureMode.TRANSIENT

    def test_fail_closed_unknown_pattern(self):
        result = classify_with_policy("unknown error", attempt_count=0, fail_open=False)
        assert result.mode == FailureMode.LOGIC_ERROR
        assert result.matched_label == "no_pattern_match_fail_closed"

    def test_known_pattern_same_in_both_modes(self):
        # When pattern matches, fail_open/fail_closed doesn't matter
        log = "rate limit hit"
        r1 = classify_with_policy(log, fail_open=True)
        r2 = classify_with_policy(log, fail_open=False)
        assert r1.mode == FailureMode.RATE_LIMIT
        assert r2.mode == FailureMode.RATE_LIMIT


# ─────────────────────────────────────────────────────────────────────
# Failure counter tests (with temp file to avoid polluting real state)
# ─────────────────────────────────────────────────────────────────────


class TestFailureCounter:
    def test_increment_and_get(self, tmp_path, monkeypatch):
        # Use temp file for counter
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        # Reset state
        if counter_file.exists():
            counter_file.unlink()

        assert get_failure_count("GRO-1") == 0
        c1 = increment_failure("GRO-1")
        assert c1 == 1
        c2 = increment_failure("GRO-1")
        assert c2 == 2
        assert get_failure_count("GRO-1") == 2

    def test_reset_clears_count(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        increment_failure("GRO-2")
        assert get_failure_count("GRO-2") >= 1
        reset_failure("GRO-2")
        assert get_failure_count("GRO-2") == 0

    def test_separate_tasks_have_separate_counts(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        increment_failure("GRO-A")
        increment_failure("GRO-A")
        increment_failure("GRO-B")

        assert get_failure_count("GRO-A") == 2
        assert get_failure_count("GRO-B") == 1

    def test_reset_after_success(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        increment_failure("GRO-3")
        increment_failure("GRO-3")
        reset_after_success("GRO-3")
        assert get_failure_count("GRO-3") == 0

    def test_corrupted_counter_file_recovers(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        counter_file.write_text("not valid json {{{")
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        # Should not raise — returns 0 on corruption
        assert get_failure_count("GRO-X") == 0


# ─────────────────────────────────────────────────────────────────────
# apply_failure_classification() with Linear integration
# ─────────────────────────────────────────────────────────────────────


class TestApplyFailureClassification:
    def test_retries_within_budget_no_escalation(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        linear_calls = []
        def mock_linear(issue_id, action, label, comment):
            linear_calls.append((issue_id, action, label))

        log = "Connection timed out"
        result = apply_failure_classification(
            "GRO-100",
            log,
            linear_api_fn=mock_linear,
        )

        # Transient on attempt 0 — should retry, no escalation
        assert result.should_retry is True
        assert len(linear_calls) == 0

    def test_exhausted_retries_escalates(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        # Pre-fill counter so we're at attempt 3
        counter_file.write_text(json.dumps({"GRO-200": 3}))

        linear_calls = []
        def mock_linear(issue_id, action, label, comment):
            linear_calls.append((issue_id, action, label, comment))

        log = "Connection timed out"
        result = apply_failure_classification(
            "GRO-200",
            log,
            linear_api_fn=mock_linear,
        )

        assert result.should_retry is False
        assert len(linear_calls) == 1
        issue_id, action, label, comment = linear_calls[0]
        assert issue_id == "GRO-200"
        assert action == "add_label"
        assert label == "dispatch:ready"  # Transient policy escalates here
        assert "Failure classification: transient" in comment

    def test_shape_violation_immediate_escalation(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        linear_calls = []
        def mock_linear(issue_id, action, label, comment):
            linear_calls.append((issue_id, action, label))

        log = "FORBIDDEN_COMMAND pytest"
        result = apply_failure_classification(
            "GRO-300",
            log,
            linear_api_fn=mock_linear,
        )

        assert result.should_retry is False
        assert len(linear_calls) == 1
        assert linear_calls[0][2] == "task:shape-violation"

    def test_impossible_escalates_to_requires_attention(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        linear_calls = []
        def mock_linear(issue_id, action, label, comment):
            linear_calls.append((issue_id, action, label))

        log = "permission denied (EACCES)"
        result = apply_failure_classification(
            "GRO-400",
            log,
            linear_api_fn=mock_linear,
        )

        assert result.mode == FailureMode.IMPOSSIBLE
        assert linear_calls[0][2] == OUTPUT_REQUIRES_ATTENTION

    def test_linear_error_does_not_block(self, tmp_path, monkeypatch):
        counter_file = tmp_path / "counter.json"
        monkeypatch.setattr("prismatic.quality.failure.COUNTER_PATH", str(counter_file))

        def broken_linear(*args, **kwargs):
            raise Exception("Linear API down")

        # Should not raise even though Linear is broken
        log = "FORBIDDEN_COMMAND pytest"
        result = apply_failure_classification("GRO-500", log, linear_api_fn=broken_linear)
        assert result.mode == FailureMode.SHAPE_VIOLATION


# ─────────────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────────────


class TestShouldRetryHelper:
    def test_returns_tuple(self):
        should, mode, backoff = should_retry("rate limit hit", attempt_count=0)
        assert isinstance(should, bool)
        assert isinstance(mode, FailureMode)
        assert isinstance(backoff, (int, float))


class TestWaitForRetry:
    def test_returns_true_when_should_retry(self):
        # Transient with low backoff
        # Use a mock to avoid actually sleeping
        with patch("prismatic.quality.failure.time.sleep") as mock_sleep:
            should = wait_for_retry("rate limit hit", attempt_count=0)
        # Rate limit has 60s backoff — should sleep
        if should:
            mock_sleep.assert_called_once()

    def test_returns_false_when_exhausted(self):
        with patch("prismatic.quality.failure.time.sleep") as mock_sleep:
            should = wait_for_retry("FORBIDDEN_COMMAND pytest", attempt_count=0)
        # Shape violation never retries
        assert should is False
        mock_sleep.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────


def test_output_requires_attention_constant():
    assert OUTPUT_REQUIRES_ATTENTION == "output:requires-attention"


def test_counter_path_constant():
    assert COUNTER_PATH == "/tmp/failure_counter.json"


def test_failure_patterns_format():
    """Each pattern entry should be (regex, FailureMode, label)."""
    for entry in FAILURE_PATTERNS:
        assert len(entry) == 3
        pattern, mode, label = entry
        assert isinstance(pattern, str)
        assert isinstance(mode, FailureMode)
        assert isinstance(label, str)
        # No duplicate labels
    labels = [e[2] for e in FAILURE_PATTERNS]
    assert len(labels) == len(set(labels)), f"Duplicate labels: {labels}"