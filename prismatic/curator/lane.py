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

# Make sibling modules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.supervisor.recovery import (  # noqa: E402
    get_pool, dispatch_to_supervisor_bounded,
)
from prismatic.curator.dispatcher import (  # noqa: E402
    LaneBudgetTracker, decide_dispatch, build_supervisor_cmd,
)

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


def _event_inner_payload(payload: dict) -> dict:
    """Return EventBus-wrapped payload.payload when present, else payload."""
    inner = payload.get("payload") if isinstance(payload, dict) else None
    return inner if isinstance(inner, dict) else payload


def _event_action(payload: dict) -> str:
    inner = _event_inner_payload(payload) or {}
    return str(inner.get("action") or payload.get("action") or "").lower()


def _github_handoff_id(topic: str, payload: dict) -> str:
    """Stable identifier for GitHub PR delegate handoff."""
    inner = _event_inner_payload(payload) or {}
    pr = inner.get("pull_request") if isinstance(inner.get("pull_request"), dict) else {}
    repo = inner.get("repository") if isinstance(inner.get("repository"), dict) else {}
    html_url = pr.get("html_url")
    if html_url:
        return str(html_url)
    full_name = repo.get("full_name")
    number = inner.get("number") or pr.get("number")
    if full_name and number:
        return f"{full_name}#{number}"
    return topic


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

    # Agent failed = real problem → escalate, preserving lane when available.
    if "agent_failed" in topic or "agent.failed" in topic:
        lane_hint = None
        if src.startswith("dispatcher:"):
            lane_hint = src.split(":", 1)[1]
        elif isinstance(payload.get("lane"), str):
            lane_hint = payload["lane"]
        elif isinstance(payload.get("agent"), str):
            lane_hint = payload["agent"]
        return TagResult("escalate", lane_hint=lane_hint,
                         reason="agent reported failure")

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

    # Webhook control events. Handle these before Linear/GitHub source rules so
    # auth failures cannot be downgraded to routine webhook noise.
    if src == "webhook" or "webhook" in topic or "webhook" in src:
        if "auth_failed" in topic:
            return TagResult("escalate", reason="webhook hmac auth failure")
        if "ping" in topic:
            return TagResult("drop", reason="webhook ping")

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
        github_payload = _event_inner_payload(payload)
        if topic == "ping" or github_payload.get("zen") or payload.get("zen"):
            return TagResult("drop", reason="GitHub ping/test event")
        if topic == "pull_request":
            action = _event_action(payload)
            if action in ("opened", "reopened", "synchronize"):
                return TagResult("delegate", lane_hint="jules",
                                 reason=f"GitHub PR {action}")
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

    # Final fallback for generic webhooks that were not Linear/GitHub events.
    if src == "webhook" or "webhook" in topic or "webhook" in src:
        return TagResult("auto-pick", reason="generic webhook delivery")

    # Default: unknown event type, escalate so it gets human attention
    return TagResult("escalate", reason=f"unmatched source={src!r} topic={topic!r}")


