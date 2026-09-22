#!/usr/bin/env python3
"""Historical PR replay — shadow evidence backfill CLI.

Replays closed PRs of mbgulden/prismatic-engine through the exact live
shadow path (shadow_poller.build_input_dict -> shadow_observer.evaluate)
and emits shadow records flagged ``backfilled: true``.

Read-only against GitHub (GET-only). Writes records only with ``--write``.

Usage:
    python scripts/replay_shadow_backfill.py --limit 5 --dry-run
    python scripts/replay_shadow_backfill.py --limit 60 --write --out shadow-backfill-2026-09-22.jsonl
    python scripts/replay_shadow_backfill.py --pr 504 --pr 505 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.review_factory.replay import (  # noqa: E402
    GitHubApiPRSource,
    filter_prs_with_check_runs,
    replay_batch,
    select_prs,
)
from prismatic.review_factory.shadow_agreement import (  # noqa: E402
    agreement_rate,
    shadow_exit_met,
)


def _to_agreement_records(records: list[dict]) -> list[dict]:
    return [
        {"system_call": r["system_call"], "actual_outcome": r["actual_outcome"]}
        for r in records
    ]


def _report(records: list[dict], errors: list[dict]) -> str:
    lines = [f"replayed={len(records)} errors={len(errors)}"]
    if records:
        stats = agreement_rate(_to_agreement_records(records))
        gate = shadow_exit_met(_to_agreement_records(records))
        rate = stats["rate"]
        lines.append(
            f"decided={stats['n_decided']} agreed={stats['n_agreed']} rate={rate:.3f}"
            if rate is not None
            else "rate=n/a"
        )
        lines.append(
            "gate(min_prs/min_agreement/zero_bad_merges)=("
            + "/".join(
                "Y" if gate["checks"][k] else "n"
                for k in ("min_prs", "min_agreement", "zero_bad_merges")
            )
            + ")"
        )
        unknown: dict[str, int] = {}
        for r in records:
            for f in r["replay"]["unknown_fields"]:
                unknown[f] = unknown.get(f, 0) + 1
        lines.append(f"unknown_fields={unknown or 'none'}")
        disagreements = [r for r in records if not r["agree"]]
        if disagreements:
            lines.append("disagreements:")
            for r in disagreements[:20]:
                lines.append(
                    f"  #{r['pr_number']} system={r['system_call']} "
                    f"actual={r['actual_outcome']} {r['pr_title'][:60]}"
                )
    for e in errors[:20]:
        lines.append(f"  ERROR #{e['pr_number']}: {e['error'][:120]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--since-days", type=int, default=90)
    ap.add_argument(
        "--pr",
        type=int,
        action="append",
        default=[],
        help="replay specific PR number(s); can repeat",
    )
    ap.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="selection order within the window (default desc: newest first — "
        "most informative about current norms and least decayed)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="full replay, print records, write nothing (default)",
    )
    ap.add_argument(
        "--write", action="store_true", help="append records as JSONL to --out"
    )
    ap.add_argument(
        "--out",
        default=None,
        help="output path for --write (default shadow-backfill-YYYY-MM-DD.jsonl)",
    )
    args = ap.parse_args(argv)

    source = GitHubApiPRSource()
    _runs_cache: dict[str, list[dict]] = {}

    def cached_runs(sha: str) -> list[dict]:
        if sha not in _runs_cache:
            _runs_cache[sha] = source.get_check_runs(sha)
        return _runs_cache[sha]

    if args.pr:
        prs = []
        for n in args.pr:
            prs.extend(
                p for p in source.list_closed_prs(args.since_days) if p["number"] == n
            )
        missing = set(args.pr) - {p["number"] for p in prs}
        for n in sorted(missing):
            print(
                f"PR #{n}: not found among closed PRs (last {args.since_days}d)",
                file=sys.stderr,
            )
    else:
        in_window = source.list_closed_prs(args.since_days)
        # Prefer PRs with CI history: without check runs every gate fails
        # closed (system=skip), which inflates agreement without testing
        # the pipeline's judgment. Deterministic: ascending PR numbers.
        # Probe newest-first with early stop: the freshest evidence is the
        # most informative about current norms and the least decayed.
        newest_first = sorted(in_window, key=lambda p: p["number"], reverse=True)
        with_runs = filter_prs_with_check_runs(
            newest_first, cached_runs, max_keep=args.limit
        )
        print(
            f"{len(in_window)} in-window PR(s), {len(with_runs)} with check runs",
            file=sys.stderr,
        )
        prs = select_prs(with_runs, args.limit, order=args.order)
    print(f"selected {len(prs)} PR(s) for replay", file=sys.stderr)

    records, errors = replay_batch(prs, source.get_pr_files, cached_runs)
    print(_report(records, errors))

    if args.write:
        out = Path(
            args.out or f"shadow-backfill-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        )
        with out.open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        print(f"wrote {len(records)} records to {out}")
    elif args.dry_run:
        for r in records:
            print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
