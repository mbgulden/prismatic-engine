#!/usr/bin/env python3
"""Ingest a completed AGY result packet into the persisted completed-work store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.agy_completed_work import (  # noqa: E402
    ingest_completed_work,
    ingest_completed_work_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "packet", help="Path to AGY result packet JSON/log text, or '-' for stdin"
    )
    parser.add_argument("--db", dest="db_path", help="Optional SQLite DB path")
    parser.add_argument(
        "--format",
        choices=("auto", "json", "text"),
        default="auto",
        help="Input format. auto tries JSON first, then compact packet text.",
    )
    parser.add_argument(
        "--dirty-source", action="store_true", help="Classify as clean_rebuild_required"
    )
    parser.add_argument(
        "--source-is-stale", action="store_true", help="Classify as superseded"
    )
    parser.add_argument(
        "--conflict",
        dest="conflicts",
        action="append",
        default=[],
        help="Conflict path/reason; may be provided multiple times",
    )
    return parser.parse_args()


def load_payload(packet_arg: str) -> tuple[str, str]:
    if packet_arg == "-":
        return sys.stdin.read(), "stdin"
    return Path(packet_arg).read_text(encoding="utf-8"), packet_arg


def load_packet(packet_arg: str, *, input_format: str = "auto") -> tuple[dict, str]:
    payload, source = load_payload(packet_arg)
    if input_format in {"auto", "json"}:
        try:
            packet = json.loads(payload)
        except json.JSONDecodeError as exc:
            if input_format == "json":
                raise SystemExit(f"invalid JSON from {source}: {exc}") from exc
        else:
            if not isinstance(packet, dict):
                raise SystemExit(f"packet from {source} must be a JSON object")
            return packet, "json"
    return {"__completed_work_text__": payload}, "text"


def main() -> int:
    args = parse_args()
    packet, input_format = load_packet(args.packet, input_format=args.format)
    if input_format == "text":
        row = ingest_completed_work_text(
            packet["__completed_work_text__"],
            db_path=args.db_path,
        )
    else:
        row = ingest_completed_work(
            packet,
            db_path=args.db_path,
            dirty_source=args.dirty_source,
            source_is_stale=args.source_is_stale,
            conflicts=args.conflicts,
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "completed_work": row.as_dict(),
                "non_claims": {
                    "auto_merge": False,
                    "production_deploy": False,
                    "github_pr_created": False,
                    "agy_dispatch": False,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
