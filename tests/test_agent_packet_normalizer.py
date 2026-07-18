from __future__ import annotations

import json

from prismatic.agent_packet_normalizer import (
    REPAIR_HINTS,
    NormalizationStatus,
    normalize_agent_output,
    repair_preview,
)


_GATE_ACCEPTED_SOURCE_PATH = f"{'/home'}/ubuntu/work/agy-gro-3952-proof"


def valid_packet(**overrides):
    packet = {
        "agent": "agy",
        "source_branch": "feature/gro-3952-proof",
        "source_path": _GATE_ACCEPTED_SOURCE_PATH,
        "base_branch": "main",
        "changed_files": ["prismatic/agent_raw_output_queue.py"],
        "result_summary": "raw output queue implemented",
        "proof": {
            "command": "python -m pytest tests/test_agent_raw_output_queue.py",
            "result": "PASS",
            "log": "/tmp/fred-raw-agent-output-repair-queue-verify.log",
            "scope": "raw output queue",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "marker": "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK",
            "non_claims": [
                "auto_rerun_enabled",
                "auto_merge_enabled",
                "production_deploy",
            ],
        },
        "lane_scope": {
            "allowed_paths": ["prismatic/"],
            "touched_paths": ["prismatic/agent_raw_output_queue.py"],
        },
    }
    packet.update(overrides)
    return packet


def test_all_required_repair_hints_are_named() -> None:
    assert REPAIR_HINTS == {
        "missing_source_path",
        "missing_proof_log",
        "missing_non_claims",
        "invalid_changed_files",
        "production_claim_without_proof",
        "agent_prose_only",
        "secret_like_content_detected",
        "wrong_agent_or_ambiguous_agent",
    }


def test_valid_packet_is_accepted() -> None:
    result = normalize_agent_output(json.dumps(valid_packet()), expected_agent="agy")
    assert result.status == NormalizationStatus.ACCEPTED
    assert result.canonical_packet_id is not None
    assert result.canonical_packet_id.startswith("packet_")
    assert result.repair_hint is None
    assert result.rerun_allowed is False


def test_missing_source_path_is_rerun_required() -> None:
    packet = valid_packet()
    packet.pop("source_path")
    result = normalize_agent_output(json.dumps(packet), expected_agent="agy")
    assert result.status == NormalizationStatus.REJECTED_RERUN_REQUIRED
    assert result.repair_hint == "missing_source_path"
    assert result.rerun_allowed is True


def test_missing_proof_log_is_repairable_not_success() -> None:
    packet = valid_packet()
    packet["proof"].pop("log")
    result = normalize_agent_output(json.dumps(packet), expected_agent="agy")
    assert result.status == NormalizationStatus.REJECTED_REPAIRABLE
    assert result.repair_hint == "missing_proof_log"
    assert result.canonical_packet_id is None


def test_missing_non_claims_is_repairable() -> None:
    packet = valid_packet()
    packet["proof"].pop("non_claims")
    result = normalize_agent_output(json.dumps(packet), expected_agent="agy")
    assert result.status == NormalizationStatus.REJECTED_REPAIRABLE
    assert result.repair_hint == "missing_non_claims"


def test_invalid_changed_files_requires_rerun() -> None:
    result = normalize_agent_output(
        json.dumps(valid_packet(changed_files=[])), expected_agent="agy"
    )
    assert result.status == NormalizationStatus.REJECTED_RERUN_REQUIRED
    assert result.repair_hint == "invalid_changed_files"
    assert result.rerun_allowed is True


def test_production_claim_without_proof_is_repairable_hint() -> None:
    packet = valid_packet(runtime_deployed=True)
    packet["proof"].pop("non_claims")
    result = normalize_agent_output(json.dumps(packet), expected_agent="agy")
    assert result.status == NormalizationStatus.REJECTED_REPAIRABLE
    assert result.repair_hint == "production_claim_without_proof"


def test_agent_prose_only_is_repairable() -> None:
    result = normalize_agent_output(
        "I did the work but forgot the packet.", expected_agent="agy"
    )
    assert result.status == NormalizationStatus.REJECTED_REPAIRABLE
    assert result.repair_hint == "agent_prose_only"
    assert result.rerun_allowed is False


def test_secret_like_content_is_policy_violation() -> None:
    secret_like_output = "".join(
        ["OPENAI_API", "_KEY=", "sk", "-", "test-secret-like-token"]
    )
    result = normalize_agent_output(secret_like_output, expected_agent="agy")
    assert result.status == NormalizationStatus.REJECTED_POLICY_VIOLATION
    assert result.repair_hint == "secret_like_content_detected"
    assert result.rerun_allowed is False


def test_wrong_agent_is_policy_violation() -> None:
    result = normalize_agent_output(
        json.dumps(valid_packet(agent="super-agent")), expected_agent="agy"
    )
    assert result.status == NormalizationStatus.REJECTED_POLICY_VIOLATION
    assert result.repair_hint == "wrong_agent_or_ambiguous_agent"


def test_repair_preview_does_not_auto_repair_or_rerun() -> None:
    preview = repair_preview("plain prose", expected_agent="agy")
    assert preview["would_auto_repair"] is False
    assert preview["would_auto_rerun"] is False
    assert preview["repair_hint"] == "agent_prose_only"
