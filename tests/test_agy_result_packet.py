from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from prismatic.agy_result_packet import (
    AGY_RESULT_PACKET_MARKER,
    AGY_RESULT_PACKET_SCHEMA_MARKER,
    ResultPacketValidationError,
    is_raw_agy_result_packet,
    require_valid_packet,
    validate_packet,
)


def valid_packet(**overrides):
    packet = {
        "agent": "agy",
        "issue_identifier": "GRO-3837",
        "branch": "feature/agy-dashboard-canary",
        "base_branch": "main",
        "changed_files": ["docs/example.md"],
        "result_artifacts": [
            str(Path.home() / ".prismatic" / "agy-results" / "GRO-3837" / "RESULT.md")
        ],
        "verification": {
            "commands": ["python3 -m pytest tests/test_agy_result_packet.py"],
            "result": "PASS",
            "log_path": "/tmp/fred-agy-autopilot-verify.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["production_deploy", "auto_merge_enabled"],
        "merge_lane": "docs",
        "risk_level": "low",
        "next_action": "merge-ready",
        "marker": AGY_RESULT_PACKET_MARKER,
    }
    for key, value in overrides.items():
        if key == "verification":
            packet["verification"].update(value)
        else:
            packet[key] = value
    return packet


def errors_for(packet):
    return "\n".join(validate_packet(packet).errors)


def test_valid_raw_result_packet_passes_without_pr_url():
    packet = valid_packet()
    result = validate_packet(packet)
    assert result.ok, result.errors
    assert require_valid_packet(packet) is packet
    assert is_raw_agy_result_packet(packet) is True
    assert AGY_RESULT_PACKET_SCHEMA_MARKER == "AGY_RESULT_PACKET_SCHEMA_OK"


def test_pr_url_is_optional_but_must_be_real_pull_url_when_present():
    assert validate_packet(valid_packet(pr_url=None)).ok
    assert validate_packet(
        valid_packet(pr_url="https://github.com/acme/repo/pull/123")
    ).ok
    result = validate_packet(valid_packet(pr_url="https://example.com/not-a-pr"))
    assert not result.ok
    assert "pr_url must be null or GitHub PR URL" in errors_for(
        valid_packet(pr_url="https://example.com/not-a-pr")
    )


def test_branch_policy_requires_feature_branch():
    packet = valid_packet(branch="agy/GRO-3837-dashboard-canary")
    result = validate_packet(packet)
    assert not result.ok
    assert "branch must be a feature/* branch" in errors_for(packet)


def test_missing_artifact_is_rejected_before_normalization():
    packet = valid_packet(result_artifacts=[])
    result = validate_packet(packet)
    assert not result.ok
    assert "result_artifacts must contain at least 1 item" in errors_for(packet)


def test_arbitrary_tmp_artifact_is_not_safe_raw_provenance():
    packet = valid_packet(result_artifacts=["/tmp/agy/GRO-3837/result.json"])
    result = validate_packet(packet)
    assert not result.ok
    assert "arbitrary /tmp provenance" in errors_for(packet)


def test_secret_like_content_is_rejected_secret_safely():
    packet = valid_packet()
    packet["verification"] = copy.deepcopy(packet["verification"])
    fake_token = "ghp_" + "1234567890abcdef" + "1234567890abcdef" + "1234"
    packet["verification"]["commands"] = [f"export TOKEN={fake_token}"]
    result = validate_packet(packet)
    assert not result.ok
    assert "secret-like content" in errors_for(packet)
    assert fake_token not in errors_for(packet)


def test_non_claims_are_not_positive_production_claims():
    packet = valid_packet(non_claims=["production deployed", "auto_merge_enabled"])
    result = validate_packet(packet)
    assert result.ok, result.errors


def test_unknown_property_behavior_matches_json_schema():
    packet = valid_packet(unexpected="nope")
    py_result = validate_packet(packet)
    assert not py_result.ok
    assert "unknown field(s): unexpected" in errors_for(packet)

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "agy-result-packet.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    validator = Draft202012Validator(schema)
    schema_errors = sorted(
        validator.iter_errors(packet), key=lambda error: list(error.path)
    )
    assert schema_errors
    assert any("Additional properties" in error.message for error in schema_errors)


def test_next_action_blocked_cannot_be_merge_ready():
    packet = valid_packet(next_action="blocked", verification={"result": "PASS"})
    result = validate_packet(packet)
    assert not result.ok
    assert (
        "next_action blocked requires verification.result FAIL or BLOCKED"
        in errors_for(packet)
    )


def test_high_risk_manual_review_semantics_survive_raw_validation():
    packet = valid_packet(risk_level="high", merge_lane="manual-review")
    result = validate_packet(packet)
    assert not result.ok
    err = errors_for(packet)
    assert "high risk packets cannot be merge-ready" in err
    assert "mixed/manual-review lanes cannot be merge-ready" in err


def test_require_valid_packet_raises_structured_errors():
    packet = valid_packet(branch="local/not-feature")
    with pytest.raises(ResultPacketValidationError) as exc:
        require_valid_packet(packet)
    assert exc.value.errors == ("branch must be a feature/* branch",)
