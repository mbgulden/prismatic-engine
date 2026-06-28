"""Tests for the ``agent:ned-review`` trigger (Phase 2 / Gap 4 / Task #6).

Covers:
  - ``pr_reviewer`` public contract: PRReviewResult, InlineComment,
    StubPRReviewer, Protocol conformance
  - ``has_ned_review_label`` — string list, dict list, case-insensitive,
    empty/None input
  - ``trigger_ned_review`` — label missing, label present without PR,
    APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION paths, callback
    invocation, callback-error swallowing
"""

from __future__ import annotations

import pytest

from prismatic.review.pr_reviewer import (
    APPROVE,
    NEEDS_DISCUSSION,
    NED_REVIEW_LABEL,
    PRReviewResult,
    REQUEST_CHANGES,
    InlineComment,
    StubPRReviewer,
)

from prismatic.quality.gates import (
    NED_REVIEW_TARGET_STATE,
    NedReviewDecision,
    _format_linear_comment,
    has_ned_review_label,
    trigger_ned_review,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class FakeReviewer:
    """Deterministic reviewer for trigger tests.

    Returns the verdict passed at construction. Records every PR URL it
    is called with so tests can assert on call shape.
    """

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def review_pr(self, pr_url: str) -> PRReviewResult:
        self.calls.append(pr_url)
        return PRReviewResult(
            verdict=self.verdict,
            summary=f"fake reviewer verdict: {self.verdict}",
            metadata={"reviewer": "fake", "pr_url": pr_url},
        )


# ─────────────────────────────────────────────────────────────────────
# PRReviewResult & InlineComment
# ─────────────────────────────────────────────────────────────────────


class TestPRReviewResult:
    """Contract for the verdict dataclass."""

    def test_approve_verdict_accepted(self):
        r = PRReviewResult(verdict=APPROVE, summary="looks good")
        assert r.verdict == APPROVE
        assert r.passed is True

    def test_request_changes_verdict_accepted(self):
        r = PRReviewResult(verdict=REQUEST_CHANGES, summary="fix x")
        assert r.passed is False

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValueError, match="Invalid verdict"):
            PRReviewResult(verdict="MAYBE", summary="?")

    def test_to_dict_round_trip(self):
        r = PRReviewResult(
            verdict=REQUEST_CHANGES,
            summary="needs work",
            inline_comments=[InlineComment(path="x.py", line=10, body="bug")],
            metadata={"k": 1},
        )
        d = r.to_dict()
        assert d["verdict"] == REQUEST_CHANGES
        assert d["inline_comments"] == [{"path": "x.py", "line": 10, "body": "bug"}]
        assert d["metadata"] == {"k": 1}


class TestInlineComment:
    def test_construction(self):
        c = InlineComment(path="a/b.py", line=42, body="looks suspicious")
        assert c.path == "a/b.py"
        assert c.line == 42
        assert c.body == "looks suspicious"


# ─────────────────────────────────────────────────────────────────────
# StubPRReviewer
# ─────────────────────────────────────────────────────────────────────


class TestStubPRReviewer:
    def test_default_returns_approve(self):
        r = StubPRReviewer().review_pr("https://example/pr/1")
        assert r.verdict == APPROVE
        assert "stub" in r.summary.lower()
        assert r.inline_comments == []
        assert r.metadata["reviewer"] == "stub"

    def test_explicit_default_verdict(self):
        r = StubPRReviewer(default_verdict=REQUEST_CHANGES).review_pr("x")
        assert r.verdict == REQUEST_CHANGES

    def test_invalid_default_verdict_rejected(self):
        with pytest.raises(ValueError, match="Invalid default_verdict"):
            StubPRReviewer(default_verdict="MAYBE")

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("NED_REVIEW_STUB_VERDICT", NEEDS_DISCUSSION)
        r = StubPRReviewer().review_pr("x")
        assert r.verdict == NEEDS_DISCUSSION


# ─────────────────────────────────────────────────────────────────────
# has_ned_review_label
# ─────────────────────────────────────────────────────────────────────


class TestHasNedReviewLabel:
    def test_label_present_string_list(self):
        assert has_ned_review_label([NED_REVIEW_LABEL, "other"]) is True

    def test_label_present_dict_list(self):
        assert (
            has_ned_review_label([{"name": NED_REVIEW_LABEL}, {"name": "unrelated"}])
            is True
        )

    def test_label_missing(self):
        assert has_ned_review_label(["agent:fred", "dispatch:ready"]) is False

    def test_label_empty_list(self):
        assert has_ned_review_label([]) is False

    def test_label_none(self):
        assert has_ned_review_label(None) is False

    def test_case_insensitive_match(self):
        assert has_ned_review_label(["Agent:Ned-Review"]) is True
        assert has_ned_review_label([{"name": "AGENT:NED-REVIEW"}]) is True

    def test_dict_entry_with_non_string_name_ignored(self):
        # Defensive: malformed label entries must not crash the trigger.
        assert has_ned_review_label([{"name": None}, {"name": 123}]) is False


