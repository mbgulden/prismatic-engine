#!/usr/bin/env python3
"""
Prismatic Credential Injector
==============================
Reads credentials.json, filters by lane scope, exports as ephemeral
environment variables for agent subprocesses.

Usage:
    python3 injector.py <lane_name>        # Print scoped env vars (export FMT)
    python3 injector.py <lane_name> --json # Print scoped env vars as JSON
    python3 injector.py init               # Populate credentials.json from .env
    python3 injector.py validate           # Validate all credential entries

Agent NEVER sees the credentials.json file or keys outside its scope.
"""

import json
import os
import sys
from pathlib import Path

CREDENTIALS_PATH = Path(os.environ.get(
    "PRISMATIC_CREDENTIALS",
    os.path.expanduser("~/.hermes/profiles/orchestrator/credentials.json")
))
ORCHESTRATOR_ENV = Path(os.environ.get(
    "ORCHESTRATOR_ENV",
    os.path.expanduser("~/.hermes/profiles/orchestrator/.env")
))


def load_credentials() -> dict:
    """Load credentials.json with error handling."""
    if not CREDENTIALS_PATH.exists():
        print(f"ERROR: credentials.json not found at {CREDENTIALS_PATH}", file=sys.stderr)
        print("Run: python3 injector.py init", file=sys.stderr)
        sys.exit(1)

    try:
        with open(CREDENTIALS_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid credentials.json: {e}", file=sys.stderr)
        sys.exit(1)


def load_env_file(path: Path) -> dict:
    """Load a .env file into a dict."""
    env = {}
    if not path.exists():
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            env[key] = value
    return env


def get_scoped_keys(credentials: dict, lane: str) -> dict:
    """Get credentials scoped to a specific lane."""
    lane_defs = credentials.get("lane_definitions", {})
    creds = credentials.get("credentials", {})

    # Get allowed keys for this lane
    if lane in lane_defs:
        allowed_keys = set(lane_defs[lane].get("allowed_keys", []))
    else:
        # If lane not defined, check credential scopes directly
        allowed_keys = set()
        for key_name, cred in creds.items():
            if lane in cred.get("scope", []):
                allowed_keys.add(key_name)

    result = {}
    for key_name in allowed_keys:
        if key_name in creds:
            value = creds[key_name].get("value", "")
            if value:
                result[key_name] = value
    return result


def cmd_init():
    """Populate credentials.json values from orchestrator .env file."""
    with open(CREDENTIALS_PATH) as f:
        cred_db = json.load(f)

    env = load_env_file(ORCHESTRATOR_ENV)
    credentials = cred_db.get("credentials", {})
    populated = 0
    missing = 0

    for key_name, cred in credentials.items():
        source_env = cred.get("source_env")
        if source_env and source_env in env:
            cred["value"] = env[source_env]
            populated += 1
        elif source_env and source_env not in env:
            missing += 1

    # For keys without source_env (SMTP_CREDENTIALS, LINEAR_API_KEY_RO),
    # leave value as empty string — user must populate manually

    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(cred_db, f, indent=2)

    print(f"credentials.json populated: {populated} keys filled, {missing} source env vars missing")
    if missing:
        print("WARNING: Some source env vars were not found in orchestrator .env")
        print(f"  Missing source vars: {missing}")
        print("These may need manual population or are intentionally absent.")


def cmd_dump(lane: str, as_json: bool = False):
    """Dump scoped credentials for a lane as export commands or JSON."""
    cred_db = load_credentials()
    scoped = get_scoped_keys(cred_db, lane)

    if as_json:
        print(json.dumps(scoped, indent=2))
        return

    # Print as shell export commands for sourcing
    for key, value in scoped.items():
        # Escape single quotes for shell safety
        safe_value = value.replace("'", "'\\''")
        print(f"export {key}='{safe_value}'")


def cmd_validate():
    """Validate credential integrity and scoping."""
    cred_db = load_credentials()
    credentials = cred_db.get("credentials", {})
    lane_defs = cred_db.get("lane_definitions", {})
    errors = []

    for key_name, cred in credentials.items():
        # Check value format
        value = cred.get("value", "")
        if not value:
            errors.append(f"WARN: {key_name} has empty value — populate via 'init' or manually")

        # Check scope references valid lanes
        for scope in cred.get("scope", []):
            if scope not in lane_defs and not scope.startswith("agent:"):
                errors.append(f"WARN: {key_name} references undefined scope '{scope}'")

        # Check lane defs reference this key correctly
        for lane_name, lane_def in lane_defs.items():
            allowed = lane_def.get("allowed_keys", [])
            in_allowed = key_name in allowed
            in_scope = lane_name in cred.get("scope", [])

            if in_allowed and not in_scope:
                errors.append(f"WARN: {key_name} in {lane_name} allowed_keys but not in its scope")
            if in_scope and not in_allowed:
                errors.append(f"WARN: {key_name} in scope of {lane_name} but not in its allowed_keys")

    # Check lane isolation for media lanes
    for lane_name in ["lane_1", "lane_2", "lane_3"]:
        if lane_name in lane_defs:
            allowed = lane_defs[lane_name].get("allowed_keys", [])
            if allowed:
                errors.append(f"ERROR: Media lane {lane_name} has non-empty allowed_keys — must be empty []")

    if errors:
        print(f"Validation complete: {len(errors)} issues found:")
        for e in errors:
            print(f"  {e}")
    else:
        print("Validation PASS — all credentials correctly scoped")


def cmd_show_lanes():
    """Show all lane scopes and their allowed keys."""
    cred_db = load_credentials()
    lane_defs = cred_db.get("lane_definitions", {})

    print("Prismatic Credential Lane Scopes:")
    print("=" * 60)
    for lane_name, lane_def in sorted(lane_defs.items()):
        allowed = lane_def.get("allowed_keys", [])
        print(f"\n{lane_name}: {lane_def.get('name', '')}")
        print(f"  Description: {lane_def.get('description', '')}")
        if allowed:
            for key in allowed:
                print(f"    ✓ {key}")
        else:
            print(f"    (no external keys — fully isolated)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "init":
        cmd_init()
    elif command == "validate":
        cmd_validate()
    elif command == "lanes":
        cmd_show_lanes()
    elif command in ("dump", "export"):
        lane = sys.argv[2] if len(sys.argv) > 2 else "lane_general"
        as_json = "--json" in sys.argv
        cmd_dump(lane, as_json)
    else:
        # Assume it's a lane name
        as_json = "--json" in sys.argv
        cmd_dump(command, as_json)
