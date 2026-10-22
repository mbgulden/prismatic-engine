"""E1 cross-system timeline aggregator (gap G2, tweaks plan Phase 3).

A read-only collector that merges four event sources into one JSONL feed,
source-tagged and time-ordered:

1. ``kanban``        -- ``task_events`` in the Hermes kanban database
   (``$PRISMATIC_KANBAN_DB`` / ``$HERMES_KANBAN_DB`` / ``<hermes>/kanban.db``),
   opened in SQLite read-only mode.
2. ``event_bus``     -- ``events`` in the Prismatic EventBus store
   (``$PRISMATIC_BUS_DB``, then the ``PRISMATIC_HOME``-relative path, then
   ``~/.prismatic/bus/event_log.sqlite`` — same convention as
   ``prismatic/gateway/event_bus.py``).
3. ``admissions``    -- ``task_admissions`` in the same EventBus store.
4. ``evidence:<p>``  -- ``verification_events`` in each Hermes profile's
   ``verification_evidence.db`` (glob ``<root>/*/verification_evidence.db``).

Feed contract
-------------
Each feed line is a JSON object::

    {"record_kind": "event", "source": "<tag>", "ts": "<ISO-8601 UTC>",
     "ts_epoch": <float epoch seconds>, "seq": <stable per-source order>,
     "record": {<source-specific fields>}}

The final line is a self-describing manifest::

    {"record_kind": "manifest", "generated_at": ..., "total_events": N,
     "order": "ts_epoch,source,seq",
     "sources": {"<tag>": {"status": "ok|missing|error", "count": N,
                            "dropped": N, "error": null}}}

Total order: records are sorted by ``(ts_epoch, source tag, seq)``. The
manifest's per-source counts are the reconciliation surface: a row that
disappears from a source changes the manifest count on the next run
instead of being silently absorbed into an opaque feed.

Read-only: every source is opened with ``file:<path>?mode=ro``; the
aggregator never writes to a source database. The feed is a view; the
sources remain authoritative.

CLI exit codes: 0 = every source ok or missing; 2 = at least one source
errored (the feed is still written with the sources that succeeded).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_MISSING = "missing"
SOURCE_STATUS_ERROR = "error"

# A collector pulls FeedEvents out of an open read-only connection,
# tagged with the source tag from its spec (so per-profile evidence tags
# are preserved end to end).
Collector = Callable[[str, sqlite3.Connection], Iterator["FeedEvent"]]


@dataclass
class FeedEvent:
    """One normalized event from a single source."""

    source: str
    seq: int
    ts_epoch: float
    record: dict[str, Any]

    def to_line(self) -> dict[str, Any]:
        return {
            "record_kind": "event",
            "source": self.source,
            "seq": self.seq,
            "ts": iso_utc(self.ts_epoch),
            "ts_epoch": self.ts_epoch,
            "record": self.record,
        }


@dataclass
class SourceResult:
    """Outcome of reading one source (one tag, one file, one table)."""

    tag: str
    status: str = SOURCE_STATUS_OK
    count: int = 0
    dropped: int = 0
    error: str | None = None


@dataclass
class AggregateReport:
    """Everything an aggregate() run produced."""

    events: list[FeedEvent] = field(default_factory=list)
    sources: list[SourceResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.status == SOURCE_STATUS_ERROR for s in self.sources)

    def build_manifest(self, generated_at: str) -> dict[str, Any]:
        return {
            "generated_at": generated_at,
            "order": "ts_epoch,source,seq",
            "record_kind": "manifest",
            "sources": {
                s.tag: {
                    "count": s.count,
                    "dropped": s.dropped,
                    "error": s.error,
                    "status": s.status,
                }
                for s in self.sources
            },
            "total_events": len(self.events),
        }


# ── Timestamp and payload helpers ──────────────────────────────────


def parse_timestamp(value: Any, field_name: str) -> float:
    """Normalize a source timestamp to float epoch seconds (UTC).

    Accepts int/float epoch seconds or ISO-8601 strings (naive values are
    assumed UTC). Raises ValueError on anything else.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name}: boolean is not a timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    raise ValueError(f"{field_name}: unsupported timestamp {value!r}")


