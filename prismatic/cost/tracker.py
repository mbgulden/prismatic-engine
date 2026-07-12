# SPDX-License-Identifier: AGPL-3.0-only
"""Per-dispatch token usage and dollar cost tracking."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

COST_DB = Path("~/.prismatic/cost.db").expanduser()

# Model pricing per 1M tokens — July 2026 defaults.
PRICING: dict[str, dict[str, float]] = {
    "gemini-3.5-flash-high": {"input": 0.075, "output": 0.30},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "claude-sonnet-4.6-thinking": {"input": 3.00, "output": 15.00},
    "claude-opus-4.6-thinking": {"input": 15.00, "output": 75.00},
}
FALLBACK_PRICING = {"input": 1.0, "output": 5.0}


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Initialize and return the cost database connection."""
    path = (db_path or COST_DB).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    from prismatic.admin import cmd_db_upgrade
    cmd_db_upgrade(str(path))
    conn = sqlite3.connect(str(path))
    return conn


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = PRICING.get(model, FALLBACK_PRICING)
    return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000


def record_cost(
    run_id: str,
    issue_id: str | None,
    agent: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    created_at: float | None = None,
    db_path: Path | None = None,
) -> float:
    """Record a dispatch cost row and return the calculated cost in dollars."""
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("token counts must be non-negative")
    cost = calculate_cost(model, tokens_in, tokens_out)
    conn = init_db(db_path)
    try:
        conn.execute(
            """INSERT INTO dispatch_costs
            (run_id, issue_id, agent, model, tokens_in, tokens_out, cost_dollars, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                issue_id,
                agent,
                model,
                tokens_in,
                tokens_out,
                cost,
                created_at or time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cost


def _date_string(date: str | datetime | None) -> str:
    if date is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(date, datetime):
        return date.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return str(date)


def daily_spend(
    date: str | datetime | None = None, *, db_path: Path | None = None
) -> float:
    """Return total spend for a UTC date (defaults to today)."""
    day = _date_string(date)
    conn = init_db(db_path)
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(cost_dollars), 0)
            FROM dispatch_costs
            WHERE strftime('%Y-%m-%d', created_at, 'unixepoch') = ?""",
            (day,),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


def agent_spend(agent: str, days: int = 7, *, db_path: Path | None = None) -> float:
    """Return spend for an agent over the last N days."""
    cutoff = time.time() - days * 86400
    conn = init_db(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_dollars), 0) FROM dispatch_costs WHERE agent = ? AND created_at >= ?",
            (agent, cutoff),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


def model_spend(days: int = 1, *, db_path: Path | None = None) -> dict[str, float]:
    """Return spend per model over the last N days."""
    cutoff = time.time() - days * 86400
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            """SELECT model, COALESCE(SUM(cost_dollars), 0)
            FROM dispatch_costs
            WHERE created_at >= ?
            GROUP BY model
            ORDER BY model""",
            (cutoff,),
        ).fetchall()
        return {str(model): float(cost or 0.0) for model, cost in rows}
    finally:
        conn.close()


def last_7_days() -> list[str]:
    """Return the last seven UTC date strings, oldest first."""
    today = datetime.now(timezone.utc)
    return [
        (today - timedelta(days=i)).strftime("%Y-%m-%d") for i in reversed(range(7))
    ]


def cost_summary(*, db_path: Path | None = None) -> dict[str, Any]:
    """Return gateway-ready cost summary."""
    agents = ["agy", "fred", "jules", "ned", "kai", "codex"]
    return {
        "today": daily_spend(db_path=db_path),
        "per_agent": {
            agent: agent_spend(agent, 1, db_path=db_path) for agent in agents
        },
        "per_model": model_spend(1, db_path=db_path),
        "trend_7d": [
            {"date": day, "spend": daily_spend(day, db_path=db_path)}
            for day in last_7_days()
        ],
    }
