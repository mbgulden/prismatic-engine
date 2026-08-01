#!/usr/bin/env python3
"""Purge abandoned Google Jules sessions stuck awaiting user feedback.

Default mode is a dry run. Pass ``--execute`` to call:

    jules remote delete --session <session_id>

Candidate selection is intentionally conservative:
- the session must appear in ``jules remote list --session``;
- the status text must contain ``Awaiting User`` / ``Awaiting User F``;
- a tracked state JSON file must mention the session id; and
- the tracked launch/created/start timestamp (or state-file mtime fallback) must be
  older than the configured threshold (24 hours by default).
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SESSION_ID_RE = re.compile(r"\b(\d{15,25})\b")
AWAITING_RE = re.compile(r"Awaiting\s+User(?:\s+Feedback|\s+F)?", re.IGNORECASE)
TIMESTAMP_KEYS = (
    "launched_at",
    "launchedAt",
    "launch_time",
    "launchTime",
    "created_at",
    "createdAt",
    "started_at",
    "startedAt",
    "submitted_at",
    "submittedAt",
    "timestamp",
)
SESSION_ID_KEYS = (
    "session_id",
    "sessionId",
    "jules_session_id",
    "julesSessionId",
    "id",
)
DEFAULT_STATE_GLOBS = (
    "/tmp/jules_dispatcher/*.json",
    "~/.hermes/profiles/orchestrator/state/jules*.json",
    "~/.hermes/profiles/orchestrator/logs/jules*.json",
    "~/.hermes/profiles/orchestrator/scripts/jules*.json",
    "~/work/agentic-swarm-ops/ops/orchestration/session-manifest.json",
)


@dataclasses.dataclass(frozen=True)
class JulesSession:
    session_id: str
    raw_line: str
    status: str


@dataclasses.dataclass(frozen=True)
class TrackedSession:
    session_id: str
    launched_at: datetime | None
    source_path: str
    source_kind: str


@dataclasses.dataclass(frozen=True)
class Decision:
    session: JulesSession
    tracked: TrackedSession | None
    age_hours: float | None
    action: str
    reason: str


def parse_timestamp(value: Any) -> datetime | None:
    """Parse common JSON timestamp shapes into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Accept unix seconds and milliseconds.
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.isdigit():
        return parse_timestamp(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_jules_sessions(output: str) -> list[JulesSession]:
    sessions: list[JulesSession] = []
    for line in output.splitlines():
        match = SESSION_ID_RE.search(line)
        if not match:
            continue
        status = "Awaiting User Feedback" if AWAITING_RE.search(line) else ""
        if not status:
            for known in (
                "Completed",
                "In Progress",
                "Planning",
                "Failed",
                "Awaiting Plan",
            ):
                if known.lower() in line.lower():
                    status = known
                    break
        sessions.append(
            JulesSession(match.group(1), line.rstrip(), status or "Unknown")
        )
    return sessions


def run_cmd(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )


def walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def walk_json_with_timestamp(
    value: Any, inherited_timestamp: datetime | None = None
) -> Iterable[tuple[dict[str, Any], datetime | None]]:
    """Yield dict records with the nearest parent timestamp inherited.

    Jules manifests commonly store ``created_at`` on the work unit while the
    nested ``sessions`` entry only contains the remote session id. Carrying the
    nearest timestamp down lets the purge script cross-reference those entries
    without requiring every nested dict to duplicate launch time.
    """
    if isinstance(value, dict):
        current_timestamp = extract_timestamp(value) or inherited_timestamp
        yield value, current_timestamp
        for child in value.values():
            yield from walk_json_with_timestamp(child, current_timestamp)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json_with_timestamp(child, inherited_timestamp)


def extract_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in SESSION_ID_KEYS:
        raw = record.get(key)
        if isinstance(raw, str) and SESSION_ID_RE.fullmatch(raw):
            ids.add(raw)
        elif isinstance(raw, int) and SESSION_ID_RE.fullmatch(str(raw)):
            ids.add(str(raw))
    # Some manifests store a URL rather than a dedicated session id.
    for raw in record.values():
        if isinstance(raw, str):
            ids.update(SESSION_ID_RE.findall(raw))
    return ids


def extract_timestamp(record: dict[str, Any]) -> datetime | None:
    for key in TIMESTAMP_KEYS:
        parsed = parse_timestamp(record.get(key))
        if parsed:
            return parsed
    return None


def expand_state_paths(patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        expanded = os.path.expanduser(os.path.expandvars(pattern))
        for found in glob.glob(expanded, recursive=True):
            path = Path(found)
            if path.is_file() and path.suffix.lower() == ".json":
                paths.append(path)
    return sorted(set(paths))


def load_tracked_sessions(patterns: Iterable[str]) -> dict[str, TrackedSession]:
    tracked: dict[str, TrackedSession] = {}
    for path in expand_state_paths(patterns):
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            print(f"WARN skipped_state_file path={path} error={exc}", file=sys.stderr)
            continue
        fallback = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        for record, inherited_timestamp in walk_json_with_timestamp(data):
            ids = extract_ids(record)
            if not ids:
                continue
            record_timestamp = extract_timestamp(record)
            timestamp = record_timestamp or inherited_timestamp or fallback
            kind = (
                "timestamp"
                if (record_timestamp or inherited_timestamp)
                else "file_mtime"
            )
            for session_id in ids:
                current = tracked.get(session_id)
                candidate = TrackedSession(session_id, timestamp, str(path), kind)
                if current is None or (
                    candidate.launched_at
                    and current.launched_at
                    and candidate.launched_at < current.launched_at
                ):
                    tracked[session_id] = candidate
    return tracked


def decide(
    sessions: Iterable[JulesSession],
    tracked: dict[str, TrackedSession],
    threshold_hours: float,
    now: datetime,
) -> list[Decision]:
    decisions: list[Decision] = []
    for session in sessions:
        state = tracked.get(session.session_id)
        if not AWAITING_RE.search(session.status) and not AWAITING_RE.search(
            session.raw_line
        ):
            decisions.append(
                Decision(
                    session, state, None, "skip", "status_not_awaiting_user_feedback"
                )
            )
            continue
        if state is None or state.launched_at is None:
            decisions.append(
                Decision(session, state, None, "skip", "no_tracked_state_json")
            )
            continue
        age_hours = (now - state.launched_at).total_seconds() / 3600
        if age_hours >= threshold_hours:
            decisions.append(
                Decision(
                    session,
                    state,
                    age_hours,
                    "purge",
                    "awaiting_feedback_older_than_threshold",
                )
            )
        else:
            decisions.append(
                Decision(
                    session, state, age_hours, "skip", "tracked_age_below_threshold"
                )
            )
    return decisions


def format_decision(decision: Decision) -> str:
    age = "unknown" if decision.age_hours is None else f"{decision.age_hours:.1f}h"
    source = decision.tracked.source_path if decision.tracked else "-"
    return (
        f"{decision.action.upper():5} session={decision.session.session_id} "
        f"age={age} reason={decision.reason} state_source={source} line={decision.session.raw_line}"
    )


def jules_supports_remote_delete(jules_bin: str) -> bool:
    result = run_cmd([jules_bin, "remote", "--help"])
    help_text = result.stdout + result.stderr
    return bool(re.search(r"^\s+delete\s+", help_text, re.MULTILINE))


def delete_session(jules_bin: str, session_id: str) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        [jules_bin, "remote", "delete", "--session", session_id], timeout=120
    )


def command_failed(result: subprocess.CompletedProcess[str]) -> bool:
    text = (result.stdout + result.stderr).lower()
    return (
        result.returncode != 0
        or "error:" in text
        or "unknown command" in text
        or "unknown flag" in text
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jules-bin", default=os.environ.get("JULES_BIN", "jules"))
    parser.add_argument(
        "--state-glob",
        action="append",
        dest="state_globs",
        help="JSON state glob; may be repeated",
    )
    parser.add_argument("--threshold-hours", type=float, default=24.0)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call jules remote delete for purge candidates",
    )
    parser.add_argument(
        "--list-output-file",
        help="Use captured jules list output instead of invoking the CLI",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON summary"
    )
    args = parser.parse_args(argv)

    state_globs = tuple(args.state_globs or DEFAULT_STATE_GLOBS)
    if args.list_output_file:
        output = Path(args.list_output_file).read_text()
    else:
        result = run_cmd([args.jules_bin, "remote", "list", "--session"])
        output = result.stdout + result.stderr
        if result.returncode != 0:
            print(output.rstrip(), file=sys.stderr)
            return result.returncode or 1

    sessions = parse_jules_sessions(output)
    tracked = load_tracked_sessions(state_globs)
    decisions = decide(
        sessions, tracked, args.threshold_hours, datetime.now(timezone.utc)
    )

    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    delete_supported = True
    if args.execute and any(d.action == "purge" for d in decisions):
        delete_supported = jules_supports_remote_delete(args.jules_bin)
        if not delete_supported:
            errors.append(
                {
                    "session_id": None,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "jules remote delete is not available in this Jules CLI build; cannot purge candidates automatically",
                }
            )

    for decision in decisions:
        if decision.action != "purge" or not delete_supported:
            continue
        if not args.execute:
            continue
        result = delete_session(args.jules_bin, decision.session.session_id)
        row = {
            "session_id": decision.session.session_id,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if command_failed(result):
            errors.append(row)
        else:
            deleted.append(row)

    summary = {
        "mode": "execute" if args.execute else "dry-run",
        "sessions_seen": len(sessions),
        "tracked_sessions": len(tracked),
        "purge_candidates": sum(1 for d in decisions if d.action == "purge"),
        "deleted": deleted,
        "errors": errors,
        "decisions": [dataclasses.asdict(d) for d in decisions],
    }
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(
            f"Jules stalled-session purge ({summary['mode']}): "
            f"sessions_seen={summary['sessions_seen']} tracked_sessions={summary['tracked_sessions']} "
            f"purge_candidates={summary['purge_candidates']} deleted={len(deleted)} errors={len(errors)}"
        )
        for decision in decisions:
            print(format_decision(decision))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
