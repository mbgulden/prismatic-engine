"""Tests for the peer-review pipeline orchestrator (Phase 2 / Gap 8)."""

from __future__ import annotations

from prismatic.review.pipeline import (
    ACTION_ADVANCE,
    ACTION_GIVE_UP,
    ACTION_HOLD,
    ACTION_REWORK,
    DEFAULT_MAX_REWORK_ATTEMPTS,
    IMPACT_BLOCKER,
    IMPACT_MAJOR,
    IMPACT_MINOR,
    IMPACT_TRIVIAL,
    IMPACT_RANK,
    PipelineOrchestrator,
    build_rework_payload,
    classify_impact,
    decide_next_action,
)
from prismatic.review.pr_reviewer import (
    APPROVE,
    NEEDS_DISCUSSION,
    PRReviewResult,
    REQUEST_CHANGES,
)


def _make_result(
    verdict: str = APPROVE,
    *,
    critical: int = 0,
    high: int = 0,
    warning: int = 0,
    inline: list | None = None,
) -> PRReviewResult:
    """Build a PRReviewResult with the given severity counts."""
    return PRReviewResult(
        verdict=verdict,
        summary=f"Test verdict={verdict}",
        inline_comments=inline or [],
        metadata={
            "critical_count": critical,
            "high_count": high,
            "warning_count": warning,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# classify_impact
# ─────────────────────────────────────────────────────────────────────


class TestClassifyImpact:
    def test_approve_is_trivial(self):
        assert classify_impact(_make_result(APPROVE)) == IMPACT_TRIVIAL

    def test_request_changes_with_high_is_major(self):
        assert classify_impact(_make_result(REQUEST_CHANGES, high=1)) == IMPACT_MAJOR

    def test_request_changes_with_critical_is_blocker(self):
        assert (
            classify_impact(_make_result(REQUEST_CHANGES, critical=1)) == IMPACT_BLOCKER
        )

    def test_request_changes_with_only_warning_is_major(self):
        # Reviewer shouldn't return REQUEST_CHANGES for only warnings,
        # but if it does, treat as major (still requires rework).
        assert classify_impact(_make_result(REQUEST_CHANGES, warning=3)) == IMPACT_MAJOR

    def test_needs_discussion_with_warning_is_minor(self):
        assert (
            classify_impact(_make_result(NEEDS_DISCUSSION, warning=2)) == IMPACT_MINOR
        )

    def test_needs_discussion_with_high_is_major(self):
        assert classify_impact(_make_result(NEEDS_DISCUSSION, high=1)) == IMPACT_MAJOR

    def test_needs_discussion_empty_is_minor(self):
        # Diff-fetch-failed case: NEEDS_DISCUSSION with no findings.
        assert classify_impact(_make_result(NEEDS_DISCUSSION)) == IMPACT_MINOR

    def test_critical_in_metadata_overrides_verdict(self):
        # Even if verdict says NEEDS_DISCUSSION, critical findings escalate.
        assert (
            classify_impact(_make_result(NEEDS_DISCUSSION, critical=1))
            == IMPACT_BLOCKER
        )

    def test_impact_levels_are_orderable(self):
        # Useful for max() comparisons in callers.
        assert IMPACT_RANK[IMPACT_TRIVIAL] < IMPACT_RANK[IMPACT_MINOR]
        assert IMPACT_RANK[IMPACT_MINOR] < IMPACT_RANK[IMPACT_MAJOR]
        assert IMPACT_RANK[IMPACT_MAJOR] < IMPACT_RANK[IMPACT_BLOCKER]


# ─────────────────────────────────────────────────────────────────────
# decide_next_action
# ─────────────────────────────────────────────────────────────────────


class TestDecideNextAction:
    def test_approve_returns_advance(self):
        result = _make_result(APPROVE)
        assert decide_next_action(result) == ACTION_ADVANCE
        assert decide_next_action(result, rework_attempts=5) == ACTION_ADVANCE

    def test_request_changes_first_attempt_returns_rework(self):
        result = _make_result(REQUEST_CHANGES, high=1)
        assert decide_next_action(result, rework_attempts=0) == ACTION_REWORK

    def test_request_changes_at_budget_returns_give_up(self):
        result = _make_result(REQUEST_CHANGES, high=1)
        assert (
            decide_next_action(result, rework_attempts=DEFAULT_MAX_REWORK_ATTEMPTS)
            == ACTION_GIVE_UP
        )
        assert decide_next_action(result, rework_attempts=10) == ACTION_GIVE_UP

    def test_request_changes_one_below_budget_returns_rework(self):
        result = _make_result(REQUEST_CHANGES, high=1)
        assert (
            decide_next_action(result, rework_attempts=DEFAULT_MAX_REWORK_ATTEMPTS - 1)
            == ACTION_REWORK
        )

    def test_needs_discussion_returns_hold(self):
        result = _make_result(NEEDS_DISCUSSION, warning=1)
        assert decide_next_action(result) == ACTION_HOLD
        assert decide_next_action(result, rework_attempts=99) == ACTION_HOLD


# ─────────────────────────────────────────────────────────────────────
# build_rework_payload
# ─────────────────────────────────────────────────────────────────────


class TestBuildReworkPayload:
    def test_basic_payload_fields(self):
        result = _make_result(REQUEST_CHANGES, high=1)
        payload = build_rework_payload(
            "GRO-1234", "https://github.com/o/r/pull/1", result
        )
        assert payload.issue_identifier == "GRO-1234"
        assert payload.pr_url == "https://github.com/o/r/pull/1"
        assert payload.verdict == REQUEST_CHANGES
        assert payload.rework_attempt == 1
        assert payload.rework_label == "agent:rework"

    def test_payload_serializes_inline_comments(self):
        from prismatic.review.pr_reviewer import InlineComment

        comments = [
            InlineComment(path="foo.py", line=10, body="foo too long"),
            InlineComment(path="bar.py", line=20, body="bar is wrong"),
        ]
        result = _make_result(REQUEST_CHANGES, high=2, inline=comments)
        payload = build_rework_payload("GRO-1234", "url", result)
        assert len(payload.findings) == 2
        assert payload.findings[0] == {
            "path": "foo.py",
            "line": 10,
            "body": "foo too long",
        }
        assert payload.findings[1]["path"] == "bar.py"

    def test_payload_rework_attempt_counter(self):
        result = _make_result(REQUEST_CHANGES, high=1)
        p1 = build_rework_payload("GRO-1", "url", result, rework_attempt=1)
        p2 = build_rework_payload("GRO-1", "url", result, rework_attempt=2)
        assert p1.rework_attempt == 1
        assert p2.rework_attempt == 2


# ─────────────────────────────────────────────────────────────────────
# PipelineOrchestrator
# ─────────────────────────────────────────────────────────────────────


class TestPipelineOrchestrator:
    def test_initial_state_zero_attempts(self):
        orch = PipelineOrchestrator()
        assert orch.attempts_for("GRO-1") == 0

    def test_approve_advances_and_resets(self):
        orch = PipelineOrchestrator()
        # First, do some rework to bump the counter.
        orch.record_rework("GRO-1")
        orch.record_rework("GRO-1")
        assert orch.attempts_for("GRO-1") == 2

        # Then approve — counter should reset.
        decision = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(APPROVE),
        )
        assert decision.action == ACTION_ADVANCE
        assert orch.attempts_for("GRO-1") == 0

    def test_request_changes_dispatches_rework_with_incrementing_counter(self):
        orch = PipelineOrchestrator()
        d1 = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d1.action == ACTION_REWORK
        assert d1.rework_payload is not None
        assert d1.rework_payload.rework_attempt == 1

        d2 = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d2.action == ACTION_REWORK
        assert d2.rework_payload.rework_attempt == 2

    def test_rework_budget_exhausted_gives_up(self):
        orch = PipelineOrchestrator(max_rework_attempts=2)
        d1 = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d1.action == ACTION_REWORK  # attempt 1
        d2 = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d2.action == ACTION_REWORK  # attempt 2
        d3 = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d3.action == ACTION_GIVE_UP
        assert d3.rework_payload is None

    def test_needs_discussion_holds_even_after_rework(self):
        orch = PipelineOrchestrator()
        # NEEDS_DISCUSSION should never escalate to rework, no matter
        # how many times the same verdict is returned.
        for i in range(5):
            d = orch.process(
                identifier="GRO-1",
                pr_url="url",
                result=_make_result(NEEDS_DISCUSSION, warning=1),
            )
            assert d.action == ACTION_HOLD
            assert d.rework_payload is None

    def test_decision_includes_impact_and_rationale(self):
        orch = PipelineOrchestrator()
        d = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d.verdict == REQUEST_CHANGES
        assert d.impact == IMPACT_MAJOR
        assert d.action == ACTION_REWORK
        assert "verdict=REQUEST_CHANGES" in d.rationale
        assert "impact=major" in d.rationale
        assert "attempts=0/2" in d.rationale
        assert d.metadata["high_count"] == 1

    def test_decision_metadata_carries_severity_counts(self):
        orch = PipelineOrchestrator()
        d = orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, critical=1, high=2, warning=3),
        )
        assert d.metadata["critical_count"] == 1
        assert d.metadata["high_count"] == 2
        assert d.metadata["warning_count"] == 3

    def test_rework_payload_serializes_for_queue(self):
        """ReworkPayload must be JSON-friendly so the factory queue can ingest it."""
        import json
        from prismatic.review.pr_reviewer import InlineComment

        orch = PipelineOrchestrator()
        result = _make_result(
            REQUEST_CHANGES,
            high=1,
            inline=[InlineComment(path="x.py", line=5, body="too long")],
        )
        d = orch.process(
            identifier="GRO-1", pr_url="https://github.com/o/r/pull/9", result=result
        )
        # Serialize via dataclasses.asdict
        from dataclasses import asdict

        payload_dict = asdict(d.rework_payload)
        # Must round-trip through json.dumps without TypeError.
        serialized = json.dumps(payload_dict)
        assert "GRO-1" in serialized
        assert "https://github.com/o/r/pull/9" in serialized

    def test_separate_issues_have_separate_counters(self):
        orch = PipelineOrchestrator()
        orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        orch.process(
            identifier="GRO-1",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        # GRO-2 is fresh — should be at attempt 0.
        assert orch.attempts_for("GRO-1") == 2
        assert orch.attempts_for("GRO-2") == 0

    def test_reset_clears_counter(self):
        orch = PipelineOrchestrator()
        orch.record_rework("GRO-1")
        orch.record_rework("GRO-1")
        assert orch.attempts_for("GRO-1") == 2
        orch.reset("GRO-1")
        assert orch.attempts_for("GRO-1") == 0
        # Resetting a non-existent key is a no-op.
        orch.reset("GRO-NONEXISTENT")


# ─────────────────────────────────────────────────────────────────────
# Integration: orchestrator + reviewer (mocked)
# ─────────────────────────────────────────────────────────────────────


class TestOrchestratorWithReviewer:
    def test_full_cycle_request_changes_then_approve(self):
        """End-to-end: REQUEST_CHANGES dispatches rework twice, then APPROVE resets."""
        orch = PipelineOrchestrator(max_rework_attempts=2)
        identifier = "GRO-CYCLE-1"
        pr_url = "https://github.com/o/r/pull/42"

        # First review finds a high-severity issue → dispatch rework.
        d1 = orch.process(
            identifier=identifier,
            pr_url=pr_url,
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d1.action == ACTION_REWORK
        assert d1.rework_payload.rework_attempt == 1

        # Second review: still failing.
        d2 = orch.process(
            identifier=identifier,
            pr_url=pr_url,
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d2.action == ACTION_REWORK
        assert d2.rework_payload.rework_attempt == 2

        # Third review: fix landed, APPROVE.
        d3 = orch.process(
            identifier=identifier,
            pr_url=pr_url,
            result=_make_result(APPROVE),
        )
        assert d3.action == ACTION_ADVANCE
        assert orch.attempts_for(identifier) == 0  # counter reset

    def test_full_cycle_exhausted_budget(self):
        orch = PipelineOrchestrator(max_rework_attempts=1)
        d1 = orch.process(
            identifier="GRO-X",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d1.action == ACTION_REWORK
        # Same issue, still REQUEST_CHANGES, but budget exhausted.
        d2 = orch.process(
            identifier="GRO-X",
            pr_url="url",
            result=_make_result(REQUEST_CHANGES, high=1),
        )
        assert d2.action == ACTION_GIVE_UP
        assert d2.rework_payload is None
