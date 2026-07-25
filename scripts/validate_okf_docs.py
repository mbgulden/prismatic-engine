#!/usr/bin/env python3
"""Fail-closed validation for Prismatic canonical docs and OKF registry."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = [
    "docs/index.md",
    "docs/governance/documentation-policy.md",
    "docs/governance/source-of-truth-order.md",
    "docs/governance/glossary.md",
    "docs/north-star.md",
    "docs/okf-evidence-map.md",
    "docs/architecture/verification-engine.md",
    "docs/contracts/verification-contract.md",
    "docs/contracts/canonical-agy-cli-workflow.md",
    "docs/contracts/evidence-retention.md",
    "docs/decisions/index.md",
    "docs/decisions/ADR-0001-documentation-source-of-truth.md",
    "docs/decisions/ADR-0002-provider-neutral-verification-receipts.md",
    "docs/decisions/ADR-0003-canonical-agy-cli-workflow.md",
    "docs/research/verifiers-are-king-evidence-review.md",
    "docs/research/agentic-swarm-ops-agy-workflow-deep-dive.md",
    "okf/index.yaml",
    "okf/schemas/okf.schema.json",
]
REQUIRED_OBJECTIVE_FIELDS = {
    "id",
    "objective",
    "owner",
    "system_of_record",
    "key_results",
    "functions",
    "evidence",
    "current_surface",
    "review_after",
}


def _schema_errors(registry: Any, schema: Any) -> list[str]:
    """Validate the registry against its canonical Draft 2020-12 schema."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"invalid canonical OKF schema: {exc.message}"]
    validator = Draft202012Validator(schema)
    return [
        f"OKF schema violation at {error.json_path}: {error.message}"
        for error in sorted(
            validator.iter_errors(registry), key=lambda item: list(item.path)
        )
    ]


def _parity_errors(registry: object, evidence_map: str) -> list[str]:
    """Bind human objective/system-of-record rows to the machine registry."""
    errors: list[str] = []
    block = re.search(
        r"<!-- OKF_REGISTRY_PARITY_BEGIN -->(.*?)<!-- OKF_REGISTRY_PARITY_END -->",
        evidence_map,
        re.DOTALL,
    )
    if not block:
        return ["missing canonical OKF registry parity block"]
    rows = re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|$", block.group(1), re.MULTILINE
    )
    parity: dict[str, str] = {}
    for objective_id, system_of_record in rows:
        if objective_id in parity:
            errors.append(f"duplicate OKF parity objective: {objective_id}")
        parity[objective_id] = system_of_record
    if not isinstance(registry, dict):
        return errors + ["OKF registry must be an object for parity validation"]
    expected: dict[str, str] = {}
    for objective in registry.get("objectives", []):
        if not isinstance(objective, dict):
            continue
        objective_id = objective.get("id")
        system_of_record = objective.get("system_of_record")
        if isinstance(objective_id, str) and isinstance(system_of_record, str):
            expected[objective_id] = system_of_record
    if set(parity) != set(expected):
        errors.append(
            "OKF parity objective IDs differ: "
            f"missing={sorted(set(expected) - set(parity))} "
            f"extra={sorted(set(parity) - set(expected))}"
        )
    for objective_id in sorted(set(parity) & set(expected)):
        if parity[objective_id] != expected[objective_id]:
            errors.append(f"{objective_id}: parity system_of_record mismatch")
    return errors


def validate() -> list[str]:
    """Return every documentation governance violation."""
    errors: list[str] = []
    for relative_path in CANONICAL:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing canonical path: {relative_path}")
    if errors:
        return errors

    try:
        registry = json.loads((ROOT / "okf/index.yaml").read_text())
        schema = json.loads((ROOT / "okf/schemas/okf.schema.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot parse canonical OKF registry/schema: {exc}"]
    errors.extend(_schema_errors(registry, schema))
    evidence_map = (ROOT / "docs/okf-evidence-map.md").read_text(errors="replace")
    errors.extend(_parity_errors(registry, evidence_map))
    if not isinstance(registry, dict):
        return errors
    if registry.get("governing_principle") != "Don't trust, Verify":
        errors.append("wrong governing principle")

    objective_ids: set[str] = set()
    for objective in registry.get("objectives", []):
        missing = REQUIRED_OBJECTIVE_FIELDS - set(objective)
        if missing:
            errors.append(f"{objective.get('id', '?')}: missing {sorted(missing)}")
        objective_id = objective.get("id")
        if objective_id in objective_ids:
            errors.append(f"duplicate objective: {objective_id}")
        objective_ids.add(objective_id)

        for key in ("functions", "evidence"):
            for reference in objective.get(key, []):
                if reference.startswith(("http://", "https://", "/api/")):
                    continue
                if not (ROOT / reference).exists():
                    errors.append(f"{objective_id}: missing {key} path {reference}")
        try:
            if date.fromisoformat(objective["review_after"]) < date.today():
                errors.append(f"{objective_id}: review overdue")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{objective_id}: invalid review_after")

    for relative_path in CANONICAL:
        path = ROOT / relative_path
        if path.suffix != ".md":
            continue
        text = path.read_text(errors="replace")
        if "/home/ubuntu/" in text:
            errors.append(f"{relative_path}: hardcoded workstation path")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                errors.append(f"{relative_path}: broken link {target}")
    return errors


if __name__ == "__main__":
    problems = validate()
    if problems:
        print("OKF_DOCS_VALIDATION_FAILED")
        for problem in problems:
            print(problem)
        sys.exit(1)
    print("OKF_DOCS_VALIDATION_OK")
