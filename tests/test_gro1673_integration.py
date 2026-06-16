"""GRO-1673 integration coverage for the 7-step loop and mode policy edge cases.

These tests intentionally stitch together the existing state machine, mode switch,
RFR quality gate, and fallback router behavior without calling live external APIs.
"""

import tempfile

from prismatic.mode_switch import ModeSwitch
from prismatic.rfr_loop import (
    FeedbackItem,
    QualityGate,
    ReviewFeedbackRefineLoop,
    ReviewResult,
    ReviewSeverity,
    ReviewVerdict,
)
from prismatic.state_machine import PipelineStateMachine, Step


class FakeTelemetryCollector:
    """Tiny circuit-breaker fake used to exercise router fallback behavior."""

    def __init__(self, tripped=None, decisions=None):
        self.tripped = set(tripped or [])
        self.decisions = list(decisions or [])
        self.events = []
        self.loops = []
        self.checks = []
        self.resets = []
        self.failures = []
        self._call_counts = {}

    def check_circuit(self, *, issue_id, agent, micro_count=1, macro_count=0):
        self.checks.append((issue_id, agent, micro_count, macro_count))
        if self.decisions:
            return self.decisions.pop(0)

        # Fall back to tracking using the tripped set of model names.
        key = (issue_id, agent)
        count = self._call_counts.get(key, 0)
        self._call_counts[key] = count + 1

        chain = ["deepseek-v4-flash", "gpt-4o-mini"]
        if count < len(chain):
            model = chain[count]
        else:
            model = None

        return model in self.tripped if model else True

    def record_failure(self, model, micro_cost=1, macro_cost=0):
        self.failures.append((model, micro_cost, macro_cost))
        self.tripped.add(model)

    def reset_breaker(self, issue_id):
        self.resets.append(issue_id)

    def record_event(self, event_type, payload):
        self.events.append((event_type, payload))

    def record_loop(self, **kwargs):
        self.loops.append(kwargs)
        loop_type = kwargs.get("loop_type", "unknown")
        self.events.append((loop_type, kwargs))


class FakeReviewAgent:
    """Deterministic review agent for RFR loop integration tests."""

    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []

    def review(self, issue_id, execution_output, cycle_number=1, context=None):
        self.calls.append((issue_id, execution_output, cycle_number, context or {}))
        result = self.reviews.pop(0)
        result.cycle_number = cycle_number
        return result


def test_full_seven_step_cycle_records_ordered_history_and_completes():
    with tempfile.TemporaryDirectory() as tmp:
        sm = PipelineStateMachine("GRO-1673-full-cycle", mode="autonomous", store_dir=tmp)

        for step in [
            Step.DECOMPOSE,
            Step.DISPATCH,
            Step.EXECUTE,
            Step.REVIEW,
            Step.FEEDBACK,
            Step.REFINE,
            Step.INTEGRATE,
            Step.COMPLETED,
        ]:
            sm.transition(step, agent="agy", metadata={"issue": "GRO-1673"})

        snapshot = sm.snapshot()
        assert sm.is_complete()
        assert sm.progress_pct() == 100.0
        assert [event["to_step"] for event in snapshot["history"]] == [
            "decompose",
            "dispatch",
            "execute",
            "review",
            "feedback",
            "refine",
            "integrate",
            "completed",
        ]
        assert all(event["agent"] == "agy" for event in snapshot["history"])


def test_collaborative_mode_pauses_at_review_and_integrate_but_autonomous_does_not():
    collaborative = ModeSwitch("collaborative")
    autonomous = ModeSwitch("autonomous")

    for step in [Step.DECOMPOSE, Step.DISPATCH, Step.EXECUTE, Step.FEEDBACK, Step.REFINE]:
        assert collaborative.can_auto_advance(step, Step.EXECUTE)
        assert not collaborative.requires_approval(step)

    assert collaborative.requires_approval(Step.REVIEW)
    assert collaborative.requires_approval(Step.INTEGRATE)
    assert not collaborative.can_auto_advance(Step.REVIEW, Step.FEEDBACK)
    assert not collaborative.can_auto_advance(Step.INTEGRATE, Step.COMPLETED)

    for step in [
        Step.DECOMPOSE,
        Step.DISPATCH,
        Step.EXECUTE,
        Step.REVIEW,
        Step.FEEDBACK,
        Step.REFINE,
        Step.INTEGRATE,
    ]:
        assert autonomous.can_auto_advance(step, Step.COMPLETED)
        assert not autonomous.requires_approval(step)


def test_mode_switch_set_mode_records_transition_history_and_changes_gate_behavior():
    switch = ModeSwitch("interactive")
    assert switch.requires_approval(Step.EXECUTE)
    assert not switch.can_auto_advance(Step.EXECUTE, Step.REVIEW)

    switch.set_mode("autonomous")

    assert not switch.requires_approval(Step.EXECUTE)
    assert switch.can_auto_advance(Step.EXECUTE, Step.REVIEW)
    assert switch.mode_transition_count() == 1
    history = switch.snapshot()["history"]
    assert history[-1]["from"] == "interactive"
    assert history[-1]["to"] == "autonomous"


