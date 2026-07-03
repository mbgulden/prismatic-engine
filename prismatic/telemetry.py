import os
import queue
import sqlite3
import threading
import json
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_DB_PATH = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "prismatic",
    "event_router.db"
)

class TelemetryCollector:
    """Non-blocking telemetry collector. All writes go through a queue."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._queue = queue.Queue(maxsize=10000)
        self._db_path = db_path
        self._stop_event = threading.Event()

        # Ensure DB directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        self._init_db()

        self._writer = threading.Thread(target=self._drain, daemon=True)
        self._writer.start()

    def _init_db(self):
        """Initialize telemetry tables."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        # 2.1 Loop Architecture
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_loop_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                issue_id TEXT,
                agent TEXT,
                loop_type TEXT,
                trigger TEXT,
                resolved INTEGER,
                depth INTEGER,
                parent_id TEXT,
                timestamp TEXT
            )
        """)

        # 2.2 Circuit Breakers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_circuit_breakers (
                issue_id TEXT PRIMARY KEY,
                agent TEXT,
                micro_count INTEGER,
                macro_count INTEGER,
                tripped INTEGER,
                last_updated TEXT
            )
        """)

        # 2.3 Token Metrics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_token_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                agent TEXT,
                provider TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                ttft_ms REAL,
                tps REAL,
                context_pct REAL,
                vram_mb INTEGER,
                timestamp TEXT
            )
        """)

        # 2.4 Validation Events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_validation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                agent TEXT,
                event_type TEXT,
                total_tests INTEGER,
                passed INTEGER,
                failed INTEGER,
                sandbox_id TEXT,
                rollback INTEGER,
                watch_sec REAL,
                timestamp TEXT
            )
        """)

        conn.commit()
        conn.close()

    def _drain(self):
        """Background thread to write events to SQLite."""
        while not self._stop_event.is_set():
            try:
                # Batch processing could be implemented here for higher throughput
                item = self._queue.get(timeout=1.0)
                if item is None:
                    break

                func_name, args = item
                self._execute_write(func_name, args)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[telemetry] Writer thread error: {e}")

    def _execute_write(self, func_name: str, args: tuple):
        """Actually perform the DB write."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            if func_name == "record_loop":
                cursor.execute("""
                    INSERT INTO telemetry_loop_events
                    (run_id, issue_id, agent, loop_type, trigger, resolved, depth, parent_id, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, args + (timestamp,))
            elif func_name == "record_tokens":
                cursor.execute("""
                    INSERT INTO telemetry_token_metrics
                    (run_id, agent, provider, model, prompt_tokens, completion_tokens, ttft_ms, tps, context_pct, vram_mb, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, args + (timestamp,))
            elif func_name == "record_validation":
                cursor.execute("""
                    INSERT INTO telemetry_validation_events
                    (run_id, agent, event_type, total_tests, passed, failed, sandbox_id, rollback, watch_sec, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, args + (timestamp,))
            elif func_name == "update_circuit":
                issue_id, agent, micro_count, macro_count, tripped = args
                cursor.execute("""
                    INSERT OR REPLACE INTO telemetry_circuit_breakers
                    (issue_id, agent, micro_count, macro_count, tripped, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (issue_id, agent, micro_count, macro_count, tripped, timestamp))

            conn.commit()
        except Exception as e:
            print(f"[telemetry] DB write error in {func_name}: {e}")
        finally:
            conn.close()

    def record_loop(self, run_id, issue_id, agent, loop_type, trigger=None,
                    resolved=False, depth=0, parent_id=None):
        """Push a loop event onto the queue."""
        try:
            self._queue.put_nowait(("record_loop", (run_id, issue_id, agent, loop_type, trigger, 1 if resolved else 0, depth, parent_id)))
        except queue.Full:
            pass

    def record_tokens(self, run_id, agent, provider, model=None,
                      prompt_tokens=0, completion_tokens=0,
                      ttft_ms=0.0, tps=0.0, context_pct=0.0, vram_mb=0):
        """Push token metrics onto the queue."""
        try:
            self._queue.put_nowait(("record_tokens", (run_id, agent, provider, model, prompt_tokens, completion_tokens, ttft_ms, tps, context_pct, vram_mb)))
        except queue.Full:
            pass

    def record_validation(self, run_id, agent, event_type,
                          total=0, passed=0, failed=0,
                          sandbox_id=None, rollback=False, watch_sec=0.0):
        """Push validation event onto the queue."""
        try:
            self._queue.put_nowait(("record_validation", (run_id, agent, event_type, total, passed, failed, sandbox_id, 1 if rollback else 0, watch_sec)))
        except queue.Full:
            pass

    def check_circuit(self, issue_id, agent, micro_count, macro_count) -> bool:
        """Check and update circuit breaker. Returns True if tripped."""
        micro_max = int(os.environ.get("PRISMATIC_BREAKER_MICRO_MAX", 5))
        macro_max = int(os.environ.get("PRISMATIC_BREAKER_MACRO_MAX", 3))

        tripped = (micro_count >= micro_max) or (macro_count >= macro_max)

        # We push the update to the queue but return the result immediately for the caller
        try:
            self._queue.put_nowait(("update_circuit", (issue_id, agent, micro_count, macro_count, 1 if tripped else 0)))
        except queue.Full:
            pass

        return tripped

    def get_dashboard_data(self, hours: int = 24) -> dict:
        """Query recent telemetry for dashboard display."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        since = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()

        data = {
            "dispatches": 0,
            "stalled": 0,
            "blocked": 0,
            "tokens_used": 0,
            "avg_tps": 0.0,
            "context_heat": {},
            "tests_passed": 0,
            "tests_failed": 0,
            "tripped_breakers": 0,
            "rollbacks": 0
        }

        # Dispatches count
        cursor.execute("SELECT COUNT(*) FROM telemetry_loop_events WHERE timestamp > ? AND loop_type = 'dispatch'", (since_iso,))
        data["dispatches"] = cursor.fetchone()[0]

        # Tokens and TPS
        cursor.execute("SELECT SUM(prompt_tokens + completion_tokens), AVG(tps) FROM telemetry_token_metrics WHERE timestamp > ?", (since_iso,))
        row = cursor.fetchone()
        data["tokens_used"] = row[0] or 0
        data["avg_tps"] = row[1] or 0.0

        # Context heat per model
        cursor.execute("SELECT model, AVG(context_pct) FROM telemetry_token_metrics WHERE timestamp > ? GROUP BY model", (since_iso,))
        for row in cursor.fetchall():
            if row[0]:
                data["context_heat"][row[0]] = row[1]

        # Tests
        cursor.execute("SELECT SUM(passed), SUM(failed) FROM telemetry_validation_events WHERE timestamp > ?", (since_iso,))
        row = cursor.fetchone()
        data["tests_passed"] = row[0] or 0
        data["tests_failed"] = row[1] or 0

        # Rollbacks
        cursor.execute("SELECT SUM(rollback) FROM telemetry_validation_events WHERE timestamp > ?", (since_iso,))
        data["rollbacks"] = cursor.fetchone()[0] or 0

        # Tripped breakers
        cursor.execute("SELECT COUNT(*) FROM telemetry_circuit_breakers WHERE tripped = 1 AND last_updated > ?", (since_iso,))
        data["tripped_breakers"] = cursor.fetchone()[0]

        conn.close()
        return data

# Singleton
_collector: Optional[TelemetryCollector] = None
_lock = threading.Lock()

def get_collector() -> TelemetryCollector:
    """Get or create the global telemetry collector."""
    global _collector
    with _lock:
        if _collector is None:
            _collector = TelemetryCollector()
        return _collector
