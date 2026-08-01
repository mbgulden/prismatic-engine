#!/usr/bin/env python3
"""
Verify Phase Dependencies — Ensure all prerequisite phases are completed.

Usage:
    python3 scripts/verify-phase-dependencies.py <phase_id> [--state-dir <path>]

Exits:
    0 if all prerequisites are met or if manifest is missing.
    1 if a prerequisite is not met.
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Verify phase dependencies.")
    parser.add_argument("phase_id", help="The ID of the phase to verify.")
    parser.add_argument("--state-dir", default="prismatic_state", help="Directory containing state files.")
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
        print(f"Error reading {deps_file}: {e}")
        sys.exit(1)

    # If the phase is not in the dependency map, assume no dependencies.
    if args.phase_id not in deps:
        print(f"Phase '{args.phase_id}' has no defined dependencies.")
        sys.exit(0)

    prerequisites = deps[args.phase_id]

    if not records_file.exists():
        if prerequisites:
            print(f"Violation: {records_file} missing, but '{args.phase_id}' requires: {prerequisites}")
            sys.exit(1)
        else:
            sys.exit(0)

    try:
        with open(records_file, "r") as f:
            records = json.load(f)
    except Exception as e:
        print(f"Error reading {records_file}: {e}")
        sys.exit(1)

    completed_phases = records.get("completed_phases", [])

    missing_deps = [p for p in prerequisites if p not in completed_phases]

    if missing_deps:
        print(f"Violation: Phase '{args.phase_id}' requires {prerequisites}, but the following are missing: {missing_deps}")
        sys.exit(1)

    print(f"Success: All prerequisites for '{args.phase_id}' are met.")
    sys.exit(0)

if __name__ == "__main__":
    main()
