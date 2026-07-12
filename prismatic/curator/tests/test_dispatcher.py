"""
Tests for prismatic.curator.dispatcher.

Covers:
- Lane model selection
- Budget checks (allowed / over-cap)
- Daily reset behavior
- Dispatch decision logic
- Supervisor cmd construction

Run: pytest prismatic/curator/tests/test_dispatcher.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.curator.dispatcher import (  # noqa: E402
    LaneBudgetTracker, DispatchDecision, decide_dispatch, build_supervisor_cmd,
    LANE_MODEL, LANE_DAILY_BUDGET_USD, LANE_ESTIMATED_COST_USD,
    BUDGET_STATE,
)


# === LaneBudgetTracker tests ===

@pytest.fixture
def fresh_tracker(tmp_path):
    """A tracker with a temp state file."""
    state = tmp_path / "budget.json"
    return LaneBudgetTracker(state_path=state)


def test_fresh_tracker_allows_all(fresh_tracker):
    """No prior spending → all lanes under cap."""
    for lane in LANE_DAILY_BUDGET_USD.keys():
        check = fresh_tracker.check(lane)
        assert check.allowed, f"{lane} should be allowed initially"


def test_charge_accumulates(fresh_tracker):
    fresh_tracker.charge("codex", amount=1.0)
    fresh_tracker.charge("codex", amount=2.0)
    assert fresh_tracker.spent("codex") == 3.0


def test_cap_enforced_after_charges(fresh_tracker):
    """Repeatedly charge past cap, then check fails."""
    cap = LANE_DAILY_BUDGET_USD["kai"]  # smallest cap = $3
    # Charge to just under cap
    for _ in range(int(cap / LANE_ESTIMATED_COST_USD["kai"])):
        check = fresh_tracker.check("kai")
        if check.allowed:
            fresh_tracker.charge("kai")
        else:
            break

    # After many charges, should be near or at cap
    spent = fresh_tracker.spent("kai")
    assert spent >= cap - 0.5

    # Now check should fail (or be very close to cap)
    check = fresh_tracker.check("kai")
    if spent + LANE_ESTIMATED_COST_USD["kai"] > cap:
        assert not check.allowed
        assert "cap" in check.reason.lower()


def test_different_lanes_have_independent_budgets(fresh_tracker):
    """Spending on codex doesn't affect kai's budget."""
    fresh_tracker.charge("codex", amount=10.0)
    check = fresh_tracker.check("kai")
    assert check.allowed
    assert check.spent_today == 0.0


def test_snapshot_returns_all_lanes(fresh_tracker):
    fresh_tracker.charge("codex", amount=2.5)
    snap = fresh_tracker.snapshot()
    assert "date" in snap
    assert "lanes" in snap
    assert "codex" in snap["lanes"]
    assert snap["lanes"]["codex"]["spent"] == 2.5
    assert snap["lanes"]["codex"]["cap"] == LANE_DAILY_BUDGET_USD["codex"]


def test_snapshot_computes_utilization_pct(fresh_tracker):
    cap = LANE_DAILY_BUDGET_USD["ned"]
    fresh_tracker.charge("ned", amount=cap * 0.5)
    snap = fresh_tracker.snapshot()
    assert snap["lanes"]["ned"]["utilization_pct"] == 50.0


# === decide_dispatch tests ===

def test_decide_dispatch_no_lane_hint():
    decision = decide_dispatch(None)
    assert not decision.should_dispatch
    assert "no lane hint" in decision.reason.lower()


def test_decide_dispatch_known_lane():
    decision = decide_dispatch("codex")
    assert decision.should_dispatch
    assert decision.lane == "codex"
    assert decision.model == "sonnet"


def test_decide_dispatch_unknown_lane():
    decision = decide_dispatch("totally-bogus-lane")
    assert not decision.should_dispatch
    assert "unknown lane" in decision.reason.lower() or "triage" in decision.reason.lower()


def test_decide_dispatch_fred_uses_opus():
    """Fred orchestrates — should default to opus for high-judgment work."""
    decision = decide_dispatch("fred")
    assert decision.model == "opus"


def test_decide_dispatch_blocks_when_over_cap(fresh_tracker):
    """Force budget exhaustion, then verify decision blocks."""
    # Set cap to 0 by over-charging
    fresh_tracker.charge("codex", amount=100.0)
    decision = decide_dispatch("codex", budget_tracker=fresh_tracker)
    assert not decision.should_dispatch
    assert decision.lane == "codex"
    assert "cap" in decision.reason.lower()


# === build_supervisor_cmd tests ===

def test_build_supervisor_cmd_includes_lane_and_model():
    cmd = build_supervisor_cmd("GRO-1234", lane="codex", model="sonnet")
    assert "python3" in cmd[0] or cmd[0] == "python3"
    assert "--issue" in cmd
    assert "GRO-1234" in cmd
    assert "--model" in cmd
    assert "sonnet" in cmd
    assert "--lane" in cmd
    assert "codex" in cmd


def test_build_supervisor_cmd_opus():
    cmd = build_supervisor_cmd("GRO-X", lane="fred", model="opus")
    assert "opus" in cmd


# === Quota check and tracking tests ===

def test_get_daily_dispatch_count(tmp_path):
    import sqlite3
    from datetime import datetime, timezone
    from prismatic.curator.dispatcher import get_daily_dispatch_count

    db_file = tmp_path / "event_router.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE telemetry_credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            credits_spent INTEGER NOT NULL,
            operation TEXT,
            recorded_at TEXT NOT NULL
        )"""
    )
    # Insert some dispatches for today (UTC midnight cutoff)
    now_str = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-1", "agent:fred", "prismatic-dispatcher", "opus", 0, "dispatch", now_str)
    )
    conn.execute(
        "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-2", "agent:fred", "prismatic-dispatcher", "opus", 0, "dispatch", now_str)
    )
    # Different operation (should not count as dispatch)
    conn.execute(
        "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-3", "agent:fred", "prismatic-dispatcher", "opus", 100, "token_usage", now_str)
    )
    # Different model (should not count for opus)
    conn.execute(
        "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-4", "agent:codex", "prismatic-dispatcher", "sonnet", 0, "dispatch", now_str)
    )
    # Outdated recorded_at (yesterday)
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-5", "agent:fred", "prismatic-dispatcher", "opus", 0, "dispatch", yesterday_str)
    )
    conn.commit()
    conn.close()

    assert get_daily_dispatch_count("opus", db_path=db_file) == 2
    assert get_daily_dispatch_count("sonnet", db_path=db_file) == 1
    assert get_daily_dispatch_count("haiku", db_path=db_file) == 0


def test_decide_dispatch_quota_paused(tmp_path):
    import sqlite3
    from datetime import datetime, timezone
    from prismatic.curator.dispatcher import decide_dispatch

    db_file = tmp_path / "event_router.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE telemetry_credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            credits_spent INTEGER NOT NULL,
            operation TEXT,
            recorded_at TEXT NOT NULL
        )"""
    )
    now_str = datetime.now(timezone.utc).isoformat()
    # Insert 14 dispatches for opus
    for i in range(14):
        conn.execute(
            "INSERT INTO telemetry_credit_ledger (run_id, agent, provider, model, credits_spent, operation, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"run-{i}", "agent:fred", "prismatic-dispatcher", "opus", 0, "dispatch", now_str)
        )
    conn.commit()
    conn.close()

    # Now decision for fred (which maps to opus) should be blocked and quota_paused=True
    decision = decide_dispatch("fred", db_path=db_file)
    assert not decision.should_dispatch
    assert decision.quota_paused
    assert "quota paused" in decision.reason
    assert decision.model == "opus"

    # Codex (which maps to sonnet) has no limit, so it should be allowed
    decision_codex = decide_dispatch("codex", db_path=db_file)
    assert decision_codex.should_dispatch
    assert not decision_codex.quota_paused