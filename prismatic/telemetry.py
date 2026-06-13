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

# ── Alerting thresholds (env-overridable) ─────────────────
ALERT_CREDIT_BURN_RATE = int(os.environ.get("PRISMATIC_ALERT_CREDIT_BURN", "1000"))
"""Credits/hour. Alert when credit burn rate exceeds this."""

ALERT_LOOP_COUNT = int(os.environ.get("PRISMATIC_ALERT_LOOP_COUNT", "5"))
"""Max loop events in 1 hour before alerting."""

ALERT_FAILURE_RATE = float(os.environ.get("PRISMATIC_ALERT_FAILURE_RATE", "0.20"))
"""Fraction (0.0-1.0). Alert when agent run failure rate exceeds this."""

ALERT_WINDOW_HOURS = int(os.environ.get("PRISMATIC_ALERT_WINDOW_HOURS", "1"))
"""Lookback window in hours for alert evaluation."""

# ── Retention periods (env-overridable, in days) ──────────
RETENTION_AGENT_RUNS = int(os.environ.get("PRISMATIC_RETENTION_AGENT_RUNS", "30"))
RETENTION_TOOL_CALLS = int(os.environ.get("PRISMATIC_RETENTION_TOOL_CALLS", "7"))
RETENTION_LOOP_EVENTS = int(os.environ.get("PRISMATIC_RETENTION_LOOP_EVENTS", "90"))
RETENTION_RESOURCE_SNAPSHOTS = int(os.environ.get("PRISMATIC_RETENTION_RESOURCE_SNAPSHOTS", "1"))
RETENTION_CREDIT_LEDGER = int(os.environ.get("PRISMATIC_RETENTION_CREDIT_LEDGER", "90"))


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

    def record_agent_run(
        self,
        run_id: str,
        agent: str,
        issue_id: str,
        provider: str = "",
        model: str | None = None,
        status: str = "dispatched",
        credits_spent: int = 0,
    ) -> None:
        """Record an agent dispatch event.

        Called at dispatch time with status='dispatched'.
        Update later with status='completed'/'failed' when the run finishes.
        """
        event = {
            "run_id": run_id,
            "agent": agent,
            "provider": provider,
            "model": model,
            "issue_id": issue_id,
            "status": status,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "exit_code": None,
            "credits_spent": credits_spent,
            "error_message": None,
        }
        self._push("agent_run", event)

    def update_agent_run(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        credits_spent: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Update an agent run record (completion/failure)."""
        event = {
            "run_id": run_id,
            "status": status,
            "end_time": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "credits_spent": credits_spent,
            "error_message": error_message,
        }
        self._push("agent_run_update", event)

    def record_credit(
        self,
        run_id: str,
        agent: str,
        provider: str,
        credits_spent: int,
        model: str | None = None,
        operation: str = "",
    ) -> None:
        """Record a credit expenditure in the ledger."""
        event = {
            "run_id": run_id,
            "agent": agent,
            "provider": provider,
            "model": model,
            "credits_spent": credits_spent,
            "operation": operation,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._push("credit", event)

    def get_dashboard_data(self, hours: int = 24) -> dict[str, Any]:
        """Query recent telemetry for dashboard display.

        Returns a dict with loops, tokens, validation, breakers_tripped,
        credit_burn_rate, failure_rate, and total_agent_runs.
        """
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

            # Credit burn rate (credits spent in window)
            credit = conn.execute(
                "SELECT COALESCE(SUM(credits_spent), 0) as total_credits "
                "FROM telemetry_credit_ledger WHERE recorded_at >= ?",
                (cutoff_str,),
            ).fetchone()
            total_credits = credit["total_credits"] if credit else 0
            credit_burn_rate = total_credits / max(hours, 1)

            # Agent run stats for failure rate
            agent_runs = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed "
                "FROM telemetry_agent_runs WHERE start_time >= ?",
                (cutoff_str,),
            ).fetchone()
            total_runs = agent_runs["total"] if agent_runs else 0
            failed_runs = agent_runs["failed"] if agent_runs else 0
            failure_rate = (failed_runs / total_runs) if total_runs > 0 else 0.0

            return {
                "loops": [dict(r) for r in loops],
                "tokens": [dict(r) for r in tokens],
                "validation": dict(validation) if validation else {},
                "breakers_tripped": breakers["cnt"] if breakers else 0,
                "hours": hours,
                "credit_burn_rate": round(credit_burn_rate, 1),
                "total_credits": total_credits,
                "failure_rate": round(failure_rate, 4),
                "total_agent_runs": total_runs,
                "failed_agent_runs": failed_runs,
            }
        finally:
            conn.close()

    # ── Alert Engine ────────────────────────────────────────

    def check_alerts(
        self,
        hours: int | None = None,
        linear_api_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate all alert rules against recent telemetry.

        Returns a list of triggered alerts as dicts with:
          rule, current_value, threshold, message, severity

        If linear_api_key is provided, also posts alert comments
        to the affected Linear issues via the dispatcher's event DB.
        """
        window = hours if hours is not None else ALERT_WINDOW_HOURS
        data = self.get_dashboard_data(hours=window)
        alerts: list[dict[str, Any]] = []

        # ── Rule 1: Credit burn rate ────────────────────────
        burn_rate = data.get("credit_burn_rate", 0)
        if burn_rate > ALERT_CREDIT_BURN_RATE:
            alerts.append({
                "rule": "credit_burn",
                "current_value": burn_rate,
                "threshold": ALERT_CREDIT_BURN_RATE,
                "message": (
                    f"Credit burn rate {burn_rate:.0f}/hr exceeds "
                    f"threshold {ALERT_CREDIT_BURN_RATE}/hr "
                    f"(total: {data.get('total_credits', 0)} credits "
                    f"in {window}h)"
                ),
                "severity": "high",
            })

        # ── Rule 2: Loop count ──────────────────────────────
        total_loops = sum(
            r.get("cnt", 0) for r in data.get("loops", [])
        )
        if total_loops > ALERT_LOOP_COUNT:
            loop_detail = ", ".join(
                f"{r.get('loop_type', '?')}={r.get('cnt', 0)}"
                for r in data.get("loops", [])
            )
            alerts.append({
                "rule": "loop_count",
                "current_value": total_loops,
                "threshold": ALERT_LOOP_COUNT,
                "message": (
                    f"Loop count {total_loops} in {window}h exceeds "
                    f"threshold {ALERT_LOOP_COUNT} "
                    f"({loop_detail})"
                ),
                "severity": "high",
            })

        # ── Rule 3: Failure rate ────────────────────────────
        failure_rate = data.get("failure_rate", 0)
        total_runs = data.get("total_agent_runs", 0)
        if total_runs > 0 and failure_rate > ALERT_FAILURE_RATE:
            alerts.append({
                "rule": "failure_rate",
                "current_value": failure_rate,
                "threshold": ALERT_FAILURE_RATE,
                "message": (
                    f"Agent failure rate {failure_rate:.1%} exceeds "
                    f"threshold {ALERT_FAILURE_RATE:.0%} "
                    f"({data.get('failed_agent_runs', 0)}/{total_runs} runs)"
                ),
                "severity": "high",
            })

        # ── Post alerts to Linear if credentials available ──
        if alerts and linear_api_key:
            self._post_alert_comments(alerts, linear_api_key)

        return alerts

    def _post_alert_comments(
        self, alerts: list[dict[str, Any]], api_key: str
    ) -> None:
        """Post alert comments to the most-recently-affected Linear issues.

        For each alert, finds the issue that generated the most related
        events and posts a structured comment. Falls back to posting on
        the first active issue found.
        """
        import subprocess as _sp

        conn = sqlite3.connect(self._db_path)
        try:
            for alert in alerts:
                # Find the most active issue for this alert type
                issue_id = None
                if alert["rule"] == "credit_burn":
                    row = conn.execute(
                        "SELECT run_id FROM telemetry_credit_ledger "
                        "ORDER BY recorded_at DESC LIMIT 1"
                    ).fetchone()
                elif alert["rule"] == "loop_count":
                    row = conn.execute(
                        "SELECT issue_id FROM telemetry_loop_events "
                        "ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                elif alert["rule"] == "failure_rate":
                    row = conn.execute(
                        "SELECT issue_id FROM telemetry_agent_runs "
                        "WHERE status = 'failed' "
                        "ORDER BY start_time DESC LIMIT 1"
                    ).fetchone()
                else:
                    row = None

                issue_id = row[0] if row else None
                if not issue_id:
                    continue

                # Build alert comment
                body = (
                    f"## 🚨 Prismatic Alert: {alert['rule']}\n\n"
                    f"{alert['message']}\n\n"
                    f"| Metric | Value |\n|--------|-------|\n"
                    f"| Rule | `{alert['rule']}` |\n"
                    f"| Severity | **{alert['severity']}** |\n"
                    f"| Current | {alert['current_value']} |\n"
                    f"| Threshold | {alert['threshold']} |\n"
                )

                # Post via Linear API (curl subprocess for reliability)
                payload = json.dumps({
                    "query": (
                        "mutation { commentCreate(input: "
                        f'{{ issueId: "{issue_id}", body: "{body}" }}'
                        ") { success } }"
                    ),
                })
                try:
                    result = _sp.run([
                        "curl", "-s", "-X", "POST",
                        "https://api.linear.app/graphql",
                        "-H", f"Authorization: {api_key}",
                        "-H", "Content-Type: application/json",
                        "-d", payload,
                    ], capture_output=True, text=True, timeout=15)
                    resp = json.loads(result.stdout)
                    ok = resp.get("data", {}).get("commentCreate", {}).get("success")
                    if not ok:
                        print(
                            f"prismatic.telemetry: alert comment failed for "
                            f"{issue_id}: {result.stderr[:200]}",
                            file=__import__("sys").stderr,
                        )
                except Exception as exc:
                    print(
                        f"prismatic.telemetry: alert comment error: {exc}",
                        file=__import__("sys").stderr,
                    )
        finally:
            conn.close()

    def cleanup_expired(self, dry_run: bool = False) -> dict[str, int]:
        """Purge telemetry rows past their retention periods.

        Returns a dict of {table_name: rows_deleted}.

        Retention periods (env-overridable):
          - agent_runs > RETENTION_AGENT_RUNS days (default 30)
          - loop_events > RETENTION_LOOP_EVENTS days (default 90)
          - credit_ledger > RETENTION_CREDIT_LEDGER days (default 90)
          - token_metrics > RETENTION_LOOP_EVENTS days (default 90)
          - validation_events > RETENTION_LOOP_EVENTS days (default 90)
        """
        conn = sqlite3.connect(self._db_path)
        deleted: dict[str, int] = {}

        # Map table → (column, retention_days)
        tables = {
            "telemetry_agent_runs": ("start_time", RETENTION_AGENT_RUNS),
            "telemetry_loop_events": ("created_at", RETENTION_LOOP_EVENTS),
            "telemetry_credit_ledger": ("recorded_at", RETENTION_CREDIT_LEDGER),
            "telemetry_token_metrics": ("recorded_at", RETENTION_LOOP_EVENTS),
            "telemetry_validation_events": ("created_at", RETENTION_LOOP_EVENTS),
        }

        try:
            for table, (col, days) in tables.items():
                cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
                cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

                if dry_run:
                    cursor = conn.execute(
                        f"SELECT COUNT(*) as cnt FROM {table} WHERE {col} < ?",
                        (cutoff_str,),
                    )
                    row = cursor.fetchone()
                    deleted[table] = row[0] if row else 0
                else:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE {col} < ?",
                        (cutoff_str,),
                    )
                    deleted[table] = cursor.rowcount

            if not dry_run:
                conn.execute("VACUUM")
                conn.commit()
        except Exception as exc:
            print(
                f"prismatic.telemetry: cleanup failed: {exc}",
                file=__import__("sys").stderr,
            )
            if not dry_run:
                conn.rollback()
        finally:
            conn.close()

        return deleted

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
                elif event_type == "agent_run":
                    conn.execute(
                        """INSERT OR REPLACE INTO telemetry_agent_runs
                           (run_id, agent, provider, model, issue_id,
                            status, start_time, end_time, exit_code,
                            credits_spent, error_message)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            data["run_id"], data["agent"],
                            data.get("provider", ""), data.get("model"),
                            data.get("issue_id", ""), data.get("status", "dispatched"),
                            data["start_time"], data.get("end_time"),
                            data.get("exit_code"), data.get("credits_spent", 0),
                            data.get("error_message"),
                        ),
                    )
                elif event_type == "agent_run_update":
                    conn.execute(
                        """UPDATE telemetry_agent_runs
                           SET status = ?, end_time = ?, exit_code = ?,
                               credits_spent = credits_spent + ?,
                               error_message = ?
                           WHERE run_id = ?""",
                        (
                            data["status"], data["end_time"],
                            data.get("exit_code"), data.get("credits_spent", 0),
                            data.get("error_message"), data["run_id"],
                        ),
                    )
                elif event_type == "credit":
                    conn.execute(
                        """INSERT INTO telemetry_credit_ledger
                           (run_id, agent, provider, model, credits_spent,
                            operation, recorded_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            data["run_id"], data["agent"], data["provider"],
                            data.get("model"), data["credits_spent"],
                            data.get("operation", ""), data["recorded_at"],
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

                CREATE TABLE IF NOT EXISTS telemetry_agent_runs (
                    run_id          TEXT PRIMARY KEY,
                    agent           TEXT NOT NULL,
                    provider        TEXT,
                    model           TEXT,
                    issue_id        TEXT,
                    status          TEXT DEFAULT 'dispatched',
                    start_time      TEXT NOT NULL,
                    end_time        TEXT,
                    exit_code       INTEGER,
                    credits_spent   INTEGER DEFAULT 0,
                    error_message   TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_agent
                    ON telemetry_agent_runs(agent, start_time);

                CREATE TABLE IF NOT EXISTS telemetry_credit_ledger (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    provider        TEXT NOT NULL,
                    model           TEXT,
                    credits_spent   INTEGER NOT NULL,
                    operation       TEXT,
                    recorded_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_credit_ledger_run
                    ON telemetry_credit_ledger(run_id);
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
