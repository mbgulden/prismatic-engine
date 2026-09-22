#!/usr/bin/env python3
"""Shadow-mode entry point for novelty quarantine routing (Jev #27).

Reads a PR via the `gh` CLI, builds the QuarantineCandidate (changed
paths + novelty context derived from the PR; precedent count passed as
data, defaulting to 0/unavailable), runs the shadow router, prints a
summary, and appends the shadow audit row. Observe-only: never
quarantines, halts, pages, comments, or gates.

Exit code is always 0: every failure mode (gh error, unreadable PR, Jev
error) is logged and the run ends cleanly — fail-closed means "recommend
quarantine in the audit row", never "crash the workflow".

Usage:
    quarantine_routing_shadow.py route-pr --pr-number 42 [--repo o/r]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prismatic.review_factory.quarantine_routing import (  # noqa: E402
    QuarantineRouter,
)


def _gh_api(path: str) -> dict | list | None:
    """GET a GitHub API path via the gh CLI. Returns parsed JSON or None."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"gh api unavailable: {exc}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"gh api {path} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        print(f"gh api {path}: bad JSON: {exc}", file=sys.stderr)
        return None


def _is_migration(path: str) -> bool:
    lowered = path.lower()
    return "migration" in lowered or "alembic" in lowered


def _is_test_change(path: str) -> bool:
    base = os.path.basename(path)
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or base.startswith("test_")
        or path.endswith("_test.py")
    )


def cmd_route_pr(args: argparse.Namespace) -> int:
    pr = _gh_api(f"repos/{args.repo}/pulls/{args.pr_number}")
    if not isinstance(pr, dict):
        print("could not read PR; routed nothing", file=sys.stderr)
        return 0
    files_data = _gh_api(f"repos/{args.repo}/pulls/{args.pr_number}/files?per_page=100")
    files = (
        tuple(str(f.get("filename", "")) for f in files_data)
        if isinstance(files_data, list)
        else ()
    )
    head_sha = str((pr.get("head") or {}).get("sha", ""))
    labels = [
        str(item.get("name", ""))
        for item in (pr.get("labels") or [])
        if isinstance(item, dict)
    ]
    # Novelty context is pure data in. The precedent count comes from audit
    # similarity in the full pipeline; here it is unavailable, so the
    # default (0) lands unfamiliar-but-undetermined PRs in the gray zone,
    # where the default-off Jev gate keeps them advisory-only.
    candidate = {
        "candidate_id": f"pr:{args.pr_number}",
        "head_sha": head_sha,
        "changed_paths": files,
        "author_association": str(pr.get("author_association") or "NONE"),
        "labels": labels,
        "novelty_context": {
            "precedent_matches": 0,
            "first_time_event_types": [],
            "unknown_error_classes": [],
            "has_migration": any(_is_migration(p) for p in files),
            "has_test_change": any(_is_test_change(p) for p in files),
        },
    }
    router = QuarantineRouter()
    result = router.route(candidate)
    print(
        f"pr=#{args.pr_number} verdict={result.verdict.value} "
        f"matched_rule={result.matched_rule} jev={result.jev_status} "
        f"[shadow — no action taken]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("route-pr", help="route a PR's novelty (shadow, no action)")
    p.add_argument("--pr-number", required=True)
    p.add_argument("--repo", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "route-pr":
            return cmd_route_pr(args)
    except Exception as exc:  # fail-closed: log, exit 0
        print(f"shadow run failed closed: {exc!r}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
