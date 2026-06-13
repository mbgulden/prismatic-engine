#!/usr/bin/env python3
"""
Prismatic Engine pre-push hook — lane validation + lock checking.

Validates that the pushing agent:
1. Uses the correct branch prefix for their role
2. Only touches files within their lane (per PRISMATIC_ENGINE.yaml)
3. Doesn't push files locked by another agent
4. Doesn't push to deploy-fresh unless they're the staging governor (Fred)
5. Never pushes directly to main (production is manual-only)

Install: ln -s ../../scripts/pre-push-hook.py .git/hooks/pre-push
         chmod +x .git/hooks/pre-push

Part of the Prismatic Engine — Phase 3: Pre-push Hooks.
Refs: specs/prismatic-engine-architecture-v1.md §3, GRO-1218
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────
PRISMATIC_HOME = os.environ.get('PRISMATIC_HOME', '/home/ubuntu')
LOCK_FILE = Path(PRISMATIC_HOME) / '.antigravity' / 'swarm_locks.json'
STALE_TTL_MS = 300_000  # 5 minutes
GOVERNOR_AGENT = "fred"
STAGING_BRANCH = "deploy-fresh"
PRODUCTION_BRANCH = "main"

# ── Helpers ────────────────────────────────────────────


def _find_repo_root() -> Path:
    """Find the git repository root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def _get_current_branch() -> str:
    """Get the current branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _get_changed_files(local_sha: str, remote_sha: str, repo_root: Path) -> list[str]:
    """Get list of files changed between remote and local sha."""
    if remote_sha == "0000000000000000000000000000000000000000":
        # New branch — compare against the base (deploy-fresh or main)
        # Fall back to diffing against HEAD~1
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{local_sha}~1", local_sha],
            capture_output=True, text=True, cwd=str(repo_root),
        )
    else:
        result = subprocess.run(
            ["git", "diff", "--name-only", remote_sha, local_sha],
            capture_output=True, text=True, cwd=str(repo_root),
        )
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def _read_yaml_config(repo_root: Path) -> dict[str, Any] | None:
    """Read PRISMATIC_ENGINE.yaml from the repo root."""
    config_path = repo_root / "PRISMATIC_ENGINE.yaml"
    if not config_path.exists():
        return None
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _read_locks() -> list[dict[str, Any]]:
    """Read the lock registry."""
    if not LOCK_FILE.exists():
        return []
    try:
        with open(LOCK_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _determine_agent(branch: str, config: dict[str, Any]) -> str | None:
    """Determine which agent is pushing based on branch prefix."""
    agents = config.get("agents", {})
    for agent_id, agent_cfg in agents.items():
        prefix = agent_cfg.get("branch_prefix", "")
        if prefix and branch.startswith(prefix.rstrip("/")):
            return agent_id
    return None


def _check_lane_ownership(
    files: list[str], agent_id: str, config: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Check which files are within the agent's lane. Returns (owned, violations)."""
    agent_cfg = config.get("agents", {}).get(agent_id, {})
    lanes = agent_cfg.get("lanes", {})

    # "owner" lanes are the directories the agent can write to
    owner_dirs = []
    for entry in lanes.get("owner", []):
        if isinstance(entry, dict):
            owner_dirs.extend(entry.get("owner", []))
        elif isinstance(entry, str):
            owner_dirs.append(entry)

    # If agent owns "*", they can touch anything
    if "*" in owner_dirs:
        return files, []

    owned = []
    violations = []
    for f in files:
        is_owned = any(
            f == d.rstrip("/") or f.startswith(d.rstrip("/") + "/")
            or f.startswith(d)  # fuzzier match for non-suffixed dirs
            for d in owner_dirs
        )
        if is_owned:
            owned.append(f)
        else:
            violations.append(f)

    return owned, violations


def _check_file_locks(
    files: list[str], agent_id: str, locks: list[dict[str, Any]], repo_root: Path
) -> list[str]:
    """Check if any files are locked by a DIFFERENT agent. Returns blocked files."""
    import time
    now_ms = int(time.time() * 1000)
    blocked = []

    for lock in locks:
        # Skip stale locks
        last_hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
        if now_ms - last_hb > STALE_TTL_MS:
            continue
        # Skip own locks
        if lock["agentId"] == agent_id:
            continue
        # Check if this lock covers any of our files
        lock_path = lock["filePath"]
        for f in files:
            # Match exact file or path relative to repo root
            if f == lock_path or str(Path(f)) == lock_path:
                blocked.append(f)

    return blocked


