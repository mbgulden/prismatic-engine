#!/usr/bin/env python3
"""CLI for preserving raw agent output before normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prismatic.agent_packet_normalizer import RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
from prismatic.agent_raw_output_queue import RawAgentOutputStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    persist = sub.add_parser("persist")
    persist.add_argument("--agent")
    persist.add_argument("--task-id")
    persist.add_argument("--source-event-id")
    persist.add_argument("--artifact-path")
    persist.add_argument("--file")

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--limit", type=int, default=50)

    preview = sub.add_parser("repair-preview")
    preview.add_argument("raw_output_id")

    rerun = sub.add_parser("mark-rerun-requested")
    rerun.add_argument("raw_output_id")

    args = parser.parse_args()
    store = RawAgentOutputStore()

    if args.command == "persist":
        raw_text = (
            Path(args.file).read_text(encoding="utf-8")
            if args.file
            else sys.stdin.read()
        )
        row = store.persist(
            raw_text=raw_text,
            agent=args.agent,
            task_id=args.task_id,
            source_event_id=args.source_event_id,
            raw_text_or_artifact_path=args.artifact_path or args.file,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
                    "raw_output": row.as_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "list":
        rows = [row.as_dict() for row in store.list(limit=args.limit)]
        print(
            json.dumps(
                {
                    "status": "ok",
                    "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
                    "count": len(rows),
                    "raw_outputs": rows,
                    "counts": store.counts(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "repair-preview":
        print(
            json.dumps(
                store.repair_preview(args.raw_output_id), indent=2, sort_keys=True
            )
        )
        return 0
    if args.command == "mark-rerun-requested":
        row = store.mark_rerun_requested(args.raw_output_id)
        print(
            json.dumps(
                {
                    "status": "rerun_requested",
                    "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
                    "auto_rerun_enabled": False,
                    "raw_output": row.as_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
