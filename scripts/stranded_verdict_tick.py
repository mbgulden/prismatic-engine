#!/usr/bin/env python3
"""Stranded-verdict tick (T1 deterministic plan, item 3).

Default: DRY-RUN. Lists open PRs (read-only), evaluates verdicts against the
7-day no-strand line, appends decision records to the dry-run log, and
refreshes the digest fragment the autonomy digest consumes. Performs NO
GitHub writes.

Live mode (``--live``): posts a verdict comment and applies a label for
``overdue`` / ``approaching`` PRs. Requires ``PRISMATIC_VERDICT_LIVE=1``
AND a fresh dry-run log (see ``prismatic.review_factory.verdict_live``);
refuses otherwise. Every live action is audited.

Cron (dry-run, 7-day soak):
    15 6 * * * cd /home/ubuntu/work/prismatic-engine && \
      /home/ubuntu/.prismatic/venv_stable/bin/python scripts/stranded_verdict_tick.py \
      >> /home/ubuntu/.prismatic/audit/stranded-verdict-tick.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prismatic.review_factory.verdict import run_pipeline
from prismatic.review_factory.verdict_live import (
    DEFAULT_DIGEST_FRAGMENT,
    DEFAULT_DRY_RUN_LOG,
    DEFAULT_LIVE_LOG,
    run_live_verdicts,
    verdicts_for_digest,
    write_digest_fragment,
    VerdictLiveError,
)

REPO = "mbgulden/prismatic-engine"


def _list_open_prs(repo: str) -> list[dict]:
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,author,createdAt,headRefName",
            "--limit",
            "100",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {proc.stderr.strip()[:300]}")
    raw = json.loads(proc.stdout or "[]")
    prs = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        prs.append(
            {
                "pr": entry.get("number"),
                "title": entry.get("title"),
                "author": author.get("login") if isinstance(author, dict) else author,
                "created_at": entry.get("createdAt"),
                "branch": entry.get("headRefName"),
            }
        )
    return prs


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Issue live verdict actions (comments + labels). Requires "
        "PRISMATIC_VERDICT_LIVE=1 and a fresh dry-run log; refuses otherwise.",
    )
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--dry-run-log", default=DEFAULT_DRY_RUN_LOG)
    parser.add_argument("--live-log", default=DEFAULT_LIVE_LOG)
    parser.add_argument("--fragment", default=DEFAULT_DIGEST_FRAGMENT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prs = _list_open_prs(args.repo)
    records = run_pipeline(prs, log_path=args.dry_run_log)

    failures: list[dict] = []
    if args.live:
        try:
            actions, failures = run_live_verdicts(
                records,
                args.repo,
                live_log=args.live_log,
                dry_run_log=args.dry_run_log,
            )
        except VerdictLiveError as exc:
            print(f"live verdicts refused: {exc}", file=sys.stderr)
            return 2
        payload = verdicts_for_digest(actions, records)
        payload["mode"] = "live"
        issued = sum(1 for a in actions if a.get("action") == "verdict_issued")
        skipped = sum(1 for a in actions if a.get("action") == "skipped")
        detail = (
            f"live: {issued} verdicts issued, {skipped} skipped, "
            f"{len(failures)} failed"
        )
    else:
        payload = verdicts_for_digest([], records)
        payload["mode"] = "dry_run"
        detail = "dry-run: no GitHub writes performed"

    write_digest_fragment(payload, args.fragment)

    counts: dict[str, int] = {}
    for record in records:
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(
        f"{len(records)} PRs evaluated ({breakdown}); "
        f"dry-run log appended to {args.dry_run_log}; "
        f"digest fragment written to {args.fragment}. {detail}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