# ── Main Hook Logic ────────────────────────────────────


def main() -> int:
    """Run pre-push validation. Returns 0 if OK, 1 if blocked."""

    # Read push refs from stdin
    ref_lines = sys.stdin.read().strip().split("\n")
    if not ref_lines or not ref_lines[0]:
        # No refs being pushed — allow (e.g., empty push)
        return 0

    repo_root = _find_repo_root()
    branch = _get_current_branch()

    # Rule 5: Block ALL pushes to main (production is manual-only)
    remote_refs = []
    for line in ref_lines:
        parts = line.split()
        if len(parts) >= 3:
            remote_refs.append(parts[2])
    for ref in remote_refs:
        if ref == f"refs/heads/{PRODUCTION_BRANCH}":
            print("❌ [Prismatic Engine] Push to main is BLOCKED.")
            print("   Production deployments are manual-only.")
            print("   Use deploy-fresh for staging, then merge manually.")
            return 1

    # Read the config
    config = _read_yaml_config(repo_root)
    if config is None:
        # No PRISMATIC_ENGINE.yaml — warn but allow (convention mode)
        print("⚠️  [Prismatic Engine] No PRISMATIC_ENGINE.yaml found.")
        print("   Push allowed, but governance is not active for this repo.")
        return 0

    # Determine agent from branch prefix
    agent_id = _determine_agent(branch, config)
    if agent_id is None:
        print(f"❌ [Prismatic Engine] Branch '{branch}' doesn't match any agent prefix.")
        agents = config.get("agents", {})
        print("   Valid prefixes:")
        for aid, acfg in agents.items():
            print(f"     {acfg.get('branch_prefix', 'N/A')} → {aid}")
        return 1

    # Rule 4: Block pushes to deploy-fresh unless governor
    governor = config.get("staging", {}).get("governor", GOVERNOR_AGENT)
    staging_branch_name = config.get("staging", {}).get("branch", STAGING_BRANCH)
    for ref in remote_refs:
        if ref == f"refs/heads/{staging_branch_name}" and agent_id != governor:
            print(f"❌ [Prismatic Engine] Push to {staging_branch_name} is BLOCKED.")
            print(f"   Only {governor} (staging governor) can push to {staging_branch_name}.")
            print(f"   You are: {agent_id}")
            return 1

    # Get changed files
    all_files: list[str] = []
    for line in ref_lines:
        parts = line.split()
        if len(parts) >= 4:
            local_sha, remote_sha = parts[1], parts[3]
            all_files.extend(_get_changed_files(local_sha, remote_sha, repo_root))

    if not all_files:
        return 0  # No files to check

    # Deduplicate
    all_files = list(dict.fromkeys(all_files))

    # Rule 2: Lane validation
    owned, violations = _check_lane_ownership(all_files, agent_id, config)
    if violations:
        print(f"❌ [Prismatic Engine] Lane violation by {agent_id}:")
        for f in violations:
            print(f"   - {f}")
        print(f"   These files are outside {agent_id}'s lane.")
        print(f"   Owned directories: {config['agents'][agent_id]['lanes']['owner']}")
        return 1

    # Rule 3: Lock checking
    locks = _read_locks()
    blocked = _check_file_locks(all_files, agent_id, locks, repo_root)
    if blocked:
        print(f"❌ [Prismatic Engine] Locked files detected:")
        for f in blocked:
            # Find who holds the lock
            for lock in locks:
                if lock["filePath"] == f or lock["filePath"] == str(Path(f)):
                    print(f"   - {f} (locked by {lock['agentId']})")
        print("   Wait for the lock to be released or contact the holding agent.")
        return 1

    print(f"✅ [Prismatic Engine] Pre-push OK: {agent_id} → {branch}")
    print(f"   Files: {len(all_files)} changed, {len(owned)} in-lane, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
