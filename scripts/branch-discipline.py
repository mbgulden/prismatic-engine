#!/usr/bin/env python3
"""
Branch Discipline — automated feature branch creation from Linear issues.

Creates a properly-prefixed branch for the agent assigned to a Linear issue,
based out of deploy-fresh (or main if deploy-fresh doesn't exist).

Usage:
    python3 scripts/branch-discipline.py GRO-1520
    python3 scripts/branch-discipline.py GRO-1520 --base main
    python3 scripts/branch-discipline.py GRO-1520 --push

Part of the Prismatic Engine — automated branch creation.
Refs: GRO-1520, GRO-1557
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────
PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", "/home/ubuntu")
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")
LINEAR_TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"

# Agent label → branch prefix mapping (from PRISMATIC_ENGINE.yaml)
LABEL_TO_PREFIX = {
    "agent:fred": "feature/",
    "agent:ned": "ned/",
    "agent:agy": "design/",
    "agent:kai": "content/",
    "agent:jules": "fix/",
}

DEFAULT_BASE = "deploy-fresh"
FALLBACK_BASE = "main"


# ── Helpers ────────────────────────────────────────────

def gql(query: str) -> dict[str, Any]:
    """Execute a Linear GraphQL query."""
    import urllib.request

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": LINEAR_API_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_issue_labels(identifier: str) -> list[str]:
    """Query Linear for the issue's labels."""
    # First get the UUID
    result = gql(
        f'{{ issue(id: "{identifier}") {{ id labels {{ nodes {{ name }} }} }} }}'
    )
    issue = result.get("data", {}).get("issue")
    if not issue:
        print(f"❌ Issue {identifier} not found in Linear.")
        sys.exit(1)
    return [l["name"] for l in issue["labels"]["nodes"]]


def determine_prefix(identifier: str) -> str:
    """Determine the branch prefix from the issue's agent label."""
    labels = get_issue_labels(identifier)
    for label_name, prefix in LABEL_TO_PREFIX.items():
        if label_name in labels:
            print(f"  ✓ Agent label '{label_name}' → prefix '{prefix}'")
            return prefix

    print(f"  ⚠️  No agent label found on {identifier}. Labels: {labels}")
    print(f"  ⚠️  Defaulting to 'feature/' prefix (Fred's lane).")
    return "feature/"


def branch_exists(branch_name: str) -> bool:
    """Check if a branch exists locally or remotely."""
    # Check local
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        return True
    # Check remote
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch_name],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def check_base_branch(base: str) -> str:
    """Verify the base branch exists, fall back to main if not."""
    # Check local
    result = subprocess.run(
        ["git", "branch", "--list", base],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        return base

    # Check remote
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", base],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        # Fetch it
        print(f"  Fetching remote base branch 'origin/{base}'...")
        subprocess.run(
            ["git", "fetch", "origin", f"{base}:{base}"],
            capture_output=True, text=True, check=True,
        )
        return base

    print(f"  ⚠️  Base branch '{base}' not found — falling back to '{FALLBACK_BASE}'")
    return FALLBACK_BASE


def create_branch(identifier: str, prefix: str, base: str, push: bool = False) -> str:
    """Create the feature branch from the base branch."""
    branch_name = f"{prefix}{identifier}"

    if branch_exists(branch_name):
        print(f"  ⚠️  Branch '{branch_name}' already exists.")
        response = input(f"  Checkout existing branch? [y/N]: ").strip().lower()
        if response == "y":
            subprocess.run(["git", "checkout", branch_name], check=True)
            return branch_name
        else:
            print("  Aborted.")
            sys.exit(0)

    # Update the base branch
    print(f"  Updating base branch '{base}'...")
    subprocess.run(
        ["git", "fetch", "origin", base],
        capture_output=True, text=True,
    )
    subprocess.run(["git", "checkout", base], capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "pull", "origin", base, "--ff-only"],
        capture_output=True, text=True,
    )

    # Create the branch
    print(f"  Creating branch '{branch_name}' from '{base}'...")
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        check=True,
    )

    if push:
        print(f"  Pushing '{branch_name}' to origin...")
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", branch_name],
            check=True,
        )
        print(f"  ✅ Branch '{branch_name}' pushed to origin.")

    return branch_name


def verify_branch(branch_name: str) -> bool:
    """Verify the branch was created successfully."""
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


# ── Main ───────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Create a properly-prefixed feature branch from a Linear issue."
    )
    parser.add_argument(
        "issue",
        help="Linear issue identifier (e.g., GRO-1520)",
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"Base branch to branch from (default: {DEFAULT_BASE}, falls back to {FALLBACK_BASE})",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push the new branch to origin after creation",
    )
    args = parser.parse_args()

    identifier = args.issue.strip()

    if not LINEAR_API_KEY:
        print("❌ LINEAR_API_KEY environment variable not set.")
        sys.exit(1)

    # 1. Determine the branch prefix from the Linear issue
    print(f"🔍 Looking up {identifier} in Linear...")
    prefix = determine_prefix(identifier)

    # 2. Check / update the base branch
    base = check_base_branch(args.base)

    # 3. Create the branch
    branch_name = create_branch(identifier, prefix, base, push=args.push)

    # 4. Verify
    if verify_branch(branch_name):
        print(f"✅ Branch '{branch_name}' created and verified.")
        print(f"   Base: {base}")
        print(f"   Prefix: {prefix}")
        print(f"   Ready for work.")
    else:
        print(f"❌ Branch '{branch_name}' verification failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
