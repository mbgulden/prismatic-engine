from copy import deepcopy

from prismatic.completed_work_gate import (
    AGY_COMPLETED_WORK_MARKER,
    GateClassification,
    classify_completed_work,
    completed_work_gate_schema,
    demo_completed_work_gate_state,
    demo_completed_work_packet,
)


def packet():
    return deepcopy(demo_completed_work_packet())


def test_merge_ready_packet_is_eligible_but_does_not_auto_merge():
    state = classify_completed_work(packet())

    assert state.classification == GateClassification.MERGE_READY
    assert state.as_dict()["classification"] == "merge_ready"
    assert state.eligible_for_merge is True
    assert "manual merge still required" in state.reasons[0]
    assert state.linear_status == "Awaiting Fred merge review"


def test_missing_proof_blocks_packet():
    p = packet()
    p.pop("proof")

    state = classify_completed_work(p)

    assert state.classification == GateClassification.BLOCKED_MISSING_PROOF
    assert state.eligible_for_merge is False
    assert "proof" in state.reasons[0]


def test_failed_verification_blocks_packet():
    p = packet()
    p["proof"]["result"] = "FAIL"

    state = classify_completed_work(p)

    assert state.classification == GateClassification.BLOCKED_FAILED_VERIFICATION
    assert state.eligible_for_merge is False
    assert state.linear_status == "Blocked: verification failed"


def test_lane_scope_mismatch_requires_manual_review():
    p = packet()
    p["lane_scope"] = {
        "allowed_paths": ["docs/"],
        "touched_paths": ["prismatic/demo.py"],
    }

    state = classify_completed_work(p)

    assert state.classification == GateClassification.MANUAL_REVIEW_SCOPE
    assert state.eligible_for_merge is False
    assert "outside lane scope" in state.reasons[0]


def test_dirty_untrusted_source_requires_clean_rebuild():
    state = classify_completed_work(packet(), dirty_source=True)

    assert state.classification == GateClassification.CLEAN_REBUILD_REQUIRED
    assert state.requires_clean_rebuild is True
    assert state.linear_status == "Clean rebuild required before review"


def test_stale_source_is_superseded():
    state = classify_completed_work(packet(), source_is_stale=True)

    assert state.classification == GateClassification.SUPERSEDED
    assert state.eligible_for_merge is False
    assert state.dashboard_label == "Superseded by current base"


def test_conflicts_require_manual_review_conflict():
    state = classify_completed_work(packet(), conflicts=["prismatic/gateway/server.py"])

    assert state.classification == GateClassification.MANUAL_REVIEW_CONFLICT
    assert state.eligible_for_merge is False
    assert "conflicts require manual review" in state.reasons[0]


def test_untrusted_agent_is_rejected():
    p = packet()
    p["agent"] = "unknown"

    state = classify_completed_work(p)

    assert state.classification == GateClassification.REJECTED
    assert state.eligible_for_merge is False


def test_demo_schema_and_state_are_machine_readable():
    schema = completed_work_gate_schema()
    demo = demo_completed_work_gate_state()

    assert schema["marker"] == AGY_COMPLETED_WORK_MARKER
    assert "merge_ready" in schema["classifications"]
    assert schema["non_claims"] == ["no_auto_merge", "no_bulk_agy_dispatch", "contract_gate_only"]
    assert "non_claims" in schema["minimum_packet"]["proof"]
    assert "not_claiming" not in schema["minimum_packet"]["proof"]
    assert demo["status"] == "ok"
    assert demo["mode"] == "fixture"
    assert demo["gate"]["classification"] == "merge_ready"
