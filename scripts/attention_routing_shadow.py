#!/usr/bin/env python3
"""Attention-routing shadow driver (Jev #29).

Fetches PR metadata with the ``gh`` CLI, scores the PR with the attention
router in shadow mode, and writes an advisory markdown report. Advisory
only: this script never gates, blocks, approves, or denies anything.

Usage:
    GH_TOKEN=... python3 scripts/attention_routing_shadow.py
        [--pr 123] [--out attention-report.md]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()


def _prior_failed_runs(head_sha: str) -> int:
    """Count failed workflow runs on the same head SHA (the PR's own flakiness)."""
    try:
        out = _gh(
            "run",
            "list",
            "--commit",
            head_sha,
            "--json",
            "conclusion",
            "--jq",
            '[.[] | select(.conclusion == "failure")] | length',
        )
        return max(0, int(out.strip() or "0"))
    except Exception:
        return 0


def main() -> int:
    sys.path.insert(0, str(_repo_root()))

    ap = argparse.ArgumentParser(description="Attention routing (shadow mode)")
    ap.add_argument("--pr", default="", help="PR number (default: from event payload)")
    ap.add_argument(
        "--out",
        default="attention-report.md",
        help="Where to write the advisory markdown report",
    )
    ap.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH", ""),
        help="GitHub event payload path",
    )
    args = ap.parse_args()

    from prismatic.review_factory.attention_routing import (
        AttentionRouter,
        ChangedFile,
        PRInput,
    )

    pr_number = args.pr
    if not pr_number and args.event_path and Path(args.event_path).exists():
        event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
        pr = event.get("pull_request") or {}
        pr_number = str(pr.get("number", ""))
        if (event.get("inputs") or {}).get("pr_number"):
            pr_number = str(event["inputs"]["pr_number"])
    if not pr_number:
        print("No PR number available; cannot score.", file=sys.stderr)
        return 1

    try:
        pr_json = json.loads(
            _gh(
                "pr",
                "view",
                pr_number,
                "--json",
                "number,title,headRefOid,isDraft,author,authorAssociation,files",
            )
        )
    except Exception as exc:
        note = (
            f"<!-- attention-routing-shadow -->\n"
            f"# Attention routing — advisory report (shadow mode)\n\n"
            f"Could not fetch PR #{pr_number} metadata ({exc}); no score produced.\n\n"
            f"> **shadow — advisory only, never blocking**\n"
        )
        Path(args.out).write_text(note, encoding="utf-8")
        print(note)
        return 0

    head_sha = pr_json.get("headRefOid", "")
    is_draft = bool(pr_json.get("isDraft"))
    author = pr_json.get("author") or {}
    files = [
        ChangedFile(
            path=f.get("path", ""),
            additions=int(f.get("additions", 0) or 0),
            deletions=int(f.get("deletions", 0) or 0),
        )
        for f in pr_json.get("files", [])
        if f.get("path")
    ]
    first_time = pr_json.get("authorAssociation") in (
        "FIRST_TIME_CONTRIBUTOR",
        "FIRST_TIMER",
    )

    router = AttentionRouter()
    report = router.score_pr(
        PRInput(
            files=tuple(files),
            author_login=str(author.get("login", "")),
            first_time_contributor=first_time,
            prior_failed_runs=_prior_failed_runs(head_sha) if head_sha else 0,
        ),
        pr_number=int(pr_json.get("number", pr_number)),
        head_sha=head_sha,
    )
    markdown = report.render_markdown()
    Path(args.out).write_text(markdown, encoding="utf-8")

    print(
        f"PR #{report.pr_number}: combined band {report.combined_band} "
        f"(deterministic {report.deterministic_band}, "
        f"{report.deterministic_score:.1f}/100)"
    )
    print(f"Jev: {report.jev_status}")
    print(f"Report written to {args.out}")
    if is_draft:
        print("Draft PR: advisory comment withheld until ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
