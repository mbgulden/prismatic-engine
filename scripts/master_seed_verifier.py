#!/usr/bin/env python3
"""
Master Seed Verifier — standalone manifest validator for pre-push hooks.

Validates anchor_manifest.json without depending on the full orchestrator. 
Designed to be run as a git pre-push hook or standalone CI gate.

Usage:
    # Valid manifest → exit 0, no output
    python3 scripts/master_seed_verifier.py

    # Valid manifest → quiet mode (exit 0, no output)
    python3 scripts/master_seed_verifier.py --quiet

    # Corrupt manifest → exit 1, structured errors to stderr
    python3 scripts/master_seed_verifier.py

    # Use as git pre-push hook (reads from HEAD)
    python3 scripts/master_seed_verifier.py --from-git

    # JSON output for machine consumption
    python3 scripts/master_seed_verifier.py --json

    # Explicit manifest path
    python3 scripts/master_seed_verifier.py --manifest custom/path.json

Part of GRO-1623 — Master Seed Verifier.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Reuse validation from master_build_orchestrator
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from master_build_orchestrator import (
    ANCHOR_MANIFEST_SCHEMA,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_DESIGN_GUIDES_DIR,
    PRE_REQ_LEDGERS,
    ValidationError,
    ValidationReport,
    validate_manifest_schema,
    check_pre_req_ledgers,
    load_manifest,
)


def verify_manifest(
    manifest_path: str,
    design_guides_dir: Optional[str] = None,
) -> ValidationReport:
    """Verify a single manifest file. Returns a ValidationReport."""
    report = ValidationReport(manifest_path=manifest_path)
    report.checked_at = datetime.now(timezone.utc).isoformat()

    # 1. Load
    manifest, err = load_manifest(manifest_path)
    if err is not None:
        report.add_error(manifest_path, err)
        return report

    # 2. Schema validate
    if manifest is not None:
        validate_manifest_schema(manifest, report)

    # 3. Pre-req ledgers (if directory provided)
    if design_guides_dir:
        check_pre_req_ledgers(design_guides_dir, report)

    return report


def verify_from_git() -> ValidationReport:
    """Verify the anchor_manifest.json from the current git HEAD.

    Returns a ValidationReport. Falls back to filesystem check if git is unavailable.
    """
    import subprocess

    manifest_path = str(REPO_ROOT / DEFAULT_MANIFEST_PATH)

    # Try to read from git HEAD
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{DEFAULT_MANIFEST_PATH}"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse the git content directly
            manifest = json.loads(result.stdout)
            report = ValidationReport(manifest_path=manifest_path)
            report.checked_at = datetime.now(timezone.utc).isoformat()
            validate_manifest_schema(manifest, report)

            design_dir = str(REPO_ROOT / DEFAULT_DESIGN_GUIDES_DIR)
            if os.path.isdir(design_dir):
                check_pre_req_ledgers(design_dir, report)
            return report
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    # Fallback: filesystem check
    design_dir = str(REPO_ROOT / DEFAULT_DESIGN_GUIDES_DIR)
    return verify_manifest(manifest_path, design_dir)


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for master_seed_verifier."""
    parser = argparse.ArgumentParser(
        description="Master Seed Verifier — standalone anchor_manifest.json validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0 — Manifest is valid
  1 — Manifest is missing, corrupt, or has schema errors

Examples:
  %(prog)s                          # Validate default manifest
  %(prog)s --quiet                  # Silent on success
  %(prog)s --json                   # Machine-readable JSON output
  %(prog)s --from-git               # Validate from git HEAD
        """,
    )

    parser.add_argument(
        "--manifest",
        type=str,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to anchor_manifest.json (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--design-guides-dir",
        type=str,
        default=DEFAULT_DESIGN_GUIDES_DIR,
        help="Check pre-req ledgers in this directory",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all output on success (exit 0 = silent)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output validation report as JSON",
    )
    parser.add_argument(
        "--from-git",
        action="store_true",
        help="Validate manifest from git HEAD instead of filesystem",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point. Exit 0 = valid, 1 = invalid/missing/corrupt."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Determine manifest path
    if args.from_git:
        report = verify_from_git()
    else:
        manifest_path = (
            str(REPO_ROOT / args.manifest)
            if not os.path.isabs(args.manifest)
            else args.manifest
        )
        design_dir = (
            str(REPO_ROOT / args.design_guides_dir)
            if args.design_guides_dir and not os.path.isabs(args.design_guides_dir)
            else args.design_guides_dir
        )
        report = verify_manifest(manifest_path, design_dir)

    # Output
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif not report.valid:
        # Errors → stderr
        for e in report.errors:
            print(f"[{e.severity.upper()}] {e.path}: {e.message}", file=sys.stderr)
        for w in report.warnings:
            print(f"[{w.severity.upper()}] {w.path}: {w.message}", file=sys.stderr)
    elif not args.quiet:
        print(f"✅ {report.manifest_path} — valid")

    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
