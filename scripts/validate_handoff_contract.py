#!/usr/bin/env python3
"""Validate a Prismatic handoff contract packet.

This CLI validates one JSON handoff packet against:

1. ``schemas/handoff-contract.schema.json``
2. focused semantic checks that are easier to review outside JSON Schema

It intentionally does not wire validation into dispatcher preflight yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = ROOT / "schemas" / "handoff-contract.schema.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def path_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(path == allowed or path.startswith(allowed.rstrip("/") + "/") for allowed in allowed_paths)


def semantic_errors(packet: dict[str, Any]) -> list[str]:
    """Return focused review-contract errors not expressed in JSON Schema."""
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
    if proof_required and not production_proof.get("artifacts", []):
        errors.append("production-facing handoff is missing production_proof artifacts")

    return errors


def validate_packet(packet: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for error in sorted(validator.iter_errors(packet), key=str)]
    errors.extend(semantic_errors(packet))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a Prismatic handoff contract JSON packet.")
    parser.add_argument("packet", type=Path, help="Path to handoff packet JSON file")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to handoff contract JSON Schema; default: {DEFAULT_SCHEMA_PATH}",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        schema = load_json(args.schema)
        packet = load_json(args.packet)
        errors = validate_packet(packet, schema)
    except ValueError as exc:
        errors = [str(exc)]

    ok = not errors
    result = {
        "ok": ok,
        "packet": str(args.packet),
        "schema": str(args.schema),
        "errors": errors,
    }
    if args.json:
        stream = sys.stdout if ok else sys.stderr
        print(json.dumps(result, indent=2, sort_keys=True), file=stream)
    elif ok:
        print(f"HANDOFF_CONTRACT_VALID packet={args.packet}")
    else:
        print(f"HANDOFF_CONTRACT_INVALID packet={args.packet}", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
