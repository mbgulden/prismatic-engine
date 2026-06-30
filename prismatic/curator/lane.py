"""
prismatic.curator.lane — the Curator Lane (Epic 1.4 of GRO-3022).

Consumes every event from the SQLite event bus, tags it per the taxonomy
defined in SPEC.md §4, persists the tag to ~/.prismatic/curator/state.sqlite,
and (when invoked) emits the daily digest.

Usage:
    python3 -m prismatic.curator.lane                    # run continuously
    python3 -m prismatic.curator.lane --once            # drain queue + exit
    python3 -m prismatic.curator.lane --emit-digest     # emit today's digest + exit
    python3 -m prismatic.curator.lane --digest-date 2026-06-29  # emit specific date
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# === Paths ===

PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
BUS_DB = Path(os.environ.get("PRISMATIC_BUS_DB") or str(PRISMATIC_HOME / ".prismatic/bus/event_log.sqlite"))
CURATOR_DB = Path(os.environ.get("PRISMATIC_CURATOR_DB") or str(PRISMATIC_HOME / ".prismatic/curator/state.sqlite"))
DIGEST_DIR = Path(os.environ.get("PRISMATIC_DIGEST_DIR") or str(PRISMATIC_HOME / ".prismatic/curator/digests"))
DIGEST_HOUR = int(os.environ.get("PRISMATIC_DIGEST_HOUR", "8"))

for path in (CURATOR_DB.parent, DIGEST_DIR):
    path.mkdir(parents=True, exist_ok=True)

# === Bus event reader ===

@dataclass
class BusEvent:
    """An event from the bus (read-only)."""
    rowid: int
    topic: str
    payload: dict
    ts: float
    source: str | None = None  # parsed from payload if available

    @classmethod
    def from_row(cls, row: tuple) -> "BusEvent":
        rowid, topic, payload_json, ts = row
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {"_raw": payload_json[:200]}
        source = payload.get("source") if isinstance(payload, dict) else None
        if not source and isinstance(payload, dict):
            source = payload.get("payload", {}).get("source")
        return cls(rowid=rowid, topic=topic, payload=payload, ts=ts, source=source)


def fetch_bus_events_after(last_rowid: int, limit: int = 100) -> list[BusEvent]:
    """Return bus events with rowid > last_rowid, up to limit."""
    if not BUS_DB.exists():
        return []
    conn = sqlite3.connect(BUS_DB, timeout=5)
    try:
        cur = conn.execute(
            "SELECT rowid, topic, payload_json, ts FROM events "
            "WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
            (last_rowid, limit),
        )
        return [BusEvent.from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


# === Tag rules (per SPEC.md §4) ===

@dataclass
class TagResult:
    tag: str  # auto-pick | delegate | escalate | drop
    lane_hint: str | None = None
    reason: str = ""


def _labels_from_payload(payload: dict) -> set[str]:
    """Extract label names from a Linear-style payload."""
    out = set()
    labels = payload.get("payload", {}).get("data", {}).get("labels", [])
    if not labels and isinstance(payload.get("data"), dict):
        labels = payload["data"].get("labels", [])
    for lbl in labels:
        if isinstance(lbl, dict):
            name = lbl.get("name")
        else:
            name = str(lbl)
        if name:
            out.add(name)
    return out


def tag_event(event: BusEvent) -> TagResult:
    """Decide which bucket this event belongs in. Per SPEC.md §4."""
    src = (event.source or "").lower()
    topic = (event.topic or "").lower()
    payload = event.payload or {}

    # Internal self-monitoring events → drop
    if topic in ("agent.heartbeat", "digest.scheduled"):
        return TagResult("drop", reason="self-monitoring event")

    # Agent completed = informational → auto-pick
    if "agent_completed" in topic or "agent.completed" in topic:
        return TagResult("auto-pick", reason="informational agent result")

    # Agent failed = real problem → escalate
    if "agent_failed" in topic or "agent.failed" in topic:
        return TagResult("escalate", reason="agent reported failure")

    # Budget exceeded = hard cap hit → escalate
    if "budget" in topic and "exceeded" in topic:
        return TagResult("escalate", reason="hard budget cap hit")

    # Webhook delivery failures → escalate
    if "delivery.failed" in topic or "webhook.delivery.failed" in topic:
        return TagResult("escalate", reason="webhook delivery failure")

    # Circuit breaker tripped → escalate (real infra issue)
    if "circuit_breaker" in topic or "circuit_breaker_trip" in topic:
        return TagResult("escalate", reason="circuit breaker tripped")

    # Agent launched (dispatcher success) → auto-pick
    if "agent_launched" in topic:
        # lane_hint from source if it looks like "dispatcher:codex"
        lane_hint = None
        if src.startswith("dispatcher:"):
            lane_hint = src.split(":", 1)[1]
        return TagResult("auto-pick", lane_hint=lane_hint,
                         reason="dispatcher launched agent")

    # Linear events (source="linear" or topic implies it)
    if src == "linear" or (not src and "issue" in topic):
        # Extract action from either direct payload or nested payload
        action = (payload.get("action")
                  or (payload.get("payload") or {}).get("action")
                  or "").lower()
        labels = _labels_from_payload(payload)

        # Comment events = conversation noise → drop
        ev_type = payload.get("type") or (payload.get("payload") or {}).get("type") or ""
        if "Comment" in str(ev_type) or "comment" in topic:
            return TagResult("drop", reason="comment noise")

        # Issue remove/close → auto-pick (lifecycle)
        if action in ("remove", "close"):
            return TagResult("auto-pick", reason="lifecycle event")

        # New issue → delegate (needs lane assignment)
        if action == "create":
            return TagResult("delegate", lane_hint="triage",
                             reason="new Linear issue")

        # Issue update with dispatch:ready label → delegate
        if action == "update" and "dispatch:ready" in labels:
            return TagResult("delegate",
                             reason="dispatch:ready label applied")

        # Issue update otherwise → auto-pick
        if action == "update":
            return TagResult("auto-pick", reason="routine status update")

    # GitHub events
    if src == "github":
        if topic == "ping" or payload.get("zen"):
            return TagResult("drop", reason="GitHub ping/test event")
        return TagResult("auto-pick", reason="github informational event")

    # Dispatcher / supervisor events (compound source like "dispatcher:fred")
    if src.startswith("dispatcher:"):
        lane_hint = src.split(":", 1)[1] if ":" in src else None
        # Already handled agent_launched above; catch-all here for other dispatcher events
        return TagResult("auto-pick", lane_hint=lane_hint,
                         reason="dispatcher event")

    # Distributed watchdog events
    if src.startswith("distributed_watchdog") or "watchdog" in src:
        if "timeout" in topic:
            return TagResult("escalate", reason="watchdog timeout")
        return TagResult("auto-pick", reason="watchdog status event")

    # Webhook source (generic)
    if src == "webhook":
        return TagResult("auto-pick", reason="generic webhook delivery")

    # Default: unknown event type, escalate so it gets human attention
    return TagResult("escalate", reason=f"unmatched source={src!r} topic={topic!r}")


# === State store ===

def init_curator_db() -> None:
    """Create curator tables if they don't exist."""
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tagged_events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                event_rowid INTEGER NOT NULL,
                tag TEXT NOT NULL CHECK(tag IN ('auto-pick','delegate','escalate','drop')),
                lane_hint TEXT,
                tagged_at REAL NOT NULL,
                reason TEXT
            );
            CREATE TABLE IF NOT EXISTS lane_stats (
                lane TEXT PRIMARY KEY,
                count_total INTEGER DEFAULT 0,
                count_delegate INTEGER DEFAULT 0,
                count_escalate INTEGER DEFAULT 0,
                count_drop INTEGER DEFAULT 0,
                count_auto_pick INTEGER DEFAULT 0,
                last_seen_ts REAL,
                p95_tag_latency_ms REAL
            );
            CREATE TABLE IF NOT EXISTS digest_runs (
                date TEXT PRIMARY KEY,
                ran_at REAL NOT NULL,
                auto_pick_count INTEGER,
                delegate_count INTEGER,
                escalate_count INTEGER,
                drop_count INTEGER,
                paged_michael INTEGER DEFAULT 0,
                digest_path TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tagged_event_rowid
                ON tagged_events(event_rowid);
            CREATE INDEX IF NOT EXISTS idx_tagged_tag
                ON tagged_events(tag);
        """)
        conn.commit()
    finally:
        conn.close()


def already_tagged(event_rowid: int) -> bool:
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        cur = conn.execute(
            "SELECT 1 FROM tagged_events WHERE event_rowid = ? LIMIT 1",
            (event_rowid,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def persist_tag(event_rowid: int, tag: str, lane_hint: str | None, reason: str) -> None:
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        conn.execute(
            "INSERT INTO tagged_events (event_rowid, tag, lane_hint, tagged_at, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_rowid, tag, lane_hint, time.time(), reason),
        )
        conn.commit()
    finally:
        conn.close()


def update_lane_stats(lane: str, tag: str) -> None:
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        # Upsert
        conn.execute(
            "INSERT INTO lane_stats (lane, count_total, last_seen_ts) VALUES (?, 1, ?) "
            "ON CONFLICT(lane) DO UPDATE SET "
            "  count_total = count_total + 1, "
            "  last_seen_ts = ?",
            (lane, time.time(), time.time()),
        )
        if tag == "delegate":
            conn.execute(
                "UPDATE lane_stats SET count_delegate = count_delegate + 1 WHERE lane = ?",
                (lane,),
            )
        elif tag == "escalate":
            conn.execute(
                "UPDATE lane_stats SET count_escalate = count_escalate + 1 WHERE lane = ?",
                (lane,),
            )
        elif tag == "drop":
            conn.execute(
                "UPDATE lane_stats SET count_drop = count_drop + 1 WHERE lane = ?",
                (lane,),
            )
        elif tag == "auto-pick":
            conn.execute(
                "UPDATE lane_stats SET count_auto_pick = count_auto_pick + 1 WHERE lane = ?",
                (lane,),
            )
        conn.commit()
    finally:
        conn.close()


def get_last_processed_rowid() -> int:
    """Use the max tagged_events.event_rowid as cursor (monotonic)."""
    if not CURATOR_DB.exists():
        return 0
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        cur = conn.execute("SELECT COALESCE(MAX(event_rowid), 0) FROM tagged_events")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


# === Digest rendering ===

def render_digest(target_date: str | None = None) -> tuple[str, dict]:
    """Build the Markdown digest for a given local date (default: today).
    Returns (markdown_text, counts_dict).
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        # All tags from the last 24 hours ending at the digest hour
        # Approximate: just get tags from today (since digests run at 8am,
        # "today" means the last day since the previous digest).
        # For simplicity, query by tagged_at >= target_date midnight.
        day_start = datetime.strptime(target_date, "%Y-%m-%d").replace(
            hour=DIGEST_HOUR, minute=0, second=0
        ).timestamp()
        day_end = day_start + 86400

        cur = conn.execute(
            "SELECT tag, COUNT(*) FROM tagged_events "
            "WHERE tagged_at >= ? AND tagged_at < ? GROUP BY tag",
            (day_start, day_end),
        )
        counts = {"auto-pick": 0, "delegate": 0, "escalate": 0, "drop": 0}
        for tag, n in cur.fetchall():
            if tag in counts:
                counts[tag] = n
        total = sum(counts.values())

        # Top escalations (last 10)
        cur = conn.execute(
            "SELECT event_rowid, lane_hint, reason, tagged_at FROM tagged_events "
            "WHERE tag = 'escalate' AND tagged_at >= ? AND tagged_at < ? "
            "ORDER BY tagged_at DESC LIMIT 10",
            (day_start, day_end),
        )
        escalations = cur.fetchall()

        # Lane stats (snapshot of all-time)
        cur = conn.execute(
            "SELECT lane, count_total, count_delegate, count_escalate, count_drop, "
            "       count_auto_pick, last_seen_ts FROM lane_stats ORDER BY lane"
        )
        lane_rows = cur.fetchall()
    finally:
        conn.close()

    lines = [
        f"# Curator Digest — {target_date}",
        "",
        f"Generated: {datetime.now().strftime('%H:%M:%S')} local",
        f"Window: {datetime.fromtimestamp(day_start).strftime('%Y-%m-%d %H:%M')} "
        f"to {datetime.fromtimestamp(day_end).strftime('%Y-%m-%d %H:%M')}",
        f"Bus events processed: {total}",
        "",
        "## Counts",
        "",
        "| Tag | Count |",
        "|---|---|",
        f"| auto-pick | {counts['auto-pick']} |",
        f"| delegate | {counts['delegate']} |",
        f"| escalate | {counts['escalate']} |",
        f"| drop | {counts['drop']} |",
        "",
    ]

    if escalations:
        lines.append("## Escalations (needs your attention)")
        lines.append("")
        for ev_rowid, lane_hint, reason, tagged_at in escalations:
            when = datetime.fromtimestamp(tagged_at).strftime("%H:%M:%S")
            lines.append(f"- **{when}** — bus event {ev_rowid}: {reason}")
            if lane_hint:
                lines.append(f"  - lane hint: {lane_hint}")
        lines.append("")
    else:
        lines.append("## Escalations")
        lines.append("")
        lines.append("None. Quiet day.")
        lines.append("")

    if lane_rows:
        lines.append("## Lane stats (all-time)")
        lines.append("")
        lines.append("| Lane | Total | delegate | escalate | drop | auto-pick | Last seen |")
        lines.append("|---|---|---|---|---|---|---|")
        for lane, total_n, dn, en, dn2, apn, last_seen in lane_rows:
            when = (datetime.fromtimestamp(last_seen).strftime("%Y-%m-%d %H:%M:%S")
                     if last_seen else "never")
            lines.append(f"| {lane} | {total_n} | {dn} | {en} | {dn2} | {apn} | {when} |")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by prismatic.curator.lane at {datetime.now().isoformat()}*")
    return "\n".join(lines), counts


def write_digest(markdown: str, target_date: str | None = None) -> Path:
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    path = DIGEST_DIR / f"{target_date}.md"
    path.write_text(markdown)
    return path


def record_digest_run(target_date: str, counts: dict, path: Path) -> None:
    """Record that the digest for target_date was emitted."""
    paged = 1 if counts.get("escalate", 0) > 0 else 0
    conn = sqlite3.connect(CURATOR_DB, timeout=5)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO digest_runs "
            "(date, ran_at, auto_pick_count, delegate_count, escalate_count, "
            " drop_count, paged_michael, digest_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (target_date, time.time(),
             counts.get("auto-pick", 0), counts.get("delegate", 0),
             counts.get("escalate", 0), counts.get("drop", 0),
             paged, str(path)),
        )
        conn.commit()
    finally:
        conn.close()