# ─────────────────────────────────────────────────────────────────────
# NED_REVIEW_TARGET_STATE
# ─────────────────────────────────────────────────────────────────────


class TestNedReviewTargetState:
    """Routing table — single source of truth for state transitions."""

    def test_approve_routes_to_done(self):
        assert NED_REVIEW_TARGET_STATE[APPROVE] == "Done"

    def test_request_changes_routes_to_in_progress(self):
        assert NED_REVIEW_TARGET_STATE[REQUEST_CHANGES] == "In Progress"

    def test_needs_discussion_routes_to_in_review(self):
        assert NED_REVIEW_TARGET_STATE[NEEDS_DISCUSSION] == "In Review"


# ─────────────────────────────────────────────────────────────────────
# _format_linear_comment
# ─────────────────────────────────────────────────────────────────────


class TestFormatLinearComment:
    def test_approve_uses_checkmark_icon(self):
        result = PRReviewResult(verdict=APPROVE, summary="clean")
        body = _format_linear_comment(result, "https://pr/1")
        assert "✅" in body
        assert "`APPROVE`" in body
        assert "`Done`" in body
        assert "https://pr/1" in body

    def test_request_changes_uses_cross_icon(self):
        result = PRReviewResult(verdict=REQUEST_CHANGES, summary="fix it")
        body = _format_linear_comment(result, "https://pr/2")
        assert "❌" in body
        assert "`REQUEST_CHANGES`" in body
        assert "`In Progress`" in body

    def test_needs_discussion_uses_speech_icon(self):
        result = PRReviewResult(verdict=NEEDS_DISCUSSION, summary="ambiguous")
        body = _format_linear_comment(result, "https://pr/3")
        assert "💬" in body
        assert "leaving `In Review`" in body

    def test_inline_comments_rendered(self):
        result = PRReviewResult(
            verdict=REQUEST_CHANGES,
            summary="issues",
            inline_comments=[
                InlineComment(path="a.py", line=1, body="bug A"),
                InlineComment(path="b.py", line=2, body="bug B"),
            ],
        )
        body = _format_linear_comment(result, "https://pr/4")
        assert "### Inline comments" in body
        assert "`a.py:1` — bug A" in body
        assert "`b.py:2` — bug B" in body

    def test_inline_comments_capped_at_20(self):
        result = PRReviewResult(
            verdict=REQUEST_CHANGES,
            summary="many",
            inline_comments=[
                InlineComment(path=f"f{i}.py", line=1, body=str(i)) for i in range(25)
            ],
        )
        body = _format_linear_comment(result, "https://pr/5")
        assert "and 5 more" in body


# ─────────────────────────────────────────────────────────────────────
# trigger_ned_review — the orchestrator entry point
# ─────────────────────────────────────────────────────────────────────


