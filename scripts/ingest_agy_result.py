#!/usr/bin/env python3
"""Ingest a completed AGY result packet into the persisted completed-work store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prismatic.agy_completed_work import ingest_completed_work


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", help="Path to AGY result packet JSON, or '-' for stdin")
    parser.add_argument("--db", dest="db_path", help="Optional SQLite DB path")
    parser.add_argument("--dirty-source", action="store_true", help="Classify as clean_rebuild_required")
    parser.add_argument("--source-is-stale", action="store_true", help="Classify as superseded")
    parser.add_argument(
        "--conflict",
        dest="conflicts",
        action="append",
        default=[],
        help="Conflict path/reason; may be provided multiple times",
    )
    return parser.parse_args()


def load_packet(packet_arg: str) -> dict:
    if packet_arg == "-":
        payload = sys.stdin.read()
        source = "stdin"
    else:
        source = packet_arg
        payload = Path(packet_arg).read_text(encoding="utf-8")
    try:
        packet = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON from {source}: {exc}") from exc
    if not isinstance(packet, dict):
        raise SystemExit(f"packet from {source} must be a JSON object")
    return packet


def main() -> int:
    args = parse_args()
    row = ingest_completed_work(
        load_packet(args.packet),
        db_path=args.db_path,
        dirty_source=args.dirty_source,
        source_is_stale=args.source_is_stale,
        conflicts=args.conflicts,
    )
    print(json.dumps({"status": "ok", "completed_work": row.as_dict()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
