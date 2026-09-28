#!/usr/bin/env python3
"""Stranded-verdict dry-run tick (T1 deterministic plan, item 3).

Lists open PRs (read-only) and runs the verdict pipeline in DRY-RUN
mode, appending decision records to the dry-run log for Michael's
review. Performs NO GitHub writes: never closes, merges, comments on,
or labels any PR.

There is no live mode in this script. Live verdicts (comment + label +
digest) require Michael's explicit review of the dry-run log and are
not implemented here.

Cron (installed dry-run, 7-day soak):
    15 6 * * * cd /home/ubuntu/work/prismatic-engine && \
      /home/ubuntu/.prismatic/venv_stable/bin/python scripts/stranded_verdict_tick.py \
      >> /home/ubuntu/.prismatic/audit/stranded-verdict-tick.log 2>&1
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prismatic.review_factory.verdict import run_pipeline

REPO = "mbgulden/prismatic-engine"
DEFAULT_LOG = "~/.prismatic/audit/stranded-verdict-dryrun.jsonl"


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


def main() -> int:
    log_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(
        DEFAULT_LOG
    ).expanduser()
    prs = _list_open_prs(REPO)
    records = run_pipeline(prs, log_path=log_path)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(
        f"dry-run: {len(records)} PRs evaluated ({breakdown}); "
        f"log appended to {log_path}. No GitHub writes performed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
