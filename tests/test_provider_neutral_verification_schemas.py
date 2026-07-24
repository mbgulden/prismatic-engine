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
PACKAGE_POLICY_PATH = (
    ROOT / "prismatic" / "schemas" / "provider-neutral-verification-policy.schema.json"
)
PACKAGE_RECEIPT_PATH = (
    ROOT / "prismatic" / "schemas" / "provider-neutral-verification-receipt.schema.json"
)
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


def policy(
    backend_class: str = "self_hosted_clean_room",
    hosted_provider: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy",
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
                "class": backend_class,
                **(
                    {"hosted_provider_metadata": {"provider": hosted_provider}}
                    if hosted_provider
                    else {}
                ),
            }
        ],
        "approved_verifiers": {
            "identities": [
                {
                    "id": "verifier-1",
                    "key_id": "verification-key-1",
                    "algorithm": "ed25519",
                    "public_key_pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAonIAm5bXuYIKs/REfChIGowpzL9SabNGIL3/H2shVJs=\n-----END PUBLIC KEY-----\n",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
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


def receipt(
    source_kind: str = "local_bare_repository",
    source_provider: str = "none",
    backend_class: str = "self_hosted_clean_room",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "policy_id": "pnv-policy",
        "policy_version": "1.0.0",
        "task_id": "GRO-4205",
        "repository_id": "prismatic-engine",
        "source_kind": source_kind,
        "source_provider": source_provider,
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
        "backend_class": backend_class,
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
    assert POLICY_PATH != PACKAGE_POLICY_PATH
    assert RECEIPT_PATH != PACKAGE_RECEIPT_PATH
    assert POLICY_PATH.read_bytes() == PACKAGE_POLICY_PATH.read_bytes()
    assert RECEIPT_PATH.read_bytes() == PACKAGE_RECEIPT_PATH.read_bytes()


def test_legacy_string_verifier_identity_is_rejected_by_both_policy_schemas() -> None:
    candidate = policy()
    candidate["approved_verifiers"]["identities"] = ["verifier-1"]

    for path in (POLICY_PATH, PACKAGE_POLICY_PATH):
        schema_errors = errors(candidate, path)
        assert schema_errors
        assert any("is not of type 'object'" in message for message in schema_errors)


def test_source_acquisition_and_execution_backend_models_validate_independently() -> (
    None
):
    for backend_class in (
        "hosted_provider_runner",
        "self_hosted_clean_room",
        "supervised_clean_room",
    ):
        assert_valid(policy(backend_class), POLICY_PATH)
    assert_valid(policy("hosted_provider_runner", "github"), POLICY_PATH)


NORMATIVE_ACTIVE_POLICY_CONTROLS = (
    ("approved_verifiers", "require_producer_verifier_separation"),
    ("clean_room", "required"),
    ("clean_room", "source_acquisition_required"),
    ("evidence", "logs_required"),
    ("evidence", "artifacts_required"),
    ("environment", "environment_digest_required"),
    ("environment", "toolchain_digest_required"),
    ("freshness", "expiry_required"),
    ("freshness", "supersession_required"),
    ("freshness", "revocation_required"),
)


@pytest.mark.parametrize(("section", "field"), NORMATIVE_ACTIVE_POLICY_CONTROLS)
def test_active_policy_normative_controls_cannot_be_disabled(
    section: str, field: str
) -> None:
    candidate = policy()
    candidate[section][field] = False
    assert errors(candidate, POLICY_PATH)


def test_active_policy_authorization_boundary_is_required_and_external() -> None:
    candidate = policy()
    del candidate["authorization_boundary"]
    assert errors(candidate, POLICY_PATH)
    candidate = policy()
    candidate["authorization_boundary"]["merge_authorization_external"] = False
    assert errors(candidate, POLICY_PATH)


@pytest.mark.parametrize("status", ("suspended", "revoked", "draft", "deprecated"))
def test_status_cannot_create_a_normative_control_loophole(status: str) -> None:
    candidate = policy()
    candidate["status"] = status
    candidate["clean_room"]["required"] = False
    assert "True was expected" in errors(candidate, POLICY_PATH)


def test_normative_policy_controls_are_global_const_true_invariants() -> None:
    document = schema(POLICY_PATH)
    for section, field in NORMATIVE_ACTIVE_POLICY_CONTROLS:
        assert document["properties"][section]["properties"][field] == {"const": True}


@pytest.mark.parametrize(
    ("source_kind", "source_provider"),
    [
        ("provider_remote", "github"),
        ("local_bare_repository", "none"),
        ("offline_git_bundle", "none"),
    ],
)
def test_all_source_kinds_validate_only_with_correct_provider_combinations(
    source_kind: str, source_provider: str
) -> None:
    assert_valid(receipt(source_kind, source_provider), RECEIPT_PATH)


def test_source_provider_conditionals_reject_invalid_combinations() -> None:
    assert errors(receipt("provider_remote", "none"), RECEIPT_PATH)
    assert errors(receipt("local_bare_repository", "github"), RECEIPT_PATH)
    assert errors(receipt("offline_git_bundle", "gitlab"), RECEIPT_PATH)


def test_source_adapter_names_reject_as_execution_backend_classes() -> None:
    for backend_class in (
        "local",
        "offline_bundle",
        "local_bare_repository",
        "offline_git_bundle",
    ):
        assert errors(policy(backend_class), POLICY_PATH)
        assert errors(receipt(backend_class=backend_class), RECEIPT_PATH)


def test_minimal_self_hosted_and_offline_receipts_pass() -> None:
    assert_valid(
        receipt("local_bare_repository", "none", "self_hosted_clean_room"), RECEIPT_PATH
    )
    assert_valid(
        receipt("offline_git_bundle", "none", "supervised_clean_room"), RECEIPT_PATH
    )
    hosted = receipt("provider_remote", "gitlab", "hosted_provider_runner")
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
        "backend_class",
        "source_kind",
        "source_provider",
        "source_locator",
        "source_acquisition_digest",
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


@pytest.mark.parametrize(
    ("exit_state", "exit_code"),
    [
        ("completed", 1),
        ("timed_out", None),
        ("cancelled", None),
        ("failed", 1),
        ("not_started", None),
    ],
)
def test_unsuccessful_executed_commands_cannot_pass_or_be_merge_eligible(
    exit_state: str, exit_code: int | None
) -> None:
    candidate = receipt()
    candidate["commands_and_exit_states"][0].update(
        {"exit_state": exit_state, "exit_code": exit_code}
    )
    candidate["decision"] = {"status": "pass", "merge_eligible": True}
    assert errors(candidate, RECEIPT_PATH)
    candidate["decision"] = {"status": "blocked", "merge_eligible": False}
    assert_valid(candidate, RECEIPT_PATH)


def test_completed_command_requires_an_integer_exit_code() -> None:
    candidate = receipt()
    candidate["commands_and_exit_states"][0]["exit_code"] = None
    assert errors(candidate, RECEIPT_PATH)


def test_unknown_revocation_is_blocked_and_non_merge_eligible() -> None:
    candidate = receipt()
    candidate["revocation_status"] = "unknown"
    candidate["decision"] = {"status": "pass", "merge_eligible": True}
    assert errors(candidate, RECEIPT_PATH)
    candidate["decision"] = {"status": "blocked", "merge_eligible": False}
    assert_valid(candidate, RECEIPT_PATH)


def test_empty_changed_paths_reject() -> None:
    candidate = receipt()
    candidate["changed_paths"] = []
    assert errors(candidate, RECEIPT_PATH)


def test_active_successful_receipt_may_pass_and_be_merge_eligible() -> None:
    candidate = receipt()
    candidate["decision"] = {"status": "pass", "merge_eligible": True}
    assert_valid(candidate, RECEIPT_PATH)


@pytest.mark.parametrize(
    ("kinds", "providers"),
    [
        (["provider_remote"], ["none"]),
        (["local_bare_repository"], ["github"]),
        (["offline_git_bundle"], ["github"]),
        (["provider_remote"], ["github", "none"]),
        (["local_bare_repository"], ["github", "none"]),
    ],
)
def test_unusable_policy_source_allowlists_reject(
    kinds: list[str], providers: list[str]
) -> None:
    candidate = policy()
    requirements = candidate["repository"]["source_requirements"]
    requirements["allowed_source_kinds"] = kinds
    requirements["allowed_source_providers"] = providers
    assert errors(candidate, POLICY_PATH)


def test_coherent_mixed_remote_and_local_policy_source_allowlist_validates() -> None:
    candidate = policy()
    requirements = candidate["repository"]["source_requirements"]
    requirements["allowed_source_kinds"] = [
        "provider_remote",
        "local_bare_repository",
        "offline_git_bundle",
    ]
    requirements["allowed_source_providers"] = ["github", "none"]
    assert_valid(candidate, POLICY_PATH)


def test_provider_metadata_cannot_replace_required_core_evidence_or_identity() -> None:
    candidate = receipt("provider_remote", "github", "hosted_provider_runner")
    candidate["provider_metadata"] = {"pull_request_id": "42", "run_id": "abc"}
    del candidate["repository_id"]
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt("provider_remote", "github", "hosted_provider_runner")
    candidate["provider_metadata"] = {"pull_request_id": "42", "run_id": "abc"}
    del candidate["source_acquisition_digest"]
    assert errors(candidate, RECEIPT_PATH)


def test_source_and_backend_dimensions_cannot_substitute_for_each_other() -> None:
    candidate = receipt()
    candidate["backend_id"] = "local_bare_repository"
    candidate["backend_class"] = "local_bare_repository"
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt()
    candidate["source_kind"] = "backend-1"
    candidate["source_provider"] = "none"
    assert errors(candidate, RECEIPT_PATH)
    candidate = receipt()
    candidate["provider_metadata"] = {"run_id": "source-adapter-run"}
    candidate["backend_class"] = "offline_bundle"
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
