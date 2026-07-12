#!/usr/bin/env python3
"""Unified SQLite database vacuum runner for all state DBs under the Prismatic Engine.

Dynamically discovers SQLite database files under the Prismatic state directory,
verifies they are valid SQLite databases, and runs VACUUM and ANALYZE on them.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

def resolve_repo() -> Path:
    """Resolve the Git repo root using git command or environment variables."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass
    
    repo_dir = os.environ.get("PRISMATIC_REPO_DIR")
    if repo_dir:
        return Path(repo_dir).resolve()
        
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists() or (parent / "prismatic_state").exists():
            return parent
    return Path.cwd().resolve()

def is_sqlite_file(path: Path) -> bool:
    """Check if the file starts with the SQLite 3 magic header."""
    if not path.is_file() or path.stat().st_size < 16:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(16)
        return header.startswith(b"SQLite format 3\x00")
    except OSError:
        return False

def vacuum_and_analyze_db(db_path: Path, dry_run: bool = False) -> bool:
    """Run integrity check, VACUUM, and ANALYZE on a single database file."""
    try:
        size_before = db_path.stat().st_size
    except OSError as exc:
        print(f"Error accessing file {db_path}: {exc}", file=sys.stderr)
        return False

    print(f"Processing database: {db_path} ({size_before / 1024:.2f} KB)")
    if dry_run:
        print("  [DRY RUN] Would check integrity, VACUUM, and ANALYZE")
        return True

    conn = None
    try:
        # isolation_level=None enables autocommit mode, which is required to run VACUUM
        conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
        
        # Set busy timeout
        conn.execute("PRAGMA busy_timeout = 10000")
        
        # Check integrity
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()
        if not integrity or integrity[0] != "ok":
            print(f"  [WARNING] Integrity check failed for {db_path}: {integrity}", file=sys.stderr)
            
        # Run VACUUM
        print("  Running VACUUM...")
        conn.execute("VACUUM")
        
        # Run ANALYZE
        print("  Running ANALYZE...")
        conn.execute("ANALYZE")
        
        try:
            size_after = db_path.stat().st_size
            reclaimed = size_before - size_after
            reclaimed_pct = (reclaimed / size_before) * 100 if size_before > 0 else 0
            print(f"  [SUCCESS] Completed. New size: {size_after / 1024:.2f} KB (Reclaimed: {reclaimed / 1024:.2f} KB, {reclaimed_pct:.1f}%)")
        except OSError:
            print("  [SUCCESS] Completed.")
            
        return True
    except Exception as exc:
        print(f"  [ERROR] Failed to vacuum {db_path}: {exc}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified SQLite Database vacuum and analyze runner."
    )
    parser.add_argument(
        "--state-dir",
        "-d",
        help="Path to the state directory (default: PRISMATIC_STATE_DIR env var or ./prismatic_state relative to repo root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only find and list database files without modifying them",
    )
    args = parser.parse_args(argv)

    # Determine state directory
    state_dir_path = None
    if args.state_dir:
        state_dir_path = Path(args.state_dir).resolve()
    else:
        env_state_dir = os.environ.get("PRISMATIC_STATE_DIR")
        if env_state_dir:
            state_dir_path = Path(env_state_dir).resolve()
        else:
            repo_root = resolve_repo()
            state_dir_path = repo_root / "prismatic_state"

    if not state_dir_path.exists():
        print(f"State directory does not exist: {state_dir_path}", file=sys.stderr)
        return 1

    print(f"Scanning state directory: {state_dir_path}")
    
    # Discover database files (.db and .sqlite extensions)
    db_files: list[Path] = []
    for ext in ("*.db", "*.sqlite"):
        for path in state_dir_path.rglob(ext):
            if is_sqlite_file(path):
                db_files.append(path)

    # Sort database files for deterministic execution order
    db_files.sort()

    if not db_files:
        print("No valid SQLite database files found.")
        return 0

    print(f"Found {len(db_files)} SQLite database file(s).")
    
    success_count = 0
    failure_count = 0
    
    for db_path in db_files:
        if vacuum_and_analyze_db(db_path, dry_run=args.dry_run):
            success_count += 1
        else:
            failure_count += 1

    print(f"\nSummary: {success_count} succeeded, {failure_count} failed.")
    return 1 if failure_count > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
