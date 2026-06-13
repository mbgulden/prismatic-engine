#!/usr/bin/env python3
"""
prismatic/agy_live_parser.py — AGY Statusline Harvester

Reads AGY status JSON from stdin (one JSON object per line — NDJSON format),
parses structured telemetry events, and records them to the Prismatic
telemetry system.

Pipe invocation::

    agy --print --issue GRO-1234 | agy_hook.sh
    cat agy_output.jsonl | python -m prismatic.agy_live_parser

Accepted event types (``event`` field):

``token_usage``
    Record token consumption for an agent run.
    Keys: run_id, agent, provider, model, prompt_tokens, completion_tokens,
          ttft_ms, tps

``tool_call``
    Record a tool call event (validation).
    Keys: run_id, agent, tool_name, duration_ms

``status``
    Track agent lifecycle (started / processing / completed / error).
    Keys: run_id, agent, issue_id, status, message

``step``
    Record a loop step event.
    Keys: run_id, agent, issue_id, step, total, message
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────

RUN_ID_PREFIX = "agy-live"
FALLBACK_LOG = os.environ.get(
    "PRISMATIC_AGY_EVENTS_LOG",
    "/tmp/prismatic/agy_events.jsonl",
)
LOG_APP = os.environ.get(
    "PRISMATIC_AGY_PARSER_LOG",
    "/tmp/agy_statusline_hook.log",
)

# Map tool_call event → validation event_type
TOOL_EVENT_MAP: dict[str, str] = {
    "read_file": "read",
    "write_file": "write",
    "search_files": "search",
    "terminal": "shell",
    "browser_navigate": "http_get",
    "web_search": "web_search",
}

# ── Telemetry import (best-effort) ─────────────────────────


def _import_collector():
    """Try to import prismatic.telemetry.get_collector.

    Adds the prismatic engine root to sys.path if needed.
    Returns the collector singleton or *None* if unavailable.
    """
    engine_root = os.environ.get(
        "PRISMATIC_ENGINE",
        str(Path(__file__).resolve().parent.parent),
    )
    if engine_root not in sys.path:
        sys.path.insert(0, engine_root)

    # Also try the directory containing this file (for ``python -m``)
    pkg_dir = str(Path(__file__).resolve().parent)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    if engine_root not in sys.path:
        sys.path.insert(0, engine_root)

    try:
        from prismatic.telemetry import get_collector  # type: ignore[import-untyped]

        return get_collector()
    except ImportError as exc:
        _log(f"[agy_live_parser] Telemetry import failed: {exc}")
        return None
    except Exception as exc:
        _log(f"[agy_live_parser] Unexpected telemetry error: {exc}")
        return None


def _log(msg: str) -> None:
    """Write a log line to the parser log file."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        os.makedirs(os.path.dirname(LOG_APP), exist_ok=True)
        with open(LOG_APP, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass  # Best-effort logging


def _fallback_write(record: dict[str, Any]) -> None:
    """Write a JSON event to the fallback log file.

    Used when the telemetry collector is unavailable.
    """
    try:
        os.makedirs(os.path.dirname(FALLBACK_LOG), exist_ok=True)
        with open(FALLBACK_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ── Event handlers ────────────────────────────────────────


def _handle_token_usage(
    collector: Any,
    event: dict[str, Any],
    fallback: bool,
) -> None:
    """Record token usage telemetry."""
    run_id = event.get("run_id", _make_run_id())
    agent = event.get("agent", "agy")
    provider = event.get("provider", "google-antigravity")
    model = event.get("model")
    prompt = event.get("prompt_tokens", 0)
    completion = event.get("completion_tokens", 0)
    ttft = event.get("ttft_ms", 0.0)
    tps = event.get("tps", 0.0)
    context_pct = event.get("context_pct", 0.0)

    if fallback or collector is None:
        _fallback_write({
            "type": "token_usage",
            "run_id": run_id,
            "agent": agent,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "ttft_ms": ttft,
            "tps": tps,
            "context_pct": context_pct,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    try:
        collector.record_tokens(
            run_id=run_id,
            agent=agent,
            provider=provider,
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            ttft_ms=ttft,
            tps=tps,
            context_pct=context_pct,
        )
    except Exception as exc:
        _log(f"[agy_live_parser] record_tokens failed: {exc}")


def _handle_tool_call(
    collector: Any,
    event: dict[str, Any],
    fallback: bool,
) -> None:
    """Record a tool call as a validation event."""
    run_id = event.get("run_id", _make_run_id())
    agent = event.get("agent", "agy")
    tool_name = event.get("tool_name", "unknown")
    duration_ms = event.get("duration_ms", 0)

    event_type = TOOL_EVENT_MAP.get(tool_name, tool_name)

    if fallback or collector is None:
        _fallback_write({
            "type": "tool_call",
            "run_id": run_id,
            "agent": agent,
            "tool_name": tool_name,
            "event_type": event_type,
            "duration_ms": duration_ms,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    try:
        collector.record_validation(
            run_id=run_id,
            agent=agent,
            event_type=event_type,
            total=1,
            passed=1,
            failed=0,
            sandbox_id=None,
            watch_sec=duration_ms / 1000.0,
        )
    except Exception as exc:
        _log(f"[agy_live_parser] record_validation failed: {exc}")


def _handle_status(
    collector: Any,
    event: dict[str, Any],
    fallback: bool,
) -> None:
    """Track agent lifecycle via agent_run records."""
    run_id = event.get("run_id", _make_run_id())
    agent = event.get("agent", "agy")
    issue_id = event.get("issue_id", "")
    status = event.get("status", "processing")
    message = event.get("message", "")

    if fallback or collector is None:
        _fallback_write({
            "type": "status",
            "run_id": run_id,
            "agent": agent,
            "issue_id": issue_id,
            "status": status,
            "message": message,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    try:
        if status in ("started", "dispatched"):
            provider = event.get("provider", "google-antigravity")
            model = event.get("model")
            collector.record_agent_run(
                run_id=run_id,
                agent=agent,
                issue_id=issue_id,
                provider=provider,
                model=model,
                status=status,
            )
        elif status in ("completed", "success"):
            collector.update_agent_run(
                run_id=run_id,
                status="completed",
                exit_code=event.get("exit_code", 0),
                credits_spent=event.get("credits_spent", 0),
            )
        elif status in ("failed", "error"):
            collector.update_agent_run(
                run_id=run_id,
                status="failed",
                exit_code=event.get("exit_code", 1),
                error_message=message or None,
            )
    except Exception as exc:
        _log(f"[agy_live_parser] status handler failed: {exc}")


def _handle_step(
    collector: Any,
    event: dict[str, Any],
    fallback: bool,
) -> None:
    """Record a work step as a loop event."""
    run_id = event.get("run_id", _make_run_id())
    agent = event.get("agent", "agy")
    issue_id = event.get("issue_id", "")
    step = event.get("step", 0)
    total = event.get("total", 0)
    message = event.get("message", "")

    if fallback or collector is None:
        _fallback_write({
            "type": "step",
            "run_id": run_id,
            "agent": agent,
            "issue_id": issue_id,
            "step": step,
            "total": total,
            "message": message,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    try:
        collector.record_loop(
            run_id=run_id,
            issue_id=issue_id or f"step-{step}-of-{total}",
            agent=agent,
            loop_type="micro_review" if total < 10 else "macro_handoff",
            trigger=f"step_{step}_of_{total}",
            resolved=True,
            depth=step,
        )
    except Exception as exc:
        _log(f"[agy_live_parser] record_loop failed: {exc}")


# ── Event router ──────────────────────────────────────────


def _make_run_id() -> str:
    """Generate a run ID from timestamp and PID."""
    return (
        f"{RUN_ID_PREFIX}-"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}-"
        f"{os.getpid()}"
    )


HANDLERS: dict[str, Any] = {
    "token_usage": _handle_token_usage,
    "tool_call": _handle_tool_call,
    "status": _handle_status,
    "step": _handle_step,
}


# ── Main ──────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AGY Statusline Harvester — parse NDJSON stdin → telemetry",
    )
    parser.add_argument(
        "--issue",
        default=os.environ.get("PRISMATIC_ISSUE_ID", ""),
        help="Linear issue ID (e.g. GRO-1234)",
    )
    parser.add_argument(
        "--fallback",
        action="store_true",
        default=False,
        help="Force file-based fallback (skip telemetry import)",
    )
    parser.add_argument(
        "--flush-interval",
        type=int,
        default=5,
        help="Seconds between fallback file flush (default: 5)",
    )
    args = parser.parse_args()

    run_id = _make_run_id()
    issue_id = args.issue
    fallback = args.fallback
    flush_interval = max(1, args.flush_interval)

    # Best-effort telemetry import
    collector = None if fallback else _import_collector()

    if collector is not None:
        _log(f"[agy_live_parser] Connected to telemetry collector (run={run_id})")
    elif not fallback:
        _log(
            f"[agy_live_parser] Telemetry unavailable — "
            f"using fallback log at {FALLBACK_LOG}"
        )
    else:
        _log(f"[agy_live_parser] Forced fallback mode (run={run_id})")

    processed = 0
    failed = 0
    last_flush = time.monotonic()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        # Try to parse as JSON
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON line — write to fallback as raw log
            _fallback_write({
                "type": "raw",
                "run_id": run_id,
                "issue_id": issue_id,
                "content": line,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
            processed += 1
            continue

        # Inject default context
        event.setdefault("run_id", run_id)
        event.setdefault("issue_id", issue_id)

        # Route by event type
        event_type = event.get("event", "")
        handler = HANDLERS.get(event_type)

        if handler:
            try:
                handler(collector, event, fallback)
                processed += 1
            except Exception:
                _log(
                    f"[agy_live_parser] Handler '{event_type}' failed: "
                    f"{traceback.format_exc()}"
                )
                failed += 1
        else:
            # Unknown event type — store raw
            _fallback_write({
                "type": "unknown",
                "run_id": run_id,
                "event_type": event_type,
                "raw": event,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
            processed += 1

        # Periodic flush notification
        if time.monotonic() - last_flush >= flush_interval:
            _log(
                f"[agy_live_parser] Progress — processed: {processed}, "
                f"failed: {failed}"
            )
            last_flush = time.monotonic()

    # Done
    _log(
        f"[agy_live_parser] Done. Processed: {processed}, "
        f"Failed: {failed}, Run ID: {run_id}"
    )

    # Final summary to stderr for parent process
    print(
        json.dumps({
            "event": "harvester_done",
            "run_id": run_id,
            "processed": processed,
            "failed": failed,
            "exit_code": 0,
        }),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
