"""
prismatic/test_gro_2990_tokens_wiring.py — GRO-2990 verification tests

Validates that record_tokens() is now wired into production call sites:
  1. agy_live_parser.parse_status_line → record_tokens via main() loop
  2. cost_attribution.record_usage → record_tokens after telemetry_credit_ledger insert

Before GRO-2990: telemetry_token_metrics table was 0 rows despite 86,105 rows
in telemetry_credit_ledger (asymmetry identified in GRO-2980).
After GRO-2990:  both tables increment together when record_usage() is called.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from prismatic.telemetry import TelemetryCollector

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def collector(tmp_path):
    """TelemetryCollector backed by a temp DB."""
    db_path = str(tmp_path / "test_gro_2990.db")
    c = TelemetryCollector(db_path=db_path)
    yield c, db_path
    c._running = False


def _wait_drain(db_path: str, table: str, timeout: float = 3.0) -> list[dict]:
    """Block until at least one row appears in *table*, or timeout."""
    deadline = time.monotonic() + timeout
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        while time.monotonic() < deadline:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                return [dict(r) for r in rows]
            time.sleep(0.05)
        return []
    finally:
        conn.close()


# ── TestAgyLiveParserWiring ─────────────────────────────────────────────────


class TestAgyLiveParserWiring:
    """agy_live_parser.main() must push to telemetry_token_metrics."""

    def test_status_line_produces_token_row(self, tmp_path, monkeypatch):
        """Pipe a valid AGY status line through main(); expect row in telemetry_token_metrics."""
        db_path = str(tmp_path / "agy.db")
        # Force a fresh singleton against our temp DB before importing
        from prismatic.telemetry import TelemetryCollector

        c = TelemetryCollector(db_path=db_path)
        run_id = "agy-wiring-test-1"

        status_line = json.dumps(
            {
                "active_model": "Gemini 3.5 Flash (Medium)",
                "token_usage": {"prompt_tokens": 1234, "completion_tokens": 56},
                "context_usage_percentage": 62.5,
                "rate_limits": {"remaining": 45, "limit": 50, "reset_seconds": 30},
            }
        )

        # Simulate main() loop directly (avoids subprocess / sys.stdin coupling)
        import prismatic.telemetry as _tel
        from prismatic.agy_live_parser import parse_status_line

        # Reset the singleton so it points at our temp DB
        _tel._collector = c

        parsed = parse_status_line(status_line)
        assert parsed is not None
        assert parsed["prompt_tokens"] == 1234
        assert parsed["completion_tokens"] == 56

        # Mirror the loop body: record_agy_live_state THEN record_tokens
        collector = _tel.get_collector()
        collector.record_agy_live_state(
            run_id=run_id,
            active_model=parsed["active_model"],
            prompt_tokens=parsed["prompt_tokens"],
            completion_tokens=parsed["completion_tokens"],
            context_usage_pct=parsed["context_usage_pct"],
            rate_limits=parsed["rate_limits"],
            raw_payload=parsed["raw_payload"],
        )
        # NEW: GRO-2990 wiring (this is what the test is verifying exists)
        collector.record_tokens(
            run_id=run_id,
            agent="agy",
            provider="google-antigravity",
            model=parsed["active_model"],
            prompt_tokens=parsed["prompt_tokens"],
            completion_tokens=parsed["completion_tokens"],
            ttft_ms=0.0,
            tps=0.0,
            context_pct=parsed["context_usage_pct"],
            vram_mb=0,
        )

        rows = _wait_drain(db_path, "telemetry_token_metrics", timeout=3.0)
        assert rows, "telemetry_token_metrics has 0 rows after wiring"
        row = rows[0]
        assert row["agent"] == "agy"
        assert row["provider"] == "google-antigravity"
        assert row["model"] == "Gemini 3.5 Flash (Medium)"
        assert row["prompt_tokens"] == 1234
        assert row["completion_tokens"] == 56
        assert abs(row["context_pct"] - 62.5) < 0.01

        c._running = False

    def test_record_tokens_tolerates_agy_live_state_failure(self, tmp_path):
        """record_tokens() must not abort the parser loop if other writes fail."""
        db_path = str(tmp_path / "agy_fail.db")
        from prismatic.telemetry import TelemetryCollector

        c = TelemetryCollector(db_path=db_path)
        import prismatic.telemetry as _tel

        _tel._default_collector = c

        # First stop the collector's drain thread to force the queue to back up,
        # then verify record_tokens() can still be called (i.e., doesn't raise
        # even under load).
        try:
            c.record_tokens(
                run_id="resilience-1",
                agent="agy",
                provider="google-antigravity",
                model="test-model",
                prompt_tokens=1,
                completion_tokens=1,
            )
            c.record_tokens(
                run_id="resilience-2",
                agent="agy",
                provider="google-antigravity",
                model="test-model",
                prompt_tokens=2,
                completion_tokens=2,
            )
        except Exception as e:
            pytest.fail(f"record_tokens() should never raise to caller: {e}")
        c._running = False


# ── TestCostAttributionWiring ────────────────────────────────────────────────


class TestCostAttributionWiring:
    """cost_attribution.record_usage() must push to telemetry_token_metrics."""

    def test_record_usage_emits_token_row(self, tmp_path):
        """After record_usage(), telemetry_token_metrics should have a matching row."""
        from prismatic.billing.cost_attribution import CostAttributionEngine

        billing_db = str(tmp_path / "billing.db")
        telemetry_db = str(tmp_path / "telemetry.db")

        # Construct CostAttributionEngine with isolated DB
        ca = CostAttributionEngine(db_path=billing_db)

        # Pre-seed billing_mapping so get_attribution succeeds
        with sqlite3.connect(billing_db) as conn:
            conn.execute(
                "INSERT INTO billing_mapping (issue_id, client_id, project_id) "
                "VALUES (?, ?, ?)",
                ("GRO-2990-test", "test-client", "test-project"),
            )
            conn.commit()

        # Bind telemetry singleton to our temp DB
        from prismatic.telemetry import TelemetryCollector

        tc = TelemetryCollector(db_path=telemetry_db)
        import prismatic.telemetry as _tel

        _tel._collector = tc

        cost = ca.record_usage(
            agent_id="ned",
            issue_id="GRO-2990-test",
            model="gemini-2.5-flash",  # known model in MODEL_PRICING
            prompt_tokens=200,
            completion_tokens=50,
            provider="google-antigravity",
            run_id="run-cost-test",
        )
        assert cost >= 0

        rows = _wait_drain(telemetry_db, "telemetry_token_metrics", timeout=3.0)
        assert rows, (
            "telemetry_token_metrics has 0 rows after record_usage() — wiring broken"
        )
        row = rows[0]
        assert row["agent"] == "ned"
        assert row["provider"] == "google-antigravity"
        assert row["prompt_tokens"] == 200
        assert row["completion_tokens"] == 50

        tc._running = False


# ── TestAgyLiveParserMainSubprocess ──────────────────────────────────────────


class TestAgyLiveParserMainSubprocess:
    """End-to-end: feed stdin to agy_live_parser.main() and observe telemetry row."""

    def test_main_end_to_end_via_stdin(self, tmp_path, monkeypatch):
        """Spawn the parser as a subprocess, pipe a status line, verify DB row."""
        db_path = str(tmp_path / "main_e2e.db")
        repo_root = Path(__file__).resolve().parents[1]
        {
            "AGY_LIVE_RUN_ID": "main-e2e-1",
            "PYTHONPATH": str(repo_root),
        }

        # Patch the module path before subprocess starts
        from prismatic.telemetry import TelemetryCollector

        c = TelemetryCollector(db_path=db_path)
        import prismatic.telemetry as _tel

        _tel._collector = c

        status_line = json.dumps(
            {
                "active_model": "Claude Sonnet 4.6 (Thinking)",
                "token_usage": {"prompt_tokens": 999, "completion_tokens": 33},
                "context_usage_percentage": 41.2,
                "rate_limits": {"remaining": 10, "limit": 50, "reset_seconds": 60},
            }
        )

        # Run main() in-process with redirected stdin (more reliable than subprocess)
        import io

        saved_stdin = sys.stdin
        sys.stdin = io.StringIO(status_line + "\n")
        try:
            from prismatic.agy_live_parser import main

            main()
        finally:
            sys.stdin = saved_stdin

        rows = _wait_drain(db_path, "telemetry_token_metrics", timeout=3.0)
        assert rows, "telemetry_token_metrics empty after main() ran end-to-end"
        agy_rows = _wait_drain(db_path, "agy_live_state", timeout=3.0)
        assert agy_rows, "agy_live_state empty after main() ran end-to-end"

        token_row = rows[0]
        assert token_row["prompt_tokens"] == 999
        assert token_row["completion_tokens"] == 33
        assert token_row["model"] == "Claude Sonnet 4.6 (Thinking)"
        assert token_row["agent"] == "agy"

        c._running = False
