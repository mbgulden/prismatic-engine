from __future__ import annotations

import copy
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "schemas" / "provider-neutral-verification-policy.schema.json"
RECEIPT_PATH = ROOT / "schemas" / "provider-neutral-verification-receipt.schema.json"
PACKAGE_POLICY_PATH = ROOT / "prismatic" / "schemas" / POLICY_PATH.name
PACKAGE_RECEIPT_PATH = ROOT / "prismatic" / "schemas" / RECEIPT_PATH.name
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
TIME = "2026-07-24T01:00:00Z"


def schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def errors(instance: Any, schema_path: Path) -> list[str]:
    return [
        error.message
        for error in Draft202012Validator(schema(schema_path)).iter_errors(instance)
    ]


def assert_valid(instance: dict[str, Any], schema_path: Path) -> None:
    assert errors(instance, schema_path) == []


def policy(provider: str = "local", backend_class: str = "local") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy",
        "policy_version": "1.0.0",
        "status": "active",
        "repository": {
            "repository_id": "prismatic-engine",
            "source_requirements": {
                "require_full_git_objects": True,
                "allowed_source_classes": [
                    "hosted",
                    "self_hosted",
                    "local",
                    "offline_bundle",
                ],
            },
        },
        "approved_backends": [
            {"id": "backend-1", "class": backend_class, "provider": provider}
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
        "required_proof_classes": ["unit", "build"],
        "non_claims": ["This policy does not authorize a merge."],
        "authorization_boundary": {"merge_authorization_external": True},
    }


def receipt(provider: str = "local") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy",
        "policy_version": "1.0.0",
        "task_id": "GRO-4205",
        "repository_id": "prismatic-engine",
        "source_provider": provider,
        "source_locator": "local://clean-checkout",
        "base_sha": SHA,
        "candidate_sha": "c" * 40,
        "tree_sha": "d" * 40,
        "changed_paths": ["schemas/provider-neutral-verification-receipt.schema.json"],
        "clean_checkout_id": "checkout-1",
        "source_acquisition_digest": DIGEST,
        "environment_digest": DIGEST,
        "commands_and_exit_states": [
            {
                "command_id": "focused-tests",
                "argv": ["python", "-m", "pytest", "-q"],
                "execution_state": "executed",
                "exit_state": "completed",
                "exit_code": 0,
                "started_at": TIME,
                "completed_at": "2026-07-24T01:01:00Z",
                "proof_class": "unit",
                "log_references": ["logs/focused-tests.log"],
            }
        ],
        "proof_classes": ["unit"],
        "logs_and_digests": [{"reference": "logs/focused-tests.log", "digest": DIGEST}],
        "artifacts_and_digests": [
            {"reference": "artifacts/report.json", "digest": DIGEST}
        ],
        "verifier_id": "verifier-1",
        "backend_id": "backend-1",
        "producer_id": "producer-1",
        "started_at": TIME,
        "completed_at": "2026-07-24T01:01:00Z",
        "expires_at": "2026-07-24T02:00:00Z",
        "supersedes": None,
        "revocation_status": "active",
        "decision": {"status": "pass", "merge_eligible": False},
        "non_claims": ["This receipt does not itself authorize a merge."],
        "signature_or_attestation": {
            "type": "attestation",
            "algorithm": "ed25519",
            "key_id": "verification-key-1",
            "value": "detached-attestation-placeholder",
        },
    }


def test_schemas_are_draft_2020_12_and_packaged_copies_are_byte_identical() -> None:
    for path in (POLICY_PATH, RECEIPT_PATH, PACKAGE_POLICY_PATH, PACKAGE_RECEIPT_PATH):
        Draft202012Validator.check_schema(schema(path))
    assert POLICY_PATH.read_bytes() == PACKAGE_POLICY_PATH.read_bytes()
    assert RECEIPT_PATH.read_bytes() == PACKAGE_RECEIPT_PATH.read_bytes()


def test_minimal_provider_neutral_policy_and_all_provider_identities_pass() -> None:
    assert_valid(policy(), POLICY_PATH)
    for provider, backend_class in [
        ("github", "hosted"),
        ("bitbucket", "hosted"),
        ("gitlab", "hosted"),
        ("forgejo", "self_hosted"),
        ("gitea", "self_hosted"),
        ("local", "local"),
        ("offline_bundle", "offline_bundle"),
    ]:
        candidate = policy(provider, backend_class)
        assert_valid(candidate, POLICY_PATH)
        assert "github" not in candidate["repository"]


def test_minimal_self_hosted_and_hosted_adapter_receipts_pass() -> None:
    assert_valid(receipt("local"), RECEIPT_PATH)
    hosted = receipt("gitlab")
    hosted["provider_metadata"] = {
        "pull_request_id": "123",
        "run_id": "run-456",
        "url": "https://gitlab.example/run/456",
    }
    assert_valid(hosted, RECEIPT_PATH)


