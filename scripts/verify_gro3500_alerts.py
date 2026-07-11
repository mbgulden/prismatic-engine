#!/usr/bin/env python3
"""Focused verifier for GRO-3500 zero-execution and stale-queue alerts."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from prismatic.gateway.alert_manager import AlertEvaluator, AlertRouter


def base_dashboard(**overrides):
    data = {
        "loops": [],
        "tokens": [],
        "validation": {},
        "breakers_tripped": 0,
        "hours": 1,
        "credit_burn_rate": 0,
        "total_credits": 0,
        "failure_rate": 0.0,
        "total_agent_runs": 5,
        "failed_agent_runs": 0,
    }
    data.update(overrides)
    return data


def make_evaluator(payload):
    telemetry = MagicMock()
    telemetry.get_dashboard_data.return_value = payload
    tmp = Path(tempfile.mkdtemp(prefix="gro3500-alerts-"))
    router = AlertRouter()
    router._alert_log_path = tmp / "alerts.log"
    return AlertEvaluator(telemetry_collector=telemetry, router=router)


def main() -> int:
    evaluator = make_evaluator(base_dashboard(
        total_agent_runs=0,
        pending_queue_depth=7,
        stale_queue_depth=0,
        stale_queue_oldest_age_sec=120,
    ))
    triggered = evaluator.evaluate(hours=1)
    stall = next(a for a in triggered if a["name"] == "AgentStall")
    assert "failing_layer=execution/consumer" in stall["details"]
    assert "pending_queue_depth=7" in stall["details"]

    evaluator = make_evaluator(base_dashboard(
        pending_queue_depth=3,
        stale_queue_depth=1,
        stale_queue_oldest_age_sec=3600,
    ))
    triggered = evaluator.evaluate(hours=1)
    stale = next(a for a in triggered if a["name"] == "StaleQueue")
    assert "failing_layer=queue/dispatcher" in stale["details"]
    assert "stale_queue_depth=1" in stale["details"]

    state_dir = Path(tempfile.mkdtemp(prefix="gro3500-state-"))
    db_path = state_dir / "linear_webhook_queue.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE linear_webhook_queue (event_id TEXT, dispatch_status TEXT, received_at REAL)")
    conn.execute("INSERT INTO linear_webhook_queue VALUES ('e1', 'pending', ?)", (time.time() - 3600,))
    conn.commit()
    conn.close()

    old_state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    os.environ["PRISMATIC_STATE_DIR"] = str(state_dir)
    try:
        evaluator = make_evaluator(base_dashboard())
        triggered = evaluator.evaluate(hours=1)
    finally:
        if old_state_dir is None:
            os.environ.pop("PRISMATIC_STATE_DIR", None)
        else:
            os.environ["PRISMATIC_STATE_DIR"] = old_state_dir
    stale = next(a for a in triggered if a["name"] == "StaleQueue")
    assert "pending_queue_depth=1" in stale["details"]
    assert "failing_layer=queue/dispatcher" in stale["details"]

    print("GRO-3500 verifier passed: AgentStall, StaleQueue, and queue DB fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