# === State store ===

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    """Add a column if it is missing, preserving existing SQLite files."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


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
                reason TEXT,
                dispatched INTEGER DEFAULT 0,
                dispatched_at REAL
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
        _ensure_column(conn, "tagged_events", "dispatched", "INTEGER DEFAULT 0")
        _ensure_column(conn, "tagged_events", "dispatched_at", "REAL")
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

    def __init__(self, poll_interval: float = 3.0,
                 enable_dispatch: bool = True):
        self.poll_interval = poll_interval
        self._last_rowid = get_last_processed_rowid()
        self.enable_dispatch = enable_dispatch
        self._budget = LaneBudgetTracker()
        self._pool = get_pool()
        init_curator_db()

    def tick_tagging(self) -> int:
        """Stream 1: tag new bus events without blocking on task execution."""
        # Reap zombie supervisors from prior dispatches (Story 1.2 fix).
        # Synchronous — fast WNOHANG waitpid on tracked PIDs.
        self._pool._reap_zombies()
        events = fetch_bus_events_after(self._last_rowid)
        tagged = 0
        for ev in events:
            if already_tagged(ev.rowid):
                self._last_rowid = ev.rowid
                continue  # idempotent
            result = tag_event(ev)
            persist_tag(ev.rowid, result.tag, result.lane_hint, result.reason)
            if result.lane_hint:
                update_lane_stats(result.lane_hint, result.tag)
            self._last_rowid = ev.rowid
            tagged += 1
        return tagged

    def _fetch_pending_dispatches(self, limit: int = 50) -> list[tuple[int, int, str | None, str, str]]:
        """Fetch durable delegate tags that have not been handed to supervisor."""
        if not CURATOR_DB.exists() or not BUS_DB.exists():
            return []

        with sqlite3.connect(CURATOR_DB, timeout=5) as curator_conn:
            rows = curator_conn.execute(
                """
                SELECT rowid, event_rowid, lane_hint
                FROM tagged_events
                WHERE tag = 'delegate' AND COALESCE(dispatched, 0) = 0
                ORDER BY rowid ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        if not rows:
            return []

        pending: list[tuple[int, int, str | None, str, str]] = []
        with sqlite3.connect(BUS_DB, timeout=5) as bus_conn:
            for rowid, event_rowid, lane_hint in rows:
                bus_row = bus_conn.execute(
                    "SELECT topic, payload_json FROM events WHERE rowid = ?",
                    (event_rowid,),
                ).fetchone()
                if not bus_row:
                    continue
                topic, payload_json = bus_row
                pending.append((int(rowid), int(event_rowid), lane_hint, topic, payload_json))
        return pending

    def _mark_dispatched(self, tagged_rowid: int) -> None:
        with sqlite3.connect(CURATOR_DB, timeout=5) as conn:
            conn.execute(
                "UPDATE tagged_events SET dispatched = 1, dispatched_at = ? WHERE rowid = ?",
                (time.time(), tagged_rowid),
            )
            conn.commit()

    def tick_dispatch(self) -> int:
        """Stream 2: hand pending delegate tags to supervisor. Returns count."""
        if not self.enable_dispatch:
            return 0
        dispatched = 0
        for tagged_rowid, _event_rowid, lane_hint, topic, payload_json in self._fetch_pending_dispatches():
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {}

            issue_id = None
            source = str(payload.get("source") or "").lower()
            if source == "github":
                issue_id = _github_handoff_id(topic, payload)
            if not issue_id and isinstance(payload.get("data"), dict):
                issue_id = payload["data"].get("identifier")
            if not issue_id and isinstance(payload.get("payload"), dict):
                data = payload["payload"].get("data")
                if isinstance(data, dict):
                    issue_id = data.get("identifier")
            if not issue_id:
                issue_id = topic

            decision = decide_dispatch(lane_hint, budget_tracker=self._budget)
            if not decision.should_dispatch or not decision.lane:
                print(f"[curator] dispatch pending for {issue_id}: {decision.reason}")
                continue

            cmd = build_supervisor_cmd(issue_id, decision.lane, decision.model)
            result = dispatch_to_supervisor_bounded(issue_id, cmd)
            if result.get("status") in {"spawned", "queued"}:
                if result.get("status") == "spawned":
                    self._budget.charge(decision.lane)
                try:
                    from prismatic.telemetry import get_collector
                    get_collector().record_credit(
                        run_id=f"dispatch-{issue_id}-{int(datetime.now(timezone.utc).timestamp())}",
                        agent=f"agent:{decision.lane}",
                        provider="prismatic-dispatcher",
                        credits_spent=0,
                        model=decision.model,
                        operation="dispatch",
                    )
                except Exception as e:
                    print(f"[curator] Failed to record dispatch telemetry for {issue_id}: {e}")
                self._mark_dispatched(tagged_rowid)
                dispatched += 1
                status_msg = f"PID={result.get('pid')}" if result.get("pid") else "queued"
                print(f"[curator] dispatched {issue_id} -> {decision.lane}/{decision.model} ({status_msg})")
            else:
                print(f"[curator] dispatch failed for {issue_id}: {result.get('reason', 'unknown')}")
        return dispatched

    def tick(self) -> int:
        """Compatibility one-shot: tag events, then dispatch pending delegate tags."""
        tagged = self.tick_tagging()
        self.tick_dispatch()
        return tagged

    async def run(self) -> None:
        """Run tagging and dispatch streams concurrently."""
        print(f"[curator] starting streams, last_rowid={self._last_rowid}")
        await asyncio.gather(self._tagging_loop(), self._dispatch_loop())

    async def _tagging_loop(self) -> None:
        print("[curator] stream 1 (tagging) active")
        while True:
            try:
                n = self.tick_tagging()
                if n:
                    print(f"[curator] tagged {n} events, cursor={self._last_rowid}")
            except Exception as e:
                print(f"[curator] tagging error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _dispatch_loop(self) -> None:
        if not self.enable_dispatch:
            print("[curator] stream 2 (dispatch) disabled")
            return
        print("[curator] stream 2 (dispatch) active")
        while True:
            try:
                n = self.tick_dispatch()
                if n:
                    print(f"[curator] dispatched {n} delegate tasks")
            except Exception as e:
                print(f"[curator] dispatch error: {e}")
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
        n_tags = lane.tick_tagging()
        n_dispatch = lane.tick_dispatch()
        print(f"[curator] tagged {n_tags} events, dispatched {n_dispatch} tasks, cursor={lane._last_rowid}")
        return

    # Continuous mode
    asyncio.run(lane.run())


if __name__ == "__main__":
    main()