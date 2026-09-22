"""CLI handler for the Jev progress meter (read-only).

``prismatic progress`` renders how close the installation is to leveling
up the rollout ladder and what is blocking it. It never advances a
phase, arms anything, or writes state — it only renders eligibility.
Exit code is always 0; missing evidence renders as zeros.
"""

from __future__ import annotations

import argparse
import json
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prismatic progress",
        description=(
            "Show Jev's level-up progress meter: how close this installation "
            "is to advancing the rollout ladder and what is blocking it. "
            "Read-only — the meter shows eligibility, never decides."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (the dashboard contract)",
    )
    parser.add_argument(
        "--audit-dir",
        default=None,
        help="Override the audit directory (default: ~/.prismatic/audit)",
    )
    args = parser.parse_args(argv)

    try:
        from prismatic.review_factory.progress import (
            build_status,
            render_status,
            status_json,
        )

        report = build_status(audit_dir=args.audit_dir)
        if args.json:
            print(json.dumps(status_json(report), indent=2))
        else:
            print(render_status(report), end="")
    except Exception as exc:  # never crash, never non-zero: advisory only
        print(f"progress meter unavailable ({exc}); no evidence to show")
    return 0
