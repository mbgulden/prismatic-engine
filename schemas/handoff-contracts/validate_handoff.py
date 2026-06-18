#!/usr/bin/env python3
"""
validate_handoff_contract.py — Validate agent handoff documents against JSON Schemas.

Usage:
    python3 validate_handoff_contract.py agent-contract path/to/contract.json
    python3 validate_handoff_contract.py research-output path/to/research.json
    python3 validate_handoff_contract.py review-report path/to/report.json
    python3 validate_handoff_contract.py loopback-feedback path/to/feedback.json

Returns exit code 0 on valid, 1 on invalid, with details printed to stdout.
"""

import json
import sys
import os

try:
    from jsonschema import validate, ValidationError
except ImportError:
    print("ERROR: 'jsonschema' not installed. Run: pip install jsonschema")
    sys.exit(1)

SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))

SCHEMA_FILES = {
    "agent-contract": "agent-contract-schema.json",
    "research-output": "research-output-schema.json",
    "review-report": "review-report-schema.json",
    "loopback-feedback": "loopback-feedback-schema.json",
}


def load_schema(schema_name: str) -> dict:
    filename = SCHEMA_FILES.get(schema_name)
    if not filename:
        print(f"ERROR: Unknown schema '{schema_name}'. Choose from: {', '.join(SCHEMA_FILES.keys())}")
        sys.exit(1)
    path = os.path.join(SCHEMAS_DIR, filename)
    if not os.path.exists(path):
        print(f"ERROR: Schema file not found: {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <schema-type> <json-file>")
        print(f"  schema-type: {', '.join(SCHEMA_FILES.keys())}")
        sys.exit(1)

    schema_name = sys.argv[1]
    json_path = sys.argv[2]

    if not os.path.exists(json_path):
        print(f"ERROR: File not found: {json_path}")
        sys.exit(1)

    schema = load_schema(schema_name)

    with open(json_path) as f:
        instance = json.load(f)

    try:
        validate(instance=instance, schema=schema)
        print(f"✅ VALID — '{json_path}' conforms to '{schema_name}' schema")
        sys.exit(0)
    except ValidationError as e:
        print(f"❌ INVALID — '{json_path}' fails '{schema_name}' schema")
        print(f"   Path: {' → '.join(str(p) for p in e.absolute_path) if e.absolute_path else '<root>'}")
        print(f"   Reason: {e.message}")
        print(f"   Schema rule: {e.schema_path}")
        # Show what was expected vs what was found
        if e.instance is not None:
            print(f"   Got: {json.dumps(e.instance, indent=2)[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
