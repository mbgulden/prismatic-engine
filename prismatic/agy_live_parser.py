#!/usr/bin/env python3
"""
prismatic/agy_live_parser.py — AGY Status Line JSON Parser

Reads AGY status line JSON from stdin (piped from agy_hook.sh),
extracts runtime state fields, and writes them to the telemetry
database via TelemetryCollector.

Architecture:
    AGY stdout ──► agy_hook.sh ──► agy_live_parser.py ──► TelemetryCollector
                         │                                       │
                     pipe each                          queue.Queue →
                     status line                       SQLite writer

Expected JSON schema (per status line):
    {
        "active_model": "Gemini 3.5 Flash (Medium)",
        "token_usage": {
            "prompt_tokens": 12345,
            "completion_tokens": 678
        },
        "context_usage_percentage": 62.5,
        "rate_limits": {
            "remaining": 45,
            "limit": 50,
            "reset_seconds": 30
        }
    }

Malformed JSON and partial payloads are handled gracefully —
invalid lines are logged to stderr and skipped.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _default_run_id() -> str:
    """Generate a run ID for this parser session."""
    pid = os.getpid()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"agy-live-{ts}-{pid}"


def parse_status_line(line: str) -> dict[str, Any] | None:
    """Parse a single AGY status line JSON payload.

    Returns a dict with extracted fields, or None if the line
    is malformed, empty, or lacks required fields.

    Gracefully handles:
    - Empty/whitespace-only lines
    - Non-JSON garbage
    - Partial JSON (truncated)
    - Missing optional fields (uses defaults)
    """
    line = line.strip()
    if not line:
        return None

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        print(f"[agy_live_parser] WARNING: malformed JSON, skipping: {line[:120]}...",
              file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(f"[agy_live_parser] WARNING: expected JSON object, got {type(data).__name__}, skipping",
              file=sys.stderr)
        return None

    # Extract .active_model
    active_model = data.get("active_model")

    # Extract .token_usage (nested object)
    token_usage = data.get("token_usage", {})
    if not isinstance(token_usage, dict):
        token_usage = {}
    prompt_tokens = token_usage.get("prompt_tokens", 0)
    completion_tokens = token_usage.get("completion_tokens", 0)

    # Extract .context_usage_percentage
    context_usage_pct = data.get("context_usage_percentage", 0.0)

    # Extract .rate_limits (nested object)
    rate_limits = data.get("rate_limits")
    if not isinstance(rate_limits, dict):
        rate_limits = None

    return {
        "active_model": active_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "context_usage_pct": context_usage_pct,
        "rate_limits": rate_limits,
        "raw_payload": line,
    }


def main() -> None:
    """Read stdin line-by-line, parse AGY status JSON, push to telemetry."""
    # Ensure prismatic-engine is on the path (called from hook scripts)
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    # Lazy import — defer until we need the collector
    from prismatic.telemetry import get_collector

    run_id = os.environ.get("AGY_LIVE_RUN_ID", _default_run_id())
    collector = get_collector()
    lines_processed = 0
    lines_failed = 0

    for line in sys.stdin:
        parsed = parse_status_line(line)
        if parsed is None:
            lines_failed += 1
            continue

        collector.record_agy_live_state(
            run_id=run_id,
            active_model=parsed["active_model"],
            prompt_tokens=parsed["prompt_tokens"],
            completion_tokens=parsed["completion_tokens"],
            context_usage_pct=parsed["context_usage_pct"],
            rate_limits=parsed["rate_limits"],
            raw_payload=parsed["raw_payload"],
        )
        lines_processed += 1

    print(f"[agy_live_parser] Done. Processed: {lines_processed}, "
          f"Failed: {lines_failed}, Run ID: {run_id}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
