from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismatic.handoff_contracts import (
    load_default_schema,
    validate_packet,
    validation_result,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "handoff-contract.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "handoff-contracts"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_fixture(packet: dict[str, Any]) -> list[str]:
    schema = load_json(SCHEMA_PATH)
    return validate_packet(packet, schema)


def test_valid_handoff_contract_fixture_passes() -> None:
    packet = load_json(FIXTURE_DIR / "pass.json")
    assert validate_fixture(packet) == []


@pytest.mark.parametrize(
    ("fixture", "expected_error"),
    [
        ("missing-result.json", "missing required result artifacts"),
        ("out-of-lane.json", "changed path outside allowed_paths"),
        ("production-proof-missing.json", "missing production_proof artifacts"),
        ("ambiguous-target-agent.json", "should be non-empty"),
    ],
)
def test_invalid_handoff_contract_fixtures_fail(
    fixture: str, expected_error: str
) -> None:
    packet = load_json(FIXTURE_DIR / fixture)
    errors = validate_fixture(packet)
    assert errors, fixture
    assert any(expected_error in error for error in errors), errors


def test_packaged_schema_matches_authoring_reference() -> None:
    assert load_default_schema() == load_json(SCHEMA_PATH)


@pytest.mark.parametrize(
    "packet",
    [
        {"work": [], "result": {}, "evidence": {}, "target": {"agent": "fred"}},
        {"work": {}, "result": "x", "evidence": {}, "target": {"agent": "fred"}},
        {
            "work": {},
            "result": {"status": "pass", "artifacts": [[]]},
            "evidence": {"required_artifacts": [[]]},
            "target": {"agent": "fred"},
        },
        {"work": {}, "result": {}, "evidence": {}, "target": []},
        {
            "work": {},
            "result": {},
            "evidence": {},
            "target": {"agent": "fred"},
            "source": [],
        },
    ],
)
def test_malformed_nested_values_fail_closed_without_crashing(packet: Any) -> None:
    errors = validate_fixture(packet)
    assert errors
    result = validation_result(packet, load_json(SCHEMA_PATH))
    assert result.ok is False
    assert result.status in {"blocked", "needs_manual_review"}
