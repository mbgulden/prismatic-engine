"""
Unit tests for AGY task completion hold policy (ACCEPTANCE_DECISION=PENDING -> blocked_needs_operator).
"""

from pathlib import Path

import pytest

from prismatic.agy_completed_work import normalize_agy_result_packet
from prismatic.completed_work_gate import classify_completed_work, GateClassification


def test_agy_pending_acceptance_held_as_blocked_needs_operator():
    home_work_dir = str(Path.home() / "work" / "prismatic-engine-stable")
    packet = {
        "agent": "agy",
        "STATUS": "PASS",
        "PRODUCER_STATUS": "PASS",
        "ACCEPTANCE_DECISION": "PENDING",
        "TASK_ID": "GRO-4500",
        "ATTEMPT_ID": "attempt-20260804-01",
        "BASE_HEAD": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        "CANDIDATE_HEAD": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        "CANDIDATE_TREE": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
        "SOURCE_BRANCH": "feature/GRO-4500-closeout",
        "SOURCE_PATH": home_work_dir,
        "BASE_BRANCH": "main",
        "CHANGED_PATHS": ["prismatic/cli/main.py"],
        "COMMAND": ["pytest tests/test_cli_closeout.py"],
        "RESULT": "PASS",
        "LOG": "/tmp/attempt-20260804-01.log",
        "LOG_SHA256": "8bc8eda031628bee7845b175003e4274563eb2a3a8cf5e5dddfe9b298603983c",
        "result_artifacts": [f"{home_work_dir}/logs/attempt-20260804-01.log"],
        "SCOPE": "Verified CLI closeout contract validation",
        "merge_lane": "backend-api",
        "risk_level": "low",
        "AD_HOC_OR_CANONICAL": "canonical suite",
        "PROOF_CLASSES": ["focused"],
        "SIDE_EFFECTS": {
            "push": False,
            "pr": False,
            "merge": False,
            "deploy": False,
            "linear_updated": True,
        },
        "BLOCKERS": [],
        "NOT_CLAIMING": [],
        "NEXT_ACTION": "merge-ready",
        "MARKER": "AGY_TASK_RESULT_PACKET_OK",
    }

    normalized = normalize_agy_result_packet(packet)
    gate_state = classify_completed_work(normalized)

    # Must classify as MANUAL_REVIEW_SCOPE (which maps to blocked_needs_operator)
    assert gate_state.classification == GateClassification.MANUAL_REVIEW_SCOPE
    assert gate_state.eligible_for_merge is False
    assert any("awaiting_review_factory_decision" in r for r in gate_state.reasons)


def test_modern_agy_packet_cannot_bypass_v02_contract():
    with pytest.raises(ValueError, match="require the v0.2 closeout contract"):
        normalize_agy_result_packet(
            {
                "agent": "agy",
                "issue_identifier": "GRO-4501",
                "branch": "feature/GRO-4501-bypass",
                "base_branch": "main",
            }
        )