class TestTriggerNedReview:
    """End-to-end behavior of the trigger, with fake Linear I/O."""

    def test_label_missing_returns_not_triggered(self):
        issue = {"identifier": "GRO-1", "labels": ["agent:fred"]}
        decision = trigger_ned_review(issue)
        assert decision.triggered is False
        assert decision.verdict == ""
        assert decision.target_state == ""
        assert decision.linear_comment == ""
        assert decision.metadata["reason"] == "label_missing"

    def test_label_missing_none_labels(self):
        issue = {"identifier": "GRO-2"}  # no labels key at all
        decision = trigger_ned_review(issue)
        assert decision.triggered is False

    def test_label_present_no_pr_routes_to_needs_discussion(self):
        issue = {
            "identifier": "GRO-3",
            "labels": [NED_REVIEW_LABEL],
            # no pr_url / pullRequestUrl / pull_request_url
        }
        decision = trigger_ned_review(issue)
        assert decision.triggered is True
        assert decision.verdict == NEEDS_DISCUSSION
        assert decision.target_state == "In Review"
        assert "no linked PR" in decision.linear_comment
        assert decision.metadata["reason"] == "pr_url_missing"

    def test_approve_path_calls_post_comment_and_transitions(self):
        comments: list[tuple[str, str]] = []
        transitions: list[tuple[str, str]] = []

        issue = {
            "identifier": "GRO-4",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/42",
        }
        decision = trigger_ned_review(
            issue,
            reviewer=FakeReviewer(APPROVE),
            post_comment=lambda i, b: comments.append((i, b)),
            transition_state=lambda i, s: transitions.append((i, s)),
        )

        assert decision.triggered is True
        assert decision.verdict == APPROVE
        assert decision.target_state == "Done"
        assert comments == [("GRO-4", decision.linear_comment)]
        assert transitions == [("GRO-4", "Done")]
        assert "✅" in decision.linear_comment

    def test_request_changes_path_reroutes_to_in_progress(self):
        transitions: list[tuple[str, str]] = []

        issue = {
            "identifier": "GRO-5",
            "labels": [{"name": NED_REVIEW_LABEL}],
            "pr_url": "https://github.com/org/repo/pull/7",
        }
        decision = trigger_ned_review(
            issue,
            reviewer=FakeReviewer(REQUEST_CHANGES),
            transition_state=lambda i, s: transitions.append((i, s)),
        )

        assert decision.verdict == REQUEST_CHANGES
        assert decision.target_state == "In Progress"
        assert transitions == [("GRO-5", "In Progress")]
        assert "re-routing" in decision.linear_comment

    def test_needs_discussion_path_leaves_in_review(self):
        issue = {
            "identifier": "GRO-6",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/9",
        }
        decision = trigger_ned_review(issue, reviewer=FakeReviewer(NEEDS_DISCUSSION))
        assert decision.verdict == NEEDS_DISCUSSION
        assert decision.target_state == "In Review"
        assert "leaving `In Review`" in decision.linear_comment

    def test_pullRequestUrl_alias_is_honored(self):
        # Linear sometimes returns the key as pullRequestUrl (camelCase).
        issue = {
            "identifier": "GRO-7",
            "labels": [NED_REVIEW_LABEL],
            "pullRequestUrl": "https://github.com/org/repo/pull/11",
        }
        reviewer = FakeReviewer(APPROVE)
        decision = trigger_ned_review(issue, reviewer=reviewer)
        assert decision.triggered is True
        assert reviewer.calls == ["https://github.com/org/repo/pull/11"]

    def test_pull_request_url_alias_is_honored(self):
        # GitHub webhook payload shape uses snake_case.
        issue = {
            "identifier": "GRO-8",
            "labels": [NED_REVIEW_LABEL],
            "pull_request_url": "https://api.github.com/repos/o/r/pulls/13",
        }
        reviewer = FakeReviewer(APPROVE)
        trigger_ned_review(issue, reviewer=reviewer)
        assert reviewer.calls == ["https://api.github.com/repos/o/r/pulls/13"]

    def test_default_reviewer_used_when_none_provided(self):
        issue = {
            "identifier": "GRO-9",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/100",
        }
        decision = trigger_ned_review(issue)
        assert decision.verdict == APPROVE  # StubPRReviewer default

    def test_post_comment_callback_error_swallowed(self):
        def boom(_i: str, _b: str) -> None:
            raise RuntimeError("linear API down")

        issue = {
            "identifier": "GRO-10",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/200",
        }
        decision = trigger_ned_review(
            issue,
            reviewer=FakeReviewer(APPROVE),
            post_comment=boom,
        )
        assert decision.triggered is True
        assert decision.verdict == APPROVE
        assert "post_comment_error" in decision.metadata
        assert "linear API down" in decision.metadata["post_comment_error"]

    def test_transition_state_callback_error_swallowed(self):
        def boom(_i: str, _s: str) -> None:
            raise RuntimeError("state machine locked")

        issue = {
            "identifier": "GRO-11",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/201",
        }
        decision = trigger_ned_review(
            issue,
            reviewer=FakeReviewer(REQUEST_CHANGES),
            transition_state=boom,
        )
        assert decision.verdict == REQUEST_CHANGES
        assert "transition_error" in decision.metadata

    def test_no_callbacks_is_safe(self):
        # No post_comment, no transition_state — trigger still returns
        # a decision. (Useful for dry-run / audit-mode invocations.)
        issue = {
            "identifier": "GRO-12",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/300",
        }
        decision = trigger_ned_review(issue, reviewer=FakeReviewer(APPROVE))
        assert decision.triggered is True
        assert decision.approved is True

    def test_decision_to_dict_round_trip(self):
        issue = {
            "identifier": "GRO-13",
            "labels": [NED_REVIEW_LABEL],
            "pr_url": "https://github.com/org/repo/pull/400",
        }
        decision = trigger_ned_review(issue, reviewer=FakeReviewer(APPROVE))
        d = decision.to_dict()
        assert d["identifier"] == "GRO-13"
        assert d["triggered"] is True
        assert d["verdict"] == APPROVE
        assert d["target_state"] == "Done"
        assert isinstance(d["linear_comment"], str)
        assert d["linear_comment"]  # non-empty
        assert isinstance(d["metadata"], dict)


# ─────────────────────────────────────────────────────────────────────
# NedReviewDecision
# ─────────────────────────────────────────────────────────────────────


class TestNedReviewDecision:
    def test_approved_property(self):
        d = NedReviewDecision(
            identifier="x",
            triggered=True,
            verdict=APPROVE,
            target_state="Done",
            linear_comment="ok",
        )
        assert d.approved is True

    def test_approved_property_false_for_other_verdicts(self):
        for verdict in (REQUEST_CHANGES, NEEDS_DISCUSSION, ""):
            d = NedReviewDecision(
                identifier="x",
                triggered=True,
                verdict=verdict,
                target_state="",
                linear_comment="",
            )
            assert d.approved is False
