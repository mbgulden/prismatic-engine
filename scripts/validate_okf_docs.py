#!/usr/bin/env python3
"""Fail-closed validation for Prismatic canonical docs and OKF registry."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

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
    "docs/contracts/evidence-retention.md",
    "docs/decisions/index.md",
    "docs/decisions/ADR-0001-documentation-source-of-truth.md",
    "docs/research/verifiers-are-king-evidence-review.md",
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


def validate() -> list[str]:
    """Return every documentation governance violation."""
    errors: list[str] = []
    for relative_path in CANONICAL:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing canonical path: {relative_path}")
    if errors:
        return errors

    registry = json.loads((ROOT / "okf/index.yaml").read_text())
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