def test_retry_threshold_escalates_per_mode_and_failure_state_blocks_recovery_without_reset():
    with tempfile.TemporaryDirectory() as tmp:
        sm = PipelineStateMachine("GRO-1673-timeout", mode="interactive", store_dir=tmp)
        sm.advance(agent="fred")
        failure = sm.fail(reason="timeout while waiting for worker heartbeat", agent="watchdog")

        assert failure.to_step == Step.FAILED
        assert sm.is_failed()
        assert sm.is_terminal()
        assert sm.next_step() is None
        assert not sm.can_transition(Step.DISPATCH)

    assert ModeSwitch("interactive").should_escalate(1)
    assert not ModeSwitch("collaborative").should_escalate(2)
    assert ModeSwitch("collaborative").should_escalate(3)
    assert not ModeSwitch("autonomous").should_escalate(4)
    assert ModeSwitch("autonomous").should_escalate(5)


def test_partial_failure_recovery_loops_refine_back_to_review_then_integrates_after_approval():
    with tempfile.TemporaryDirectory() as tmp:
        sm = PipelineStateMachine("GRO-1673-partial-recovery", mode="collaborative", store_dir=tmp)
        for step in [
            Step.DECOMPOSE,
            Step.DISPATCH,
            Step.EXECUTE,
            Step.REVIEW,
            Step.FEEDBACK,
            Step.REFINE,
        ]:
            sm.advance(agent="ned")

        sm.transition(Step.REVIEW, agent="agy", metadata={"reason": "verify fixes"})
        sm.advance(agent="agy")
        sm.advance(agent="ned")
        sm.advance(agent="fred")
        sm.advance(agent="fred")

        snapshot = sm.snapshot()
        assert sm.is_complete()
        assert snapshot["review_cycles"] == 1
        assert [event["to_step"] for event in snapshot["history"]].count("review") == 2


def test_rfr_full_cycle_escalates_timeout_blockers_after_mode_retry_limit():
    with tempfile.TemporaryDirectory() as tmp:
        sm = PipelineStateMachine("GRO-1673-rfr-timeout", mode="interactive", store_dir=tmp)
        for step in [Step.DECOMPOSE, Step.DISPATCH, Step.EXECUTE]:
            sm.advance(agent="ned")

        timeout_review = ReviewResult(
            verdict=ReviewVerdict.NEEDS_REWORK,
            feedback_items=[
                FeedbackItem(
                    severity=ReviewSeverity.BLOCKER,
                    message="worker timeout: no heartbeat before deadline",
                    category="timeout",
                )
            ],
            summary="timeout blocker",
            reviewer="agy",
        )
        loop = ReviewFeedbackRefineLoop(
            mode_switch=ModeSwitch("interactive"),
            review_agent=FakeReviewAgent([timeout_review]),
        )

        result = loop.run_full_cycle(
            issue_id="GRO-1673-rfr-timeout",
            execution_output="worker timed out before producing a walkthrough",
            state_machine=sm,
            context={"timeout_seconds": 300},
        )

        assert not result.passed
        assert result.cycles_completed == 1
        assert result.final_verdict == ReviewVerdict.NEEDS_REWORK
        assert "Max retries (1) exceeded" in result.escalation_reason
        assert sm.current_step == Step.FEEDBACK
        assert sm.snapshot()["history"][-1]["metadata"]["verdict"] == "needs_rework"


def test_quality_gate_routes_blockers_to_rework_and_clean_reviews_to_approval():
    gate = QualityGate(policy=ModeSwitch("collaborative").policy, mode=ModeSwitch("collaborative").mode)

    blocked = ReviewResult(
        verdict=ReviewVerdict.NEEDS_REWORK,
        feedback_items=[FeedbackItem(severity=ReviewSeverity.BLOCKER, message="missing fallback test")],
    )
    clean = ReviewResult(verdict=ReviewVerdict.APPROVED)

    assert gate.evaluate(blocked) == ReviewVerdict.NEEDS_REWORK
    assert gate.evaluate(clean) == ReviewVerdict.APPROVED


def test_dynamic_fallback_router_skips_tripped_primary_and_reports_exhaustion():
    from prismatic.router import DynamicFallbackRouter

    telemetry = FakeTelemetryCollector(decisions=[True, False])
    router = DynamicFallbackRouter(
        telemetry_collector=telemetry,
        fallback_chains={"fred": {"primary": ["deepseek-v4-flash", "gpt-4o-mini"]}},
    )

    selected = router.select_route("GRO-1673", "fred")
    assert selected["model"] == "gpt-4o-mini"
    assert selected["fallback"] is True

    exhausted = FakeTelemetryCollector(decisions=[True, True])
    exhausted_router = DynamicFallbackRouter(
        telemetry_collector=exhausted,
        fallback_chains={"fred": {"primary": ["deepseek-v4-flash", "gpt-4o-mini"]}},
    )

    assert exhausted_router.select_route("GRO-1673", "fred")["model"] is None
    assert exhausted.loops[-1]["loop_type"] == "fallback_exhausted"
