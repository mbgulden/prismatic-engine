from __future__ import annotations

import copy

from prismatic.agy_result_packet import (
    AGY_RESULT_PACKET_MARKER,
    AGY_RESULT_PACKET_SCHEMA_MARKER,
    require_valid_packet,
    validate_packet,
)


def valid_packet(**overrides):
    packet = {
        "agent": "agy",
        "issue_identifier": "GRO-3837",
        "branch": "agy/GRO-3837-dashboard-canary",
        "base_branch": "main",
        "changed_files": ["docs/example.md"],
        "pr_url": None,
        "result_artifacts": ["/tmp/agy/GRO-3837/result.json"],
        "verification": {
            "commands": ["python3 -m pytest tests/test_agy_result_packet.py"],
            "result": "PASS",
            "log_path": "/tmp/fred-agy-autopilot-verify.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["canonical_full_suite_green", "auto_merge_enabled"],
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


def test_valid_result_packet_passes():
    packet = valid_packet()
    result = validate_packet(packet)
    assert result.ok, result.errors
    assert require_valid_packet(packet) is packet
    assert AGY_RESULT_PACKET_SCHEMA_MARKER == "AGY_RESULT_PACKET_SCHEMA_OK"


def test_missing_pr_field_is_rejected_even_when_pr_can_be_null():
    packet = valid_packet()
    packet.pop("pr_url")
    result = validate_packet(packet)
    assert not result.ok
    assert "missing required field" in errors_for(packet)


def test_merge_ready_packet_without_artifact_is_rejected():
    packet = valid_packet(result_artifacts=[])
    result = validate_packet(packet)
    assert not result.ok
    assert "result_artifact" in errors_for(packet)


def test_missing_verification_command_is_rejected():
    packet = valid_packet(verification={"commands": []})
    result = validate_packet(packet)
    assert not result.ok
    assert "verification.commands" in errors_for(packet)


def test_out_of_lane_generated_or_vendor_files_are_rejected():
    packet = valid_packet(changed_files=[".venv/lib/python/site.py", "docs/example.md"])
    result = validate_packet(packet)
    assert not result.ok
    assert "generated/vendor/cache" in errors_for(packet)


def test_production_claim_without_public_runtime_proof_is_rejected():
    packet = valid_packet(
        non_claims=["production deployed"],
        verification={"commands": ["python3 -m pytest tests/test_agy_result_packet.py"], "log_path": "/tmp/proof.log"},
    )
    result = validate_packet(packet)
    assert not result.ok
    assert "production/runtime/public claims require explicit proof" in errors_for(packet)


def test_secret_like_content_is_rejected():
    packet = valid_packet()
    packet["verification"] = copy.deepcopy(packet["verification"])
    fake_token = "ghp_" + "1234567890abcdef" + "1234567890abcdef" + "1234"
    packet["verification"]["commands"] = [f"export TOKEN={fake_token}"]
    result = validate_packet(packet)
    assert not result.ok
    assert "secret-like content" in errors_for(packet)


def test_dashboard_ui_merge_ready_requires_dashboard_or_browser_proof():
    packet = valid_packet(
        merge_lane="dashboard-ui",
        changed_files=["prismatic/gateway/templates/dashboard.html"],
        verification={"commands": ["python3 -m py_compile prismatic/gateway/server.py"]},
    )
    result = validate_packet(packet)
    assert not result.ok
    assert "dashboard-ui merge-ready" in errors_for(packet)


def test_dashboard_ui_packet_accepts_node_check_dashboard_proof():
    packet = valid_packet(
        merge_lane="dashboard-ui",
        changed_files=["prismatic/gateway/templates/dashboard.html"],
        verification={"commands": ["node --check /tmp/extracted-dashboard-inline.js"]},
    )
    result = validate_packet(packet)
    assert result.ok, result.errors


def test_mixed_high_risk_cannot_be_merge_ready():
    packet = valid_packet(merge_lane="mixed", risk_level="high")
    result = validate_packet(packet)
    assert not result.ok
    err = errors_for(packet)
    assert "high risk" in err
    assert "mixed/manual-review" in err
