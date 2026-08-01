#!/usr/bin/env python3
"""Validate a Prismatic handoff contract packet.

This CLI validates one JSON handoff packet against:

1. ``schemas/handoff-contract.schema.json``
2. focused semantic checks that are easier to review outside JSON Schema

It intentionally does not wire validation into dispatcher preflight by itself;
the dispatcher imports the same reusable validation helpers from
``prismatic.handoff_contracts``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prismatic.handoff_contracts import (
    DEFAULT_SCHEMA_PATH,
    load_json,
    validate_packet,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a Prismatic handoff contract JSON packet."
    )
    parser.add_argument("packet", type=Path, help="Path to handoff packet JSON file")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to handoff contract JSON Schema; default: {DEFAULT_SCHEMA_PATH}",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON result"
    )
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
