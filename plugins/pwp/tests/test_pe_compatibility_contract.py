"""Fail-closed validation for the PWP–PE compatibility boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "pwp-pe-compatibility-contract.schema.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pwp_pe_compatibility"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_current_pwp_pe_contract_fixture_is_valid() -> None:
    errors = sorted(_validator().iter_errors(_load(FIXTURES / "valid-contract.json")), key=str)
    assert errors == []


def test_unknown_capability_is_rejected_fail_closed() -> None:
    errors = list(_validator().iter_errors(_load(FIXTURES / "reject-unknown-capability.json")))
    assert errors
    assert any(error.path[-1] == "id" for error in errors if error.path)


@pytest.mark.parametrize("removed_field", ["plugin", "disconnect_semantics"])
def test_missing_required_sections_are_rejected(removed_field: str) -> None:
    contract = _load(FIXTURES / "valid-contract.json")
    contract.pop(removed_field)
    assert list(_validator().iter_errors(contract))
