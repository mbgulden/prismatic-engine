#!/usr/bin/env python3
"""
prismatic → bot-delegation bridge watcher.

Reads nudge files written by Prismatic Engine's FileSignalProvider
(`/tmp/prismatic/nudge-{agent}`) and forwards them to the Hermes
orchestrator's bot-delegation queue (`/tmp/bot-delegation/requests/`).

This is the missing glue that lets the standalone engine work with
Hermes without requiring either side to know about the other. The
engine writes a nudge, this script copies it into the right directory
shape, and the orchestrator's bot-delegation-watchdog picks it up on
its next 1-min tick.

Why this exists (GRO-2401 follow-up):
The engine's signal provider writes to `/tmp/prismatic/nudge-{target}`
(see prismatic/providers/signals/file.py). The orchestrator's
bot-delegation-watchdog only watches `/tmp/bot-delegation/requests/`.
Without this bridge, dispatcher signals sit unhandled.

CLI flags:
  --once     Process pending nudges and exit (default for cron)
  --watch    Continuously poll (default for systemd)
  --interval Seconds between polls (default 10)

Idempotency:
- Each nudge file is moved to a processed/ subdirectory after copy
- Original file is removed only after successful copy
- Re-runs are safe (nothing in /tmp/bot-delegation/requests/ is overwritten)

Standalone-first:
- Works without Hermes (just exits 0 if /tmp/bot-delegation/ doesn't exist)
- Pure stdlib (pathlib, json, shutil, argparse)
- No external dependencies
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

NUDGE_DIR = Path("/tmp/prismatic")
BOT_DELEGATION_DIR = Path("/tmp/bot-delegation/requests")
PROCESSED_DIR = NUDGE_DIR / "nudges-processed"


def forward_one(nudge_file: Path) -> bool:
    """Forward a single nudge file to the orchestrator's bot-delegation queue.

    Returns True if forwarded (or already processed), False if skipped.
    """
    try:
        payload = json.loads(nudge_file.read_text())
    except Exception as exc:
        print(f"[bridge] WARN: {nudge_file.name} unreadable: {exc}", file=sys.stderr)
        # Move corrupt files to processed so we don't loop forever
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(nudge_file), PROCESSED_DIR / nudge_file.name)
        return False

    # Map the engine's nudge shape to bot-delegation's request shape.
    # Engine: {"target": "fred", "action": "work", "issue_id": "...", "title": "...", "priority": 3, "metadata": {}, "signal_id": "...", "created_at": 12345.6}
    # Bot-delegation expects: {"request_type": "agent_signal", "agent": "fred", "issue_id": "...", "title": "...", "priority": 3, "signal_id": "...", "created_at": 12345.6, "source": "prismatic-engine"}
    target = payload.get("target", "unknown")
    signal_id = payload.get("signal_id", "")
    request = {
        "request_type": "agent_signal",
        "agent": target,
        "issue_id": payload.get("issue_id", ""),
        "title": payload.get("title", f"Signal for {target}"),
        "priority": payload.get("priority", 3),
        "signal_id": signal_id,
        "created_at": payload.get("created_at", time.time()),
        "source": "prismatic-engine",
        "metadata": payload.get("metadata", {}),
    }

    # Write to bot-delegation with signal_id-based filename (prevents
    # duplicate forwarding if the bridge is run twice).
    out_name = f"prismatic-{signal_id[:12] or int(time.time()*1000)}-{target}.json"
    out_path = BOT_DELEGATION_DIR / out_name
    BOT_DELEGATION_DIR.mkdir(parents=True, exist_ok=True)

    # Idempotency: don't overwrite if a recent forward exists
    if out_path.exists():
        age = time.time() - out_path.stat().st_mtime
        if age < 60:
            print(f"[bridge] {out_name} already forwarded {int(age)}s ago, skipping")
            # Still move the nudge to processed to clean up
            PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(nudge_file), PROCESSED_DIR / nudge_file.name)
            return True

    out_path.write_text(json.dumps(request, indent=2))
    print(f"[bridge] forwarded {nudge_file.name} -> {out_path.name}")

    # Move processed nudge to archive
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(nudge_file), PROCESSED_DIR / nudge_file.name)
    return True


def process_pending() -> int:
    """Process all nudge files in NUDGE_DIR. Returns count forwarded."""
    if not NUDGE_DIR.exists():
        return 0
    forwarded = 0
    for nudge_file in sorted(NUDGE_DIR.glob("nudge-*")):
        if nudge_file.is_file():
            if forward_one(nudge_file):
                forwarded += 1
    return forwarded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bridge prismatic engine nudges to Hermes bot-delegation queue"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true",
        help="Process pending nudges and exit (default for cron)"
    )
    mode.add_argument(
        "--watch", action="store_true",
        help="Continuously poll (for systemd service)"
    )
    parser.add_argument(
        "--interval", type=int, default=10,
        help="Seconds between polls in --watch mode (default 10)"
    )
    args = parser.parse_args()

    # Default to --once if neither specified (cron-friendly)
    if not args.once and not args.watch:
        args.once = True

    if args.once:
        forwarded = process_pending()
        print(f"[bridge] processed {forwarded} nudge(s)")
        return 0

    # --watch mode: poll forever, exit on SIGINT/SIGTERM
    print(f"[bridge] watching {NUDGE_DIR} every {args.interval}s")
    try:
        while True:
            process_pending()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[bridge] exiting on SIGINT")
        return 0


if __name__ == "__main__":
    sys.exit(main())