# === Main curator loop ===

class CuratorLane:
    """Main curator supervisor. One instance, runs continuously."""

    def __init__(self, poll_interval: float = 3.0):
        self.poll_interval = poll_interval
        self._last_rowid = get_last_processed_rowid()
        init_curator_db()

    def tick(self) -> int:
        """Process one batch of bus events. Returns count tagged."""
        events = fetch_bus_events_after(self._last_rowid)
        tagged = 0
        for ev in events:
            if already_tagged(ev.rowid):
                continue  # idempotent
            result = tag_event(ev)
            persist_tag(ev.rowid, result.tag, result.lane_hint, result.reason)
            if result.lane_hint:
                update_lane_stats(result.lane_hint, result.tag)
            self._last_rowid = ev.rowid
            tagged += 1
        return tagged

    async def run(self) -> None:
        """Main loop. Polls bus, tags events, persists."""
        print(f"[curator] starting, last_rowid={self._last_rowid}")
        while True:
            try:
                n = self.tick()
                if n:
                    print(f"[curator] tagged {n} events, cursor={self._last_rowid}")
            except Exception as e:
                print(f"[curator] error: {e}")
            await asyncio.sleep(self.poll_interval)

    def emit_daily_digest(self, target_date: str | None = None) -> Path:
        """Render + write + record digest for target_date."""
        markdown, counts = render_digest(target_date)
        path = write_digest(markdown, target_date)
        record_digest_run(target_date or datetime.now().strftime("%Y-%m-%d"),
                          counts, path)
        return path


def main():
    ap = argparse.ArgumentParser(description="Prismatic Curator Lane")
    ap.add_argument("--once", action="store_true",
                    help="Drain queue once and exit (don't run continuously)")
    ap.add_argument("--emit-digest", action="store_true",
                    help="Emit today's digest and exit")
    ap.add_argument("--digest-date", type=str, default=None,
                    help="Emit digest for a specific date (YYYY-MM-DD)")
    ap.add_argument("--poll-interval", type=float, default=3.0,
                    help="Poll interval in seconds (default 3)")
    args = ap.parse_args()

    if args.emit_digest or args.digest_date:
        lane = CuratorLane()
        path = lane.emit_daily_digest(args.digest_date)
        print(f"[curator] digest emitted: {path}")
        return

    lane = CuratorLane(poll_interval=args.poll_interval)

    if args.once:
        n = lane.tick()
        print(f"[curator] tagged {n} events, cursor={lane._last_rowid}")
        return

    # Continuous mode
    asyncio.run(lane.run())


if __name__ == "__main__":
    main()