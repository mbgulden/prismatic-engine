from prismatic.prompt4_packet_gate import (
    evaluate_prompt4_packet_gate,
    parse_packet_observations,
)


def packet(created_at: str, result: str, marker: str):
    return {
        "createdAt": created_at,
        "body": f"RESULT={result}\nLOG=/tmp/proof.log\nSCOPE=test\nAD_HOC_OR_CANONICAL=ad-hoc targeted\nNOT_CLAIMING=Prompt5 unlocked\nMARKER={marker}\n",
        "user": {"name": "tester"},
    }


def test_later_pass_supersedes_historical_blocker_for_required_agents():
    comments = {
        "GRO-3952": [
            packet(
                "2026-07-18T05:55:08Z",
                "BLOCKED",
                "RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED",
            ),
            packet(
                "2026-07-18T06:48:47Z",
                "PASS",
                "RAW_AGENT_OUTPUT_REPAIR_QUEUE_PR_READY_OK",
            ),
        ],
        "GRO-3954": [
            packet(
                "2026-07-18T08:59:25Z",
                "BLOCKED",
                "AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED",
            ),
            packet(
                "2026-07-18T09:51:00Z", "PASS", "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"
            ),
        ],
    }
    result = evaluate_prompt4_packet_gate(comments)
    assert result["complete"] is True
    assert result["found"]["fred"] is True
    assert result["found"]["agy"] is True
    assert result["blocked"]["fred"] is False
    assert result["blocked"]["agy"] is False
    assert result["superseded_blocked"]["fred"] == 1
    assert result["superseded_blocked"]["agy"] == 1


def test_george_is_reported_but_not_required():
    result = evaluate_prompt4_packet_gate(
        {
            "GRO-3952": [
                packet(
                    "2026-07-18T06:48:47Z",
                    "PASS",
                    "FRED_PROMPT4_PACKET_SUPERSESSION_OK",
                )
            ],
            "GRO-3954": [
                packet(
                    "2026-07-18T09:51:00Z",
                    "PASS",
                    "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK",
                )
            ],
        }
    )
    assert result["required_agents"] == ["fred", "agy"]
    assert result["complete"] is True
    assert result["found"]["george"] is False


def test_mixed_comment_pairs_each_marker_with_nearest_preceding_result():
    body = """RESULT=BLOCKED
MARKER=RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED

RESULT=PASS
MARKER=FRED_GRO3952_FOLLOWUP_AD_HOC_VERIFIED_OK
"""
    observations = parse_packet_observations(
        "GRO-3952", [{"createdAt": "2026-07-18T06:17:20Z", "body": body}]
    )
    assert len(observations) == 1
    assert observations[0].result == "BLOCKED"
    assert observations[0].state == "blocked"


def test_newer_blocker_remains_active():
    result = evaluate_prompt4_packet_gate(
        {
            "GRO-3952": [
                packet(
                    "2026-07-18T06:48:47Z",
                    "PASS",
                    "RAW_AGENT_OUTPUT_REPAIR_QUEUE_PR_READY_OK",
                ),
                packet(
                    "2026-07-18T07:00:00Z",
                    "BLOCKED",
                    "RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED",
                ),
            ],
            "GRO-3954": [
                packet(
                    "2026-07-18T09:51:00Z",
                    "PASS",
                    "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK",
                )
            ],
        }
    )
    assert result["complete"] is False
    assert result["blocked"]["fred"] is True
    assert result["missing"] == ["fred"]