@pytest.mark.parametrize(
    "schema_path, fixture", [(POLICY_PATH, policy()), (RECEIPT_PATH, receipt())]
)
def test_unknown_top_level_and_nested_properties_reject(
    schema_path: Path, fixture: dict[str, Any]
) -> None:
    extra = copy.deepcopy(fixture)
    extra["unexpected"] = True
    assert errors(extra, schema_path)
    nested = copy.deepcopy(fixture)
    if schema_path == POLICY_PATH:
        nested["commands"][0]["shell"] = "pytest -q"
    else:
        nested["commands_and_exit_states"][0]["shell"] = "pytest -q"
    assert errors(nested, schema_path)


@pytest.mark.parametrize(
    "field, value",
    [
        ("base_sha", "abc123"),
        ("candidate_sha", "z" * 40),
        ("tree_sha", "a" * 39),
        ("environment_digest", "b" * 64),
    ],
)
def test_abbreviated_or_malformed_git_objects_and_digests_reject(
    field: str, value: str
) -> None:
    candidate = receipt()
    candidate[field] = value
    assert errors(candidate, RECEIPT_PATH)


@pytest.mark.parametrize(
    "field",
    [
        "verifier_id",
        "backend_id",
        "revocation_status",
        "signature_or_attestation",
        "logs_and_digests",
        "artifacts_and_digests",
    ],
)
def test_required_receipt_evidence_and_identities_cannot_be_omitted(field: str) -> None:
    candidate = receipt()
    del candidate[field]
    assert errors(candidate, RECEIPT_PATH)
    if field in {"logs_and_digests", "artifacts_and_digests"}:
        assert "digest" in " ".join(errors(receipt(), RECEIPT_PATH)) or errors(
            candidate, RECEIPT_PATH
        )


def test_missing_command_exit_state_and_evidence_digest_reject() -> None:
    candidate = receipt()
    del candidate["commands_and_exit_states"][0]["exit_state"]
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt()
    del candidate["logs_and_digests"][0]["digest"]
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt()
    del candidate["artifacts_and_digests"][0]["digest"]
    assert errors(candidate, RECEIPT_PATH)


def test_argv_passes_and_shell_string_only_payload_rejects() -> None:
    assert_valid(policy(), POLICY_PATH)
    candidate = policy()
    candidate["commands"][0].pop("argv")
    candidate["commands"][0]["shell"] = "python -m pytest -q"
    assert errors(candidate, POLICY_PATH)
    candidate = receipt()
    candidate["commands_and_exit_states"][0].pop("argv")
    candidate["commands_and_exit_states"][0]["shell"] = "python -m pytest -q"
    assert errors(candidate, RECEIPT_PATH)


def test_nonexecution_and_blocked_or_revoked_receipts_are_not_merge_eligible() -> None:
    candidate = receipt()
    candidate["commands_and_exit_states"][0].update(
        {
            "execution_state": "backend_not_executed",
            "exit_state": "not_executed",
            "exit_code": None,
        }
    )
    candidate["decision"] = {"status": "blocked", "merge_eligible": False}
    assert_valid(candidate, RECEIPT_PATH)
    candidate["decision"] = {"status": "pass", "merge_eligible": True}
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt()
    candidate["revocation_status"] = "revoked"
    candidate["decision"] = {"status": "blocked", "merge_eligible": False}
    assert_valid(candidate, RECEIPT_PATH)


def test_provider_metadata_cannot_replace_required_core_evidence_or_identity() -> None:
    candidate = receipt("github")
    candidate["provider_metadata"] = {"pull_request_id": "42", "run_id": "abc"}
    del candidate["repository_id"]
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt("github")
    candidate["provider_metadata"] = {"pull_request_id": "42", "run_id": "abc"}
    del candidate["source_acquisition_digest"]
    assert errors(candidate, RECEIPT_PATH)


def test_runtime_semantic_boundaries_are_explicitly_not_claimed_by_schema() -> None:
    candidate = receipt()
    candidate["producer_id"] = candidate["verifier_id"]
    assert_valid(candidate, RECEIPT_PATH)
    description = schema(RECEIPT_PATH)["description"]
    assert "runtime validators" in description
    assert "producer/verifier separation" in description
    assert "freshness" in description
    assert "head/tree identity" in description
    assert "revocation" in description


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        "not-a-receipt",
        {"commands_and_exit_states": [[]]},
        {"changed_paths": [None]},
        {"logs_and_digests": ["bad"]},
    ],
)
def test_malformed_nested_object_list_and_scalar_values_fail_closed(
    malformed: Any,
) -> None:
    assert errors(malformed, RECEIPT_PATH)


def test_built_wheel_contains_packaged_schemas(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv, "uv is required to build the wheel fixture"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "prismatic/schemas/provider-neutral-verification-policy.schema.json" in names
    assert (
        "prismatic/schemas/provider-neutral-verification-receipt.schema.json" in names
    )
