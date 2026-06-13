"""
prismatic/telemetry.py — Non-blocking Telemetry Collector

Zero-dependency observability layer. All writes go through a queue.Queue
and are processed by a single daemon thread. Telemetry NEVER blocks the
dispatch loop.

Database: SQLite tables in the existing event_router.db.
"""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Default paths ──────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# ── Circuit breaker thresholds (env-overridable) ──────────
BREAKER_MICRO_MAX = int(os.environ.get("PRISMATIC_BREAKER_MICRO_MAX", "5"))
BREAKER_MACRO_MAX = int(os.environ.get("PRISMATIC_BREAKER_MACRO_MAX", "3"))


class TelemetryCollector:
    """Non-blocking telemetry collector.

    All record_* methods push events onto a queue. A single daemon
    thread drains the queue and writes to SQLite. If the queue is
    full (10,000 items), writes are silently dropped.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(
            maxsize=10000
        )
        self._running = True
        self._writer = threading.Thread(target=self._drain, daemon=True)
        self._writer.start()
        self._ensure_tables()

    # ── Public API ──────────────────────────────────────────

    def record_loop(
        self,
        run_id: str,
        issue_id: str,
        agent: str,
        loop_type: str,
        trigger: str | None = None,
        resolved: bool = False,
        depth: int = 0,
        parent_id: str | None = None,
    ) -> None:
        """Record a loop event (micro-review, macro-handoff, circuit-breaker)."""
        event = {
            "run_id": run_id,
            "issue_id": issue_id,
            "agent": agent,
            "loop_type": loop_type,
            "trigger": trigger,
            "resolved": 1 if resolved else 0,
            "depth": depth,
            "parent_id": parent_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._push("loop", event)

    def record_tokens(
        self,
        run_id: str,
        agent: str,
        provider: str,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ttft_ms: float = 0.0,
        tps: float = 0.0,
        context_pct: float = 0.0,
        vram_mb: int = 0,
    ) -> None:
        """Record token metrics for an agent run."""
        event = {
            "run_id": run_id,
            "agent": agent,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "ttft_ms": ttft_ms,
            "tps": tps,
            "context_pct": context_pct,
            "vram_mb": vram_mb,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._push("tokens", event)

    def record_validation(
        self,
        run_id: str,
        agent: str,
        event_type: str,
        total: int = 0,
        passed: int = 0,
        failed: int = 0,
        sandbox_id: str | None = None,
        rollback: bool = False,
        watch_sec: float = 0.0,
    ) -> None:
        """Record a validation event (test run, compile, lint, rollback)."""
        event = {
            "run_id": run_id,
            "agent": agent,
            "event_type": event_type,
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "sandbox_id": sandbox_id,
            "rollback": 1 if rollback else 0,
            "watch_sec": watch_sec,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._push("validation", event)

    def check_circuit(
        self, issue_id: str, agent: str, micro_count: int, macro_count: int = 0
    ) -> bool:
        """Check and update circuit breaker. Returns True if tripped.

        Call this from recover_stalled_agy() or any stall detection loop.
        When the breaker trips, the caller should pause dispatch and alert.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "SELECT micro_count, macro_count, tripped "
                "FROM telemetry_circuit_breakers WHERE issue_id = ?",
                (issue_id,),
            )
            row = cursor.fetchone()

            prev_micro = row[0] if row else 0
            prev_macro = row[1] if row else 0
            already_tripped = bool(row[2]) if row else False

            total_micro = prev_micro + micro_count
            total_macro = prev_macro + macro_count

            now = datetime.now(timezone.utc).isoformat()
            tripped = (
                not already_tripped
                and (
                    total_micro >= BREAKER_MICRO_MAX
                    or total_macro >= BREAKER_MACRO_MAX
                )
            )

            conn.execute(
                """INSERT OR REPLACE INTO telemetry_circuit_breakers
                   (issue_id, agent, micro_count, macro_count, last_seen, tripped, tripped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    issue_id,
                    agent,
                    total_micro,
                    total_macro,
                    now,
                    1 if (already_tripped or tripped) else 0,
                    now if tripped else (row[5] if row and row[5] else None),
                ),
            )
            conn.commit()

            if tripped:
                self._push("loop", {
                    "run_id": f"breaker-{issue_id}",
                    "issue_id": issue_id,
                    "agent": agent,
                    "loop_type": "circuit_breaker",
                    "trigger": f"micro={total_micro} macro={total_macro}",
                    "resolved": 0,
                    "depth": 0,
                    "parent_id": None,
                    "created_at": now,
                })

            return tripped
        finally:
            conn.close()

    def reset_breaker(self, issue_id: str) -> None:
        """Reset the circuit breaker for an issue (called after human intervention)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "DELETE FROM telemetry_circuit_breakers WHERE issue_id = ?",
                (issue_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get_dashboard_data(self, hours: int = 24) -> dict[str, Any]:
        """Query recent telemetry for dashboard display."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc).timestamp() - hours * 3600)
        cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        try:
            # Loop stats
            loops = conn.execute(
                "SELECT loop_type, COUNT(*) as cnt FROM telemetry_loop_events "
                "WHERE created_at >= ? GROUP BY loop_type",
                (cutoff_str,),
            ).fetchall()

            # Token stats
            tokens = conn.execute(
                "SELECT agent, SUM(prompt_tokens + completion_tokens) as total_tokens, "
                "AVG(tps) as avg_tps, AVG(context_pct) as avg_context "
                "FROM telemetry_token_metrics WHERE recorded_at >= ? "
                "GROUP BY agent",
                (cutoff_str,),
            ).fetchall()

            # Validation stats
            validation = conn.execute(
                "SELECT SUM(total_tests) as total, SUM(passed) as passed, "
                "SUM(failed) as failed, SUM(rollback) as rollbacks "
                "FROM telemetry_validation_events WHERE created_at >= ?",
                (cutoff_str,),
            ).fetchone()

            # Tripped breakers
            breakers = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry_circuit_breakers "
                "WHERE tripped = 1"
            ).fetchone()

            return {
                "loops": [dict(r) for r in loops],
                "tokens": [dict(r) for r in tokens],
                "validation": dict(validation) if validation else {},
                "breakers_tripped": breakers["cnt"] if breakers else 0,
                "hours": hours,
            }
        finally:
            conn.close()

    # ── Internal ────────────────────────────────────────────

    def _push(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an event onto the queue. Silently drops if full."""
        try:
            self._queue.put_nowait((event_type, data))
        except queue.Full:
            pass  # Telemetry is best-effort

    def _drain(self) -> None:
        """Daemon thread: drain the queue and write to SQLite."""
        conn = sqlite3.connect(self._db_path)
        while self._running:
            try:
                event_type, data = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if event_type == "loop":
                    conn.execute(
                        """INSERT INTO telemetry_loop_events
                           (run_id, issue_id, agent, loop_type, trigger,
                            resolved, depth, parent_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            data["run_id"], data["issue_id"], data["agent"],
                            data["loop_type"], data.get("trigger"),
                            data.get("resolved", 0), data.get("depth", 0),
                            data.get("parent_id"), data["created_at"],
                        ),
                    )
                elif event_type == "tokens":
                    conn.execute(
                        """INSERT INTO telemetry_token_metrics
                           (run_id, agent, provider, model, prompt_tokens,
                            completion_tokens, ttft_ms, tps, context_pct,
                            vram_mb, recorded_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            data["run_id"], data["agent"], data["provider"],
                            data.get("model"), data.get("prompt_tokens", 0),
                            data.get("completion_tokens", 0), data.get("ttft_ms", 0.0),
                            data.get("tps", 0.0), data.get("context_pct", 0.0),
                            data.get("vram_mb", 0), data["recorded_at"],
                        ),
                    )
                elif event_type == "validation":
                    conn.execute(
                        """INSERT INTO telemetry_validation_events
                           (run_id, agent, event_type, total_tests,
                            passed, failed, sandbox_id, rollback,
                            watch_sec, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            data["run_id"], data["agent"], data["event_type"],
                            data.get("total_tests", 0), data.get("passed", 0),
                            data.get("failed", 0), data.get("sandbox_id"),
                            data.get("rollback", 0), data.get("watch_sec", 0.0),
                            data["created_at"],
                        ),
                    )
                conn.commit()
            except Exception:
                pass  # Telemetry failures must never crash the dispatcher

    def _ensure_tables(self) -> None:
        """Idempotent schema migration."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS telemetry_loop_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT NOT NULL,
                    issue_id    TEXT NOT NULL,
                    agent       TEXT NOT NULL,
                    loop_type   TEXT NOT NULL,
                    trigger     TEXT,
                    resolved    INTEGER DEFAULT 0,
                    depth       INTEGER DEFAULT 0,
                    parent_id   TEXT,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_loop_run
                    ON telemetry_loop_events(run_id);
                CREATE INDEX IF NOT EXISTS idx_loop_agent
                    ON telemetry_loop_events(agent, created_at);

                CREATE TABLE IF NOT EXISTS telemetry_circuit_breakers (
                    issue_id    TEXT PRIMARY KEY,
                    agent       TEXT NOT NULL,
                    micro_count INTEGER DEFAULT 0,
                    macro_count INTEGER DEFAULT 0,
                    last_seen   TEXT NOT NULL,
                    tripped     INTEGER DEFAULT 0,
                    tripped_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS telemetry_token_metrics (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    provider        TEXT NOT NULL,
                    model           TEXT,
                    prompt_tokens   INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    ttft_ms         REAL,
                    tps             REAL,
                    context_pct     REAL,
                    vram_mb         INTEGER,
                    recorded_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_token_run
                    ON telemetry_token_metrics(run_id);
                CREATE INDEX IF NOT EXISTS idx_token_agent
                    ON telemetry_token_metrics(agent, recorded_at);

                CREATE TABLE IF NOT EXISTS telemetry_validation_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    event_type      TEXT NOT NULL,
                    total_tests     INTEGER DEFAULT 0,
                    passed          INTEGER DEFAULT 0,
                    failed          INTEGER DEFAULT 0,
                    sandbox_id      TEXT,
                    rollback        INTEGER DEFAULT 0,
                    watch_sec       REAL,
                    created_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_run
                    ON telemetry_validation_events(run_id);
            """)
            conn.commit()
        finally:
            conn.close()


# ── Singleton ───────────────────────────────────────────────
_collector: TelemetryCollector | None = None


def get_collector() -> TelemetryCollector:
    """Get or create the global telemetry collector."""
    global _collector
    if _collector is None:
        _collector = TelemetryCollector()
    return _collector
