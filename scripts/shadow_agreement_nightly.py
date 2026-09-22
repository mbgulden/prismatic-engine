#!/usr/bin/env python3
"""Phase 0 nightly agreement job: shadow calls vs Michael's actual outcomes.

Reads the shadow observer's audit signals
(~/.prismatic/audit/shadow-decisions.jsonl), takes the latest decision per
PR, joins each against the PR's actual state on GitHub (merged /
closed-unmerged / still open), and computes the Phase 0 exit criteria via
`shadow_agreement.shadow_exit_met`:

- >= 30 PRs evaluated in shadow
- >= 95% agreement between system calls and Michael's merges
- zero "bad merge calls" (system said merge, PR later needed repair,
  rollback, or human revert)

Bad-merge detection is a best-effort mechanical proxy: for each merged PR
the system called "merge" on, it searches for a later PR with "revert" in
the title referencing it. The full deploy/rollback cross-check arrives
with the learn job; until then this proxy plus the nightly human-readable
summary is the evidence.

Output: ~/.prismatic/audit/shadow-agreement-YYYY-MM-DD.json plus a
human-readable summary on stdout (captured in the timer journal).

GitHub access: the `gh` CLI, already authenticated on webtop-hermes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.review_factory.shadow_agreement import (  # noqa: E402
    ACTUAL_CLOSED_UNMERGED,
    ACTUAL_MERGED,
    ACTUAL_OPEN,
    shadow_exit_met,
)

REPO = "mbgulden/prismatic-engine"
DEFAULT_SIGNALS = Path("~/.prismatic/audit/shadow-decisions.jsonl").expanduser()
DEFAULT_OUT_DIR = Path("~/.prismatic/audit").expanduser()


def _gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout or "null")


def load_latest_calls(signals_path: Path) -> dict[int, dict[str, Any]]:
    """Latest shadow decision per PR number: {pr: (call, head_sha)}."""
    latest: dict[int, dict[str, Any]] = {}
    if not signals_path.exists():
        return latest
    with open(signals_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sig = json.loads(line)
            except json.JSONDecodeError:
                continue
            md = sig.get("metadata") or {}
            if (
                md.get("agent") is None
                and sig.get("agent") != "prismatic-shadow-observer"
            ):
                continue
            pr = md.get("pr_number")
            call = md.get("call")
            if pr is None or call not in ("merge", "skip"):
                continue
            latest[int(pr)] = {
                "system_call": call,
                "head_sha": md.get("head_sha", ""),
                "timestamp": sig.get("timestamp", ""),
            }
    return latest


def actual_outcome(pr_number: int) -> str:
    """Michael's actual outcome for a PR: merged / closed_unmerged / open."""
    info = _gh_json(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            REPO,
            "--json",
            "state,mergedAt",
            "--jq",
            "{state: .state, merged: (.mergedAt != null)}",
        ]
    )
    if info.get("merged"):
        return ACTUAL_MERGED
    if info.get("state") == "CLOSED":
        return ACTUAL_CLOSED_UNMERGED
    return ACTUAL_OPEN


def looks_reverted(pr_number: int) -> bool:
    """Best-effort proxy: a later PR titled like a revert of this one."""
    try:
        hits = _gh_json(
            [
                "search",
                "prs",
                f"repo:{REPO} type:pr revert #{pr_number} in:title",
                "--json",
                "number",
                "--limit",
                "5",
            ]
        )
    except RuntimeError:
        return False
    return bool(hits)


def main() -> int:
    signals_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SIGNALS
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT_DIR

    latest = load_latest_calls(signals_path)
    records: list[dict[str, Any]] = []
    bad_merge_calls = 0
    for pr_number in sorted(latest):
        call = latest[pr_number]["system_call"]
        try:
            outcome = actual_outcome(pr_number)
        except RuntimeError as exc:
            print(f"PR #{pr_number}: outcome lookup failed ({exc}); skipping")
            continue
        records.append(
            {
                "pr_number": pr_number,
                "system_call": call,
                "actual_outcome": outcome,
                "head_sha": latest[pr_number]["head_sha"],
            }
        )
        if call == "merge" and outcome == ACTUAL_MERGED and looks_reverted(pr_number):
            bad_merge_calls += 1
            records[-1]["revert_detected"] = True

    result = shadow_exit_met(records, bad_merge_calls=bad_merge_calls)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"shadow-agreement-{today}.json"
    out_path.write_text(
        json.dumps(
            {
                "date": today,
                "signals_path": str(signals_path),
                **result,
                "records": records,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    print(
        f"shadow-agreement {today}: n_decided={result['n_decided']} "
        f"n_agreed={result['n_agreed']} n_pending={result['n_pending']} "
        f"rate={result['rate']} bad_merge_calls={bad_merge_calls} "
        f"exit_met={result['exit_met']} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"shadow-agreement FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
