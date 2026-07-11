"""Tests for explicit lane dispatch contracts.

Placed under ``prismatic/`` because Ned's current lane policy rejects root-level
``tests/`` changes from Ned branches.
"""

from prismatic.lane_contracts import (
    LANE_CONTRACTS,
    filter_dispatchable_issues,
    issue_label_names,
    qualifies_for_dispatch,
    starvation_signal_for,
)


def issue(labels, state="Backlog"):
    return {
        "id": "issue-1",
        "identifier": "GRO-1",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "state": {"name": state},
    }


def test_all_phase_3_lane_contracts_exist():
    assert {"agy", "kai", "ned", "jules", "fred"} <= set(LANE_CONTRACTS)
    assert LANE_CONTRACTS["jules"].queue_kind == "review"
    assert LANE_CONTRACTS["ned"].queue_kind == "execution"


def test_agent_label_alone_is_not_enough_when_state_is_wrong():
    ok, reason = qualifies_for_dispatch(
        issue(["agent:ned"], state="Done"),
        "ned",
        include_reason=True,
    )

    assert ok is False
    assert reason == "state_not_allowed:Done"


def test_execution_lanes_do_not_accept_review_only_work():
    ok, reason = qualifies_for_dispatch(
        issue(["agent:agy", "review:only"]),
        "agy",
        include_reason=True,
    )

    assert ok is False
    assert reason == "review_only_not_execution:review:only"


def test_review_lane_accepts_review_only_work():
    assert qualifies_for_dispatch(issue(["agent:jules", "review:only"], state="In Review"), "jules")


def test_filter_dispatchable_issues_reports_held_reasons():
    good = issue(["agent:ned"])
    bad = issue(["agent:ned"], state="Cancelled")

    dispatchable, held = filter_dispatchable_issues([good, bad], "ned")

    assert dispatchable == [good]
    assert held == [(bad, "state_not_allowed:Cancelled")]


def test_issue_label_names_accepts_flat_and_graphql_shapes():
    assert issue_label_names({"labels": ["agent:ned"]}) == {"agent:ned"}
    assert issue_label_names(issue(["agent:ned", "dispatch:ready"])) == {
        "agent:ned",
        "dispatch:ready",
    }


def test_starvation_signals_are_stable_per_lane():
    assert starvation_signal_for("ned") == "ned_queue_empty"
    assert starvation_signal_for("jules") == "jules_review_queue_empty"
