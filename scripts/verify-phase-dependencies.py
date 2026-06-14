#!/usr/bin/env python3
"""
Verify Phase Dependencies — Ensure all prerequisite phases are completed.

Reads phase_dependencies.json and run_records.json from prismatic_state/,
then validates that a requested phase ID has all its prerequisites satisfied
in completed run records.

Usage:
    python3 scripts/verify-phase-dependencies.py <phase_id> [--state-dir <path>]

Exits:
    0 if all prerequisites are met or manifest is missing.
    1 if a prerequisite is not met.
"""

import argparse
import json
import sys
from pathlib import Path


def get_completed_issue_ids(records_path: Path) -> set[str]:
    """Parse run_records.json (list of records) and return set of completed issue IDs."""
    with open(records_path, "r") as f:
        records = json.load(f)

    if isinstance(records, dict):
        # Support alternate format: {"completed_phases": [...]}
        return set(records.get("completed_phases", []))
    elif isinstance(records, list):
        # Production format: [{issue_id, status, ...}, ...]
        return {r["issue_id"] for r in records if r.get("status") == "completed"}

    return set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify phase dependencies.")
    parser.add_argument("phase_id", help="The ID of the phase to verify.")
    parser.add_argument(
        "--state-dir",
        default="prismatic_state",
        help="Directory containing state files.",
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    deps_file = state_dir / "phase_dependencies.json"
    records_file = state_dir / "run_records.json"

    if not deps_file.exists():
        print(f"Note: {deps_file} missing. Skipping dependency check.")
        sys.exit(0)

    try:
        with open(deps_file, "r") as f:
            deps = json.load(f)
    except Exception as e:
        print(f"Error reading {deps_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # If the phase is not in the dependency map, assume no dependencies.
    if args.phase_id not in deps:
        print(f"Phase '{args.phase_id}' has no defined dependencies.")
        sys.exit(0)

    prerequisites: list[str] = deps[args.phase_id]

    if not records_file.exists():
        if prerequisites:
            print(
                f"❌ Violation: {records_file} missing, "
                f"but '{args.phase_id}' requires: {prerequisites}"
            )
            sys.exit(1)
        sys.exit(0)

    try:
        completed_ids = get_completed_issue_ids(records_file)
    except Exception as e:
        print(f"Error reading {records_file}: {e}", file=sys.stderr)
        sys.exit(1)

    missing_deps = [p for p in prerequisites if p not in completed_ids]

    if missing_deps:
        print(
            f"❌ Dependency Violation: Phase '{args.phase_id}' requires "
            f"{prerequisites}, but the following are missing: {missing_deps}"
        )
        sys.exit(1)

    print(f"✅ Dependency check passed for phase '{args.phase_id}'.")
    sys.exit(0)


if __name__ == "__main__":
    main()
