"""PWP module contract loading and validation.

The validator intentionally supports the JSON Schema subset used by the PWP
module contracts so the contract library has no runtime dependency on
``jsonschema``. Full schema validation can be layered on later; this gate keeps
agent-authored manifests honest in the base engine checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
MODULES_ROOT = PACKAGE_ROOT / "modules"


class ModuleContractError(ValueError):
    """Raised when a PWP module contract or fixture is invalid."""


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _assert_type(value: Any, expected: str, path: str) -> None:
    actual = _type_name(value)
    if expected == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return
    if expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return
    if actual != expected:
        raise ModuleContractError(f"{path}: expected {expected}, got {actual}")


def validate_schema_subset(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type:
        _assert_type(value, expected_type, path)

    if "enum" in schema and value not in schema["enum"]:
        raise ModuleContractError(f"{path}: {value!r} is not in enum {schema['enum']!r}")

    if isinstance(value, str):
        if schema.get("minLength") and len(value) < schema["minLength"]:
            raise ModuleContractError(f"{path}: string shorter than {schema['minLength']}")
        if pattern := schema.get("pattern"):
            if not re.match(pattern, value):
                raise ModuleContractError(f"{path}: {value!r} does not match {pattern!r}")

    if isinstance(value, list):
        if schema.get("minItems") and len(value) < schema["minItems"]:
            raise ModuleContractError(f"{path}: array shorter than {schema['minItems']}")
        if schema.get("uniqueItems") and len(value) != len({json.dumps(item, sort_keys=True) for item in value}):
            raise ModuleContractError(f"{path}: duplicate array items")
        if item_schema := schema.get("items"):
            for index, item in enumerate(value):
                validate_schema_subset(item, item_schema, f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ModuleContractError(f"{path}: missing required key {key!r}")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}))
            extras = sorted(set(value) - allowed)
            if extras:
                raise ModuleContractError(f"{path}: unexpected keys {extras}")
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                validate_schema_subset(value[key], child_schema, f"{path}.{key}")


def pwp_module_schema() -> dict[str, Any]:
    return json.loads((PACKAGE_ROOT / "schemas" / "pwp-module.schema.json").read_text())


def validate_module_contract(contract: dict[str, Any]) -> None:
    validate_schema_subset(contract, pwp_module_schema())
    props_schema = contract["propsSchema"]
    if props_schema.get("type") != "object":
        raise ModuleContractError(f"{contract['id']}: propsSchema must describe an object")
    declared_props = set(props_schema.get("properties", {}))
    required_props = set(props_schema.get("required", []))
    missing_required_defs = sorted(required_props - declared_props)
    if missing_required_defs:
        raise ModuleContractError(f"{contract['id']}: required props missing definitions {missing_required_defs}")
    editable = set(contract["editableFields"])
    missing_editable_defs = sorted(editable - declared_props)
    if missing_editable_defs:
        raise ModuleContractError(f"{contract['id']}: editable fields missing prop definitions {missing_editable_defs}")
    variants = set(contract["variants"])
    for fixture in contract["fixtures"]:
        if fixture["variant"] not in variants:
            raise ModuleContractError(f"{contract['id']}: fixture {fixture['name']} uses unknown variant {fixture['variant']}")
        validate_schema_subset(fixture["props"], props_schema, f"{contract['id']}.{fixture['name']}.props")


def load_module_contracts(root: Path | None = None) -> list[dict[str, Any]]:
    modules_root = root or MODULES_ROOT
    contracts = []
    for path in sorted(modules_root.glob("*.json")):
        contract = json.loads(path.read_text())
        validate_module_contract(contract)
        contracts.append(contract)
    return contracts
