from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "handoff-contract.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "handoff-contracts"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def path_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(path == allowed or path.startswith(allowed.rstrip("/") + "/") for allowed in allowed_paths)


def semantic_errors(packet: dict[str, Any]) -> list[str]:
    """Focused review-contract checks that are clearer outside JSON Schema."""
    errors: list[str] = []
    work = packet.get("work", {})
    result = packet.get("result", {})
    evidence = packet.get("evidence", {})

    required_artifacts = set(evidence.get("required_artifacts", []))
    actual_artifacts = set(result.get("artifacts", []))
    if result.get("status") == "pass" and not required_artifacts.issubset(actual_artifacts):
        missing = sorted(required_artifacts - actual_artifacts)
        errors.append(f"missing required result artifacts: {missing}")

    allowed_paths = work.get("allowed_paths", [])
    for changed_path in result.get("changed_paths", []):
        if not path_allowed(changed_path, allowed_paths):
            errors.append(f"changed path outside allowed_paths: {changed_path}")

    claims = {claim.lower() for claim in result.get("claims", [])}
    production_facing = bool(work.get("production_facing")) or any("production" in claim for claim in claims)
    production_proof = evidence.get("production_proof") or {}
    proof_required = bool(production_proof.get("required")) or production_facing
    if proof_required:
        proof_artifacts = production_proof.get("artifacts", [])
        if not proof_artifacts:
            errors.append("production-facing handoff is missing production_proof artifacts")

    return errors


def validate_packet(packet: dict[str, Any]) -> list[str]:
    schema = load_json(SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for error in sorted(validator.iter_errors(packet), key=str)]
    errors.extend(semantic_errors(packet))
    return errors


def test_valid_handoff_contract_fixture_passes() -> None:
    packet = load_json(FIXTURE_DIR / "pass.json")
    assert validate_packet(packet) == []


@pytest.mark.parametrize(
    ("fixture", "expected_error"),
    [
        ("missing-result.json", "missing required result artifacts"),
        ("out-of-lane.json", "changed path outside allowed_paths"),
        ("production-proof-missing.json", "missing production_proof artifacts"),
        ("ambiguous-target-agent.json", "'' should be non-empty"),
    ],
)
def test_invalid_handoff_contract_fixtures_fail(fixture: str, expected_error: str) -> None:
    packet = load_json(FIXTURE_DIR / fixture)
    errors = validate_packet(packet)
    assert errors, fixture
    assert any(expected_error in error for error in errors), errors