def iso_utc(epoch: float) -> str:
    """Render epoch seconds as ISO-8601 UTC with a Z suffix."""
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def try_json(raw: Any) -> Any:
    """Parse a JSON text column; fall back to a marker dict when the text
    is not valid JSON so a bad payload never drops the row from the feed."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return {"unparsed": raw}


# ── Source opening (strictly read-only) ────────────────────────────


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open a SQLite database strictly read-only (URI mode=ro).

    Raises sqlite3.Error when the file is missing, unreadable, or not a
    valid SQLite database.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("SELECT 1")  # force the open to surface errors now
    return conn


# ── Collectors (one per source) ────────────────────────────────────


def collect_kanban(tag: str, conn: sqlite3.Connection) -> Iterator[FeedEvent]:
    """kanban: ``task_events`` in the Hermes kanban DB (epoch int)."""
    for event_id, task_id, run_id, kind, payload, created_at in conn.execute(
        "SELECT id, task_id, run_id, kind, payload, created_at FROM task_events"
    ):
        yield FeedEvent(
            source=tag,
            seq=event_id,
            ts_epoch=parse_timestamp(created_at, "created_at"),
            record={
                "event_id": event_id,
                "kind": kind,
                "payload": try_json(payload),
                "run_id": run_id,
                "task_id": task_id,
            },
        )


def collect_event_bus(tag: str, conn: sqlite3.Connection) -> Iterator[FeedEvent]:
    """event_bus: ``events`` in the Prismatic EventBus store (epoch float)."""
    for rowid, dedup_key, topic, payload_json, ts, processed in conn.execute(
        "SELECT rowid, dedup_key, topic, payload_json, ts, processed FROM events"
    ):
        yield FeedEvent(
            source=tag,
            seq=rowid,
            ts_epoch=parse_timestamp(ts, "ts"),
            record={
                "dedup_key": dedup_key,
                "payload": try_json(payload_json),
                "processed": processed,
                "topic": topic,
            },
        )


def collect_admissions(tag: str, conn: sqlite3.Connection) -> Iterator[FeedEvent]:
    """admissions: ``task_admissions`` in the EventBus store (ISO text)."""
    for (
        rowid,
        task_id,
        idem_key,
        status,
        actor,
        received_at,
        event_id,
        payload_json,
    ) in conn.execute(
        "SELECT rowid, task_id, idempotency_key, status, actor, received_at, event_id, payload_json"
        " FROM task_admissions"
    ):
        yield FeedEvent(
            source=tag,
            seq=rowid,
            ts_epoch=parse_timestamp(received_at, "received_at"),
            record={
                "actor": actor,
                "event_id": event_id,
                "idempotency_key": idem_key,
                "payload": try_json(payload_json),
                "status": status,
                "task_id": task_id,
            },
        )


def collect_evidence(tag: str, conn: sqlite3.Connection) -> Iterator[FeedEvent]:
    """evidence: ``verification_events`` in a Hermes profile evidence DB (ISO text)."""
    for (
        event_id,
        created_at,
        session_id,
        kind,
        status,
        exit_code,
        command,
    ) in conn.execute(
        "SELECT id, created_at, session_id, kind, status, exit_code, command"
        " FROM verification_events"
    ):
        yield FeedEvent(
            source=tag,
            seq=event_id,
            ts_epoch=parse_timestamp(created_at, "created_at"),
            record={
                "command": command,
                "event_id": event_id,
                "exit_code": exit_code,
                "kind": kind,
                "session_id": session_id,
                "status": status,
            },
        )


# ── Core aggregation ───────────────────────────────────────────────


def _run_source(
    tag: str, path: Path | None, collector: Collector
) -> tuple[SourceResult, list[FeedEvent]]:
    """Read one source, never writing to it.

    Missing path or missing table -> status "missing". Unreadable/corrupt
    file or other DB failure -> status "error". A row with an unparseable
    timestamp is dropped (counted in ``dropped``) rather than failing the
    whole source.
    """
    result = SourceResult(tag=tag)
    if path is None or not path.exists():
        result.status = SOURCE_STATUS_MISSING
        return result, []
    try:
        conn = open_read_only(path)
    except sqlite3.Error as exc:
        result.status = SOURCE_STATUS_ERROR
        result.error = str(exc)
        return result, []
    events: list[FeedEvent] = []
    try:
        gen = collector(tag, conn)
        while True:
            try:
                events.append(next(gen))
            except StopIteration:
                break
            except ValueError as exc:
                result.dropped += 1
                if result.error is None:
                    result.error = f"dropped first bad row: {exc}"
    except sqlite3.Error as exc:
        message = str(exc)
        events = []
        if "no such table" in message:
            result.status = SOURCE_STATUS_MISSING
        else:
            result.status = SOURCE_STATUS_ERROR
            result.error = message
    finally:
        conn.close()
    result.count = len(events)
    return result, events


def run_specs(specs: list[tuple[str, Path | None, Collector]]) -> AggregateReport:
    """Read every (tag, path, collector) spec and merge the events.

    Events are sorted into total order by (ts_epoch, source tag, seq).
    """
    report = AggregateReport()
    for tag, path, collector in specs:
        result, events = _run_source(tag, path, collector)
        report.sources.append(result)
        report.events.extend(events)
    report.events.sort(key=lambda e: (e.ts_epoch, e.source, e.seq))
    return report


def build_feed_lines(
    report: AggregateReport, generated_at: str
) -> list[dict[str, Any]]:
    """Render the feed: one line per event, manifest last."""
    lines = [event.to_line() for event in report.events]
    lines.append(report.build_manifest(generated_at))
    return lines


def write_feed(lines: list[dict[str, Any]], output: Path | None) -> None:
    """Write feed lines as JSONL. ``output=None`` writes to stdout."""
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines)
    if output is None:
        sys.stdout.write(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


# ── CLI ────────────────────────────────────────────────────────────


def _hermes_root() -> Path:
    """Hermes root: ``$HERMES_ROOT`` -> ``$HERMES_HOME`` -> ``~/.hermes``.

    Kanban workers run with ``HERMES_HOME`` pointing at their profile's
    ``<profile-home>`` directory (see ``prismatic/worker/harness.py``), where
    ``HERMES_ROOT`` holds the real root (``~/.hermes``). ``Path.home()``
    alone is not trustworthy in that context (HOME may also be the profile
    home), so the env vars win and the plain home is only the last resort.
    """
    for env_key in ("HERMES_ROOT", "HERMES_HOME"):
        env = os.environ.get(env_key)
        if env:
            return Path(env)
    return Path.home() / ".hermes"


def _hermes_profiles_root() -> Path:
    """Profiles root: ``$HERMES_PROFILES_DIR`` (explicit) or ``<hermes>/profiles``."""
    env = os.environ.get("HERMES_PROFILES_DIR")
    if env:
        return Path(env)
    return _hermes_root() / "profiles"


def resolve_source_paths(
    kanban_db: Path | None, bus_db: Path | None, evidence_root: Path | None
) -> tuple[Path, Path, Path]:
    """Resolve source paths: explicit flags win, then env, then HERMES_HOME defaults.

    Env precedence per source:

    - kanban:   ``PRISMATIC_KANBAN_DB`` -> ``HERMES_KANBAN_DB`` -> ``<hermes>/kanban.db``
    - bus:      ``PRISMATIC_BUS_DB`` -> ``<PRISMATIC_HOME|~>/.prismatic/bus/event_log.sqlite``
                (matching ``prismatic/gateway/event_bus.py``)
    - evidence: ``PRISMATIC_EVIDENCE_ROOT`` -> ``HERMES_PROFILES_DIR`` -> ``<hermes>/profiles``

    where ``<hermes>`` = ``HERMES_ROOT`` -> ``HERMES_HOME`` -> ``~/.hermes``.
    Workers see the real board because the dispatcher sets ``HERMES_ROOT``
    (and ``HERMES_KANBAN_DB``) in their environment. The evidence glob
    ``*/verification_evidence.db`` under the profiles root yields one source
    tag per profile (``evidence:<profile>``).
    """
    kanban = kanban_db
    if kanban is None:
        for env_key in ("PRISMATIC_KANBAN_DB", "HERMES_KANBAN_DB"):
            env = os.environ.get(env_key)
            if env:
                kanban = Path(env)
                break
        if kanban is None:
            kanban = _hermes_root() / "kanban.db"
    bus = bus_db
    if bus is None:
        env = os.environ.get("PRISMATIC_BUS_DB")
        if env:
            bus = Path(env)
        else:
            # Match prismatic/gateway/event_bus.py: $PRISMATIC_HOME-relative,
            # falling back to the user home.
            home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
            bus = Path(home) / ".prismatic" / "bus" / "event_log.sqlite"
    root = evidence_root
    if root is None:
        env = os.environ.get("PRISMATIC_EVIDENCE_ROOT") or os.environ.get(
            "HERMES_PROFILES_DIR"
        )
        if env:
            root = Path(env)
        else:
            root = _hermes_profiles_root()
    return kanban, bus, root


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic-eventlog",
        description="Read-only cross-system timeline aggregator (gap G2, Phase 3).",
    )
    parser.add_argument(
        "--kanban-db",
        type=Path,
        default=None,
        help="Hermes kanban SQLite DB (default: $PRISMATIC_KANBAN_DB, then "
        "$HERMES_HOME/kanban.db, then ~/.hermes/kanban.db)",
    )
    parser.add_argument(
        "--bus-db",
        type=Path,
        default=None,
        help="Prismatic EventBus store (events + task_admissions; default: $PRISMATIC_BUS_DB, "
        "then ~/.prismatic/bus/event_log.sqlite)",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Hermes profiles root; each */verification_evidence.db is one source (default: "
        "$PRISMATIC_EVIDENCE_ROOT, then $HERMES_PROFILES_DIR, then $HERMES_HOME/profiles, "
        "then ~/.hermes/profiles)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path (default: stdout)",
    )
    return parser


def _evidence_specs(root: Path) -> list[tuple[str, Path | None, Collector]]:
    """One (evidence:<profile>, db) spec per profile evidence DB found."""
    if not root.is_dir():
        return [("evidence", None, collect_evidence)]
    dbs = sorted(root.glob("*/verification_evidence.db"))
    if not dbs:
        return [("evidence", None, collect_evidence)]
    return [(f"evidence:{db.parent.name}", db, collect_evidence) for db in dbs]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 (ok/missing) or 2 (a source errored)."""
    args = build_arg_parser().parse_args(argv)
    kanban_db, bus_db, evidence_root = resolve_source_paths(
        args.kanban_db, args.bus_db, args.evidence_root
    )
    specs: list[tuple[str, Path | None, Collector]] = [
        ("kanban", kanban_db, collect_kanban),
        ("event_bus", bus_db, collect_event_bus),
        ("admissions", bus_db, collect_admissions),
    ]
    specs.extend(_evidence_specs(evidence_root))
    report = run_specs(specs)
    generated_at = iso_utc(datetime.now(timezone.utc).timestamp())
    write_feed(build_feed_lines(report, generated_at), args.output)
    return 2 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
