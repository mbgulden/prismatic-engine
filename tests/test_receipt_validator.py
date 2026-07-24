"""Tests for independent receipt validator (GRO-4208)."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


from prismatic.verification.receipt_validator import (
    check_revocation,
    determine_merge_eligibility,
    validate_receipt_freshness,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
DIGEST_VALID = "sha256:" + "d" * 64
DIGEST_ZERO = "sha256:" + "0" * 64


def _now_str(offset_seconds: float = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_policy() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy-1",
        "policy_version": "1.0.0",
        "status": "active",
        "repository": {
            "repository_id": "prismatic-engine",
            "source_requirements": {
                "require_full_git_objects": True,
                "allowed_source_kinds": [
                    "provider_remote",
                    "local_bare_repository",
                    "offline_git_bundle",
                ],
                "allowed_source_providers": [
                    "github",
                    "gitlab",
                    "bitbucket",
                    "forgejo",
                    "gitea",
                    "other",
                    "none",
                ],
            },
        },
        "approved_backends": [
            {
                "id": "backend-1",
                "class": "self_hosted_clean_room",
            }
        ],
        "approved_verifiers": {
            "identities": ["verifier-1"],
            "require_producer_verifier_separation": True,
        },
        "clean_room": {
            "required": True,
            "source_acquisition_required": True,
            "network_isolation_required": True,
        },
        "bindings": {
            "require_base_sha": True,
            "require_candidate_sha": True,
            "require_tree_sha": True,
            "require_changed_paths": True,
        },
        "commands": [
            {
                "id": "focused-tests",
                "argv": ["python", "-m", "pytest", "-q"],
                "proof_class": "unit",
                "timeout_seconds": 600,
                "required": True,
            }
        ],
        "evidence": {
            "logs_required": True,
            "artifacts_required": True,
            "digest_requirements": [
                {"kind": "log", "algorithm": "sha256", "required": True},
                {"kind": "artifact", "algorithm": "sha256", "required": True},
            ],
        },
        "environment": {
            "environment_digest_required": True,
            "toolchain_digest_required": True,
            "digest_algorithm": "sha256",
        },
        "freshness": {
            "max_age_seconds": 3600,
            "expiry_required": True,
            "supersession_required": True,
            "revocation_required": True,
        },
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "required_proof_classes": ["unit"],
        "non_claims": ["This policy does not authorize a merge."],
        "authorization_boundary": {"merge_authorization_external": True},
    }


def valid_receipt(finished_offset: float = -10) -> dict[str, Any]:
    started_time = _now_str(finished_offset - 30)
    completed_time = _now_str(finished_offset)
    expires_time = _now_str(finished_offset + 3600)

    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy-1",
        "policy_version": "1.0.0",
        "task_id": "GRO-4208",
        "repository_id": "prismatic-engine",
        "source_kind": "local_bare_repository",
        "source_provider": "none",
        "source_locator": "local://clean-checkout",
        "base_sha": SHA_A,
        "candidate_sha": SHA_B,
        "tree_sha": SHA_C,
        "changed_paths": ["prismatic/verification/receipt_validator.py"],
        "clean_checkout_id": "checkout-4208",
        "source_acquisition_digest": DIGEST_VALID,
        "environment_digest": DIGEST_VALID,
        "commands_and_exit_states": [
            {
                "command_id": "focused-tests",
                "argv": ["python", "-m", "pytest", "-q"],
                "execution_state": "executed",
                "exit_state": "completed",
                "exit_code": 0,
                "started_at": started_time,
                "completed_at": completed_time,
                "proof_class": "unit",
                "log_references": ["logs/focused-tests.log"],
            }
        ],
        "proof_classes": ["unit"],
        "logs_and_digests": [
            {"reference": "logs/focused-tests.log", "digest": DIGEST_VALID}
        ],
        "artifacts_and_digests": [
            {"reference": "artifacts/report.json", "digest": DIGEST_VALID}
        ],
        "verifier_id": "verifier-1",
        "backend_id": "backend-1",
        "backend_class": "self_hosted_clean_room",
        "producer_id": "producer-1",
        "started_at": started_time,
        "completed_at": completed_time,
        "expires_at": expires_time,
        "supersedes": None,
        "revocation_status": "active",
        "decision": {"status": "pass", "merge_eligible": True},
        "non_claims": ["This receipt does not itself authorize a merge."],
        "signature_or_attestation": {
            "type": "attestation",
            "algorithm": "ed25519",
            "key_id": "verification-key-1",
            "value": "detached-attestation-placeholder",
        },
    }


# Test 1: Fresh receipt within window -> fresh
def test_fresh_receipt_within_window() -> None:
    receipt = valid_receipt(finished_offset=-10)
    is_fresh, reason = validate_receipt_freshness(receipt, max_age_seconds=3600)
    assert is_fresh is True
    assert reason is None


# Test 2: Expired receipt -> stale with reason
def test_expired_receipt() -> None:
    receipt = valid_receipt(finished_offset=-4000)
    is_fresh, reason = validate_receipt_freshness(receipt, max_age_seconds=3600)
    assert is_fresh is False
    assert reason == "receipt_stale"


# Test 3: Future timestamp (far future) -> stale
def test_far_future_timestamp() -> None:
    receipt = valid_receipt(finished_offset=300)
    is_fresh, reason = validate_receipt_freshness(receipt, max_age_seconds=3600)
    assert is_fresh is False
    assert reason == "timestamp_future"


# Test 4: Future timestamp within clock skew (<=60s) -> fresh
def test_future_timestamp_within_clock_skew() -> None:
    receipt = valid_receipt(finished_offset=30)
    is_fresh, reason = validate_receipt_freshness(receipt, max_age_seconds=3600)
    assert is_fresh is True
    assert reason is None


# Test 5: Missing timestamp -> stale
def test_missing_timestamp() -> None:
    receipt = valid_receipt()
    del receipt["completed_at"]
    is_fresh, reason = validate_receipt_freshness(receipt, max_age_seconds=3600)
    assert is_fresh is False
    assert reason == "missing_timestamp"


# Test 6: Revoked receipt (by ID) -> revoked
def test_revoked_receipt_by_id(tmp_path: Path) -> None:
    store = tmp_path / "revocation.json"
    store.write_text(json.dumps(["GRO-4208"]), encoding="utf-8")
    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=store)
    assert not_revoked is False
    assert reason == "receipt_revoked_by_id_GRO-4208"


# Test 7: Revoked receipt (by SHA) -> revoked
def test_revoked_receipt_by_sha(tmp_path: Path) -> None:
    store = tmp_path / "revocation.json"
    store.write_text(json.dumps([SHA_B]), encoding="utf-8")
    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=store)
    assert not_revoked is False
    assert reason == f"receipt_revoked_by_sha_{SHA_B}"


# Test 8: Non-revoked receipt -> not revoked
def test_non_revoked_receipt(tmp_path: Path) -> None:
    store = tmp_path / "revocation.json"
    store.write_text(json.dumps(["unrelated-id"]), encoding="utf-8")
    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=store)
    assert not_revoked is True
    assert reason is None


# Test 9: No revocation store -> not revoked
def test_no_revocation_store() -> None:
    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=None)
    assert not_revoked is True
    assert reason is None


# Test 10: Merge eligible with complete evidence -> eligible
def test_merge_eligible_complete_evidence() -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is True
    assert reason is None


# Test 11: Missing candidate_sha -> not eligible
def test_missing_candidate_sha() -> None:
    receipt = valid_receipt()
    del receipt["candidate_sha"]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None


# Test 12: Non-zero exit code -> not eligible
def test_non_zero_exit_code() -> None:
    receipt = valid_receipt()
    receipt["commands_and_exit_states"][0]["exit_code"] = 1
    receipt["decision"] = {"status": "fail", "merge_eligible": False}
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None


# Test 13: Missing required proof_class -> not eligible
def test_missing_required_proof_class() -> None:
    receipt = valid_receipt()
    receipt["proof_classes"] = ["unit"]
    policy = valid_policy()
    policy["required_proof_classes"] = ["unit", "security"]
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "missing_required_proof_class: security" in (reason or "")


# Test 14: Full integration: fresh + not revoked + complete -> eligible
def test_full_integration_eligible(tmp_path: Path) -> None:
    store = tmp_path / "revocation.json"
    store.write_text(json.dumps(["some-other-task"]), encoding="utf-8")
    receipt = valid_receipt(finished_offset=-10)
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, revocation_store=store
    )
    assert eligible is True
    assert reason is None


# Test 15: Integration: fresh + not revoked + missing field -> not eligible
def test_integration_missing_field(tmp_path: Path) -> None:
    store = tmp_path / "revocation.json"
    store.write_text(json.dumps(["some-other-task"]), encoding="utf-8")
    receipt = valid_receipt(finished_offset=-10)
    del receipt["tree_sha"]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, revocation_store=store
    )
    assert eligible is False
    assert reason is not None


# Test 16: Schema validation: non-dict receipt -> fail closed
def test_schema_validation_non_dict() -> None:
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility("not a dict", policy)  # type: ignore[arg-type]
    assert eligible is False
    assert "non-dict receipt" in (reason or "")


# Test 17: Schema validation: missing required top-level keys -> fail closed
def test_schema_validation_missing_required_keys() -> None:
    receipt = valid_receipt()
    del receipt["policy_id"]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "schema_validation_failed" in (reason or "") or "missing" in (reason or "")


# Test 18: Malformed SHA (wrong length) -> not eligible
def test_malformed_sha() -> None:
    receipt = valid_receipt()
    receipt["candidate_sha"] = "invalid_sha_short"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None


# Test 19: Empty changed_paths -> handled by policy
def test_empty_changed_paths_handled_by_policy() -> None:
    receipt = valid_receipt()
    receipt["changed_paths"] = []
    policy = valid_policy()
    policy["bindings"]["require_changed_paths"] = True
    policy["bindings"]["allow_empty_changed_paths"] = False

    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None

    # When policy allows empty changed paths
    policy["bindings"]["allow_empty_changed_paths"] = True
    eligible_allowed, reason_allowed = determine_merge_eligibility(receipt, policy)
    assert eligible_allowed is True
    assert reason_allowed is None


# Test 20: Evidence digest mismatch -> not eligible
def test_evidence_digest_mismatch(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "focused-tests.log"
    log_file.write_text("actual log content", encoding="utf-8")

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    art_file = art_dir / "report.json"
    art_file.write_text("art content", encoding="utf-8")

    correct_art_digest = "sha256:" + hashlib.sha256(b"art content").hexdigest()
    wrong_log_digest = "sha256:" + hashlib.sha256(b"wrong content").hexdigest()

    receipt = valid_receipt()
    receipt["logs_and_digests"] = [
        {"reference": "logs/focused-tests.log", "digest": wrong_log_digest}
    ]
    receipt["artifacts_and_digests"] = [
        {"reference": "artifacts/report.json", "digest": correct_art_digest}
    ]
    policy = valid_policy()

    eligible, reason = determine_merge_eligibility(
        receipt, policy, evidence_base_path=tmp_path
    )
    assert eligible is False
    assert reason == "evidence_digest_mismatch"


# =====================================================================
# ADVERSARIAL REGRESSION TESTS (GRO-4208 REPAIR 1)
# =====================================================================


def test_adversarial_decision_fail_and_not_merge_eligible() -> None:
    receipt = valid_receipt()
    receipt["decision"] = {"status": "fail", "merge_eligible": False}
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None


def test_adversarial_policy_status_revoked() -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    policy["status"] = "revoked"
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "policy_status_revoked"


def test_adversarial_unapproved_backend_id() -> None:
    receipt = valid_receipt()
    receipt["backend_id"] = "unapproved-backend-999"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "unapproved_backend"


def test_adversarial_unapproved_backend_class() -> None:
    receipt = valid_receipt()
    receipt["backend_class"] = "hosted_provider_runner"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "unapproved_backend"


def test_adversarial_unapproved_verifier_identity() -> None:
    receipt = valid_receipt()
    receipt["verifier_id"] = "rogue-verifier"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "unapproved_verifier_identity"


def test_adversarial_policy_id_mismatch() -> None:
    receipt = valid_receipt()
    receipt["policy_id"] = "mismatched-policy-id"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "policy_id_mismatch"


def test_adversarial_repository_id_mismatch() -> None:
    receipt = valid_receipt()
    receipt["repository_id"] = "mismatched-repo-id"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "repository_id_mismatch"


def test_adversarial_already_expired_receipt() -> None:
    receipt = valid_receipt()
    receipt["expires_at"] = _now_str(-10)  # already expired 10s ago
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "receipt_expired" in (reason or "") or "freshness_failed" in (reason or "")


def test_adversarial_missing_evidence_files_when_base_path_supplied(
    tmp_path: Path,
) -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, evidence_base_path=tmp_path
    )
    assert eligible is False
    assert reason == "missing_evidence_file"


def test_adversarial_evidence_path_traversal_relative(tmp_path: Path) -> None:
    receipt = valid_receipt()
    receipt["logs_and_digests"] = [
        {"reference": "../outside_log.log", "digest": DIGEST_VALID}
    ]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, evidence_base_path=tmp_path
    )
    assert eligible is False
    assert reason == "unsafe_evidence_reference_traversal"


def test_adversarial_evidence_path_absolute(tmp_path: Path) -> None:
    receipt = valid_receipt()
    receipt["logs_and_digests"] = [{"reference": "/etc/passwd", "digest": DIGEST_VALID}]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, evidence_base_path=tmp_path
    )
    assert eligible is False
    assert reason == "unsafe_evidence_reference_absolute"


def test_adversarial_evidence_symlink_rejection(tmp_path: Path) -> None:
    target = tmp_path / "real_file.log"
    target.write_text("content", encoding="utf-8")
    symlink_file = tmp_path / "sym_link.log"
    symlink_file.symlink_to(target)

    receipt = valid_receipt()
    receipt["logs_and_digests"] = [
        {
            "reference": "sym_link.log",
            "digest": "sha256:" + hashlib.sha256(b"content").hexdigest(),
        }
    ]
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(
        receipt, policy, evidence_base_path=tmp_path
    )
    assert eligible is False
    assert reason == "unsafe_evidence_reference_symlink"


def test_adversarial_provided_but_missing_revocation_store(
    tmp_path: Path,
) -> None:
    non_existent = tmp_path / "missing_revocation_store.json"
    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=non_existent)
    assert not_revoked is False
    assert reason == "revocation_store_missing"

    policy = valid_policy()
    eligible, me_reason = determine_merge_eligibility(
        receipt, policy, revocation_store=non_existent
    )
    assert eligible is False
    assert me_reason == "revocation_store_missing"


def test_adversarial_revocation_store_symlink_rejection(
    tmp_path: Path,
) -> None:
    real_store = tmp_path / "real_revocation.json"
    real_store.write_text("[]", encoding="utf-8")
    sym_store = tmp_path / "sym_revocation.json"
    sym_store.symlink_to(real_store)

    receipt = valid_receipt()
    not_revoked, reason = check_revocation(receipt, revocation_store=sym_store)
    assert not_revoked is False
    assert reason == "revocation_store_is_symlink"


def test_adversarial_malformed_max_age_seconds_does_not_raise() -> None:
    receipt = valid_receipt()
    # Test string, bool, float, negative, None max_age_seconds
    for bad_max_age in ("3600", True, False, 3600.0, -10, None):
        is_fresh, reason = validate_receipt_freshness(
            receipt,
            max_age_seconds=bad_max_age,  # type: ignore[arg-type]
        )
        assert is_fresh is False
        assert reason == "invalid_max_age_seconds"

        policy = valid_policy()
        policy["freshness"]["max_age_seconds"] = bad_max_age
        eligible, me_reason = determine_merge_eligibility(receipt, policy)
        assert eligible is False
        assert me_reason is not None


def test_adversarial_duplicate_command_ids() -> None:
    receipt = valid_receipt()
    policy = valid_policy()

    # Duplicate policy command IDs
    policy["commands"].append(copy.deepcopy(policy["commands"][0]))
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "duplicate_policy_command_id" in (reason or "")

    # Duplicate receipt command IDs
    policy = valid_policy()
    receipt["commands_and_exit_states"].append(
        copy.deepcopy(receipt["commands_and_exit_states"][0])
    )
    eligible2, reason2 = determine_merge_eligibility(receipt, policy)
    assert eligible2 is False
    assert "duplicate_receipt_command_id" in (reason2 or "") or "schema_validation" in (
        reason2 or ""
    )


def test_adversarial_command_argv_mismatch() -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    receipt["commands_and_exit_states"][0]["argv"] = [
        "python",
        "-m",
        "pytest",
        "-v",
    ]
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "command_argv_mismatch" in (reason or "")


def test_adversarial_command_proof_class_mismatch() -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    receipt["commands_and_exit_states"][0]["proof_class"] = "integration"
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "command_proof_class_mismatch" in (reason or "")


def test_adversarial_producer_verifier_same_identity() -> None:
    receipt = valid_receipt()
    receipt["producer_id"] = "verifier-1"
    receipt["verifier_id"] = "verifier-1"
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason == "producer_verifier_separation_failed"


def test_adversarial_unknown_policy_binding_overlay_key() -> None:
    receipt = valid_receipt()
    policy = valid_policy()
    policy["bindings"]["unauthorized_overlay_key"] = "hacked"
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert "unknown policy binding overlay key" in (reason or "")


def test_adversarial_singular_proof_class_fallback_removed() -> None:
    receipt = valid_receipt()
    del receipt["proof_classes"]
    receipt["proof_class"] = "unit"  # Old singular fallback
    policy = valid_policy()
    eligible, reason = determine_merge_eligibility(receipt, policy)
    assert eligible is False
    assert reason is not None
