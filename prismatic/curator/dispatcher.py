"""
prismatic.curator.dispatcher — Sonnet/Opus integration for the curator (Story 1.5).

When the curator tags an event as 'delegate', it should:
1. Determine which lane (fred|codex|kai|jules|ned) should handle it
2. Check per-lane per-day budget cap
3. If under cap, dispatch via bounded supervisor pool with the
   appropriate model flag
4. If over cap, escalate with reason=budget_exceeded

This is the bridge from the curator's classification layer to the actual
agent dispatch layer.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# Lane -> default AGY model
LANE_MODEL = {
    "fred": "opus",      # orchestration, audits: high-judgment
    "codex": "sonnet",   # general code: balanced
    "kai": "sonnet",     # content/broken-link: fast
    "jules": "sonnet",   # UI/review: balanced
    "ned": "sonnet",     # cron/monitoring: fast
    "triage": "sonnet",  # new issue assignment: cheap classification
    "creative": "sonnet", # creative asset generation
}

# Per-lane per-day budget caps (in USD)
LANE_DAILY_BUDGET_USD = {
    "fred": float(os.environ.get("PRISMATIC_BUDGET_FRED", "5.00")),
    "codex": float(os.environ.get("PRISMATIC_BUDGET_CODEX", "10.00")),
    "kai": float(os.environ.get("PRISMATIC_BUDGET_KAI", "3.00")),
    "jules": float(os.environ.get("PRISMATIC_BUDGET_JULES", "3.00")),
    "ned": float(os.environ.get("PRISMATIC_BUDGET_NED", "5.00")),
    "triage": float(os.environ.get("PRISMATIC_BUDGET_TRIAGE", "1.00")),
    "creative": float(os.environ.get("PRISMATIC_BUDGET_CREATIVE", "50.00")),
}

# Estimated cost per dispatch (USD). Conservative defaults; tune in prod.
LANE_ESTIMATED_COST_USD = {
    "fred": 0.50,    # opus is expensive
    "codex": 0.20,
    "kai": 0.10,
    "jules": 0.15,
    "ned": 0.15,
    "triage": 0.05,
    "creative": 0.10,
}

# Path to budget state (resets daily)
BUDGET_STATE = Path(os.environ.get("PRISMATIC_BUDGET_STATE",
                                   os.path.expanduser("~/.prismatic/curator/budget.json")))


@dataclass
class BudgetCheck:
    lane: str
    allowed: bool
    spent_today: float
    cap: float
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "allowed": self.allowed,
            "spent_today": round(self.spent_today, 4),
            "cap": self.cap,
            "reason": self.reason,
        }


class LaneBudgetTracker:
    """Track per-lane daily spending. Resets at midnight local."""

    def __init__(self, state_path: Path = BUDGET_STATE):
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {"date": self._today(), "lanes": {}}
        try:
            import json
            with self.state_path.open() as f:
                return json.load(f)
        except Exception:
            return {"date": self._today(), "lanes": {}}

    def _save(self) -> None:
        import json
        with self.state_path.open("w") as f:
            json.dump(self._state, f, indent=2)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _maybe_reset(self) -> None:
        today = self._today()
        if self._state.get("date") != today:
            self._state = {"date": today, "lanes": {}}

    def check(self, lane: str) -> BudgetCheck:
        """Check if a dispatch to `lane` is allowed under today's budget."""
        self._maybe_reset()
        cap = LANE_DAILY_BUDGET_USD.get(lane, 1.0)
        spent = self._state.get("lanes", {}).get(lane, 0.0)
        cost = LANE_ESTIMATED_COST_USD.get(lane, 0.10)
        if spent + cost > cap:
            return BudgetCheck(
                lane=lane, allowed=False,
                spent_today=spent, cap=cap,
                reason=f"would exceed cap (${spent + cost:.2f} > ${cap:.2f})",
            )
        return BudgetCheck(
            lane=lane, allowed=True,
            spent_today=spent, cap=cap,
            reason=f"under cap (${spent + cost:.2f}/${cap:.2f})",
        )

    def charge(self, lane: str, amount: float | None = None) -> None:
        """Record spending against a lane's daily budget."""
        self._maybe_reset()
        if lane not in self._state["lanes"]:
            self._state["lanes"][lane] = 0.0
        self._state["lanes"][lane] += amount if amount is not None else LANE_ESTIMATED_COST_USD.get(lane, 0.10)
        self._save()

    def spent(self, lane: str) -> float:
        self._maybe_reset()
        return self._state.get("lanes", {}).get(lane, 0.0)

    def snapshot(self) -> dict:
        self._maybe_reset()
        out = {"date": self._state["date"], "lanes": {}}
        for lane, cap in LANE_DAILY_BUDGET_USD.items():
            spent = self._state.get("lanes", {}).get(lane, 0.0)
            out["lanes"][lane] = {
                "spent": round(spent, 4),
                "cap": cap,
                "remaining": round(cap - spent, 4),
                "utilization_pct": round(100 * spent / cap, 1) if cap > 0 else 0,
            }
        return out


# === Dispatch decision ===

MODEL_DAILY_DISPATCH_LIMIT = {
    "opus": 14,
}


def get_daily_dispatch_count(model: str, db_path: Path | str | None = None) -> int:
    """Query telemetry_credit_ledger for the count of dispatches for `model` today (since UTC midnight)."""
    import sqlite3

    if db_path is None:
        db_path = os.path.join(
            os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
            "event_router.db",
        )

    if not os.path.exists(db_path):
        return 0

    # UTC midnight cutoff
    utc_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT COUNT(*) FROM telemetry_credit_ledger
                   WHERE model = ? AND operation = 'dispatch' AND recorded_at >= ?""",
                (model, utc_midnight),
            )
            row = cursor.fetchone()
            return row[0] if row else 0
    except Exception:
        # If database or table is missing/malformed, return 0
        return 0


@dataclass
class DispatchDecision:
    should_dispatch: bool
    lane: str | None = None
    model: str = "sonnet"
    reason: str = ""
    budget_check: BudgetCheck | None = None
    quota_paused: bool = False

    def to_dict(self) -> dict:
        return {
            "should_dispatch": self.should_dispatch,
            "lane": self.lane,
            "model": self.model,
            "reason": self.reason,
            "budget_check": self.budget_check.to_dict() if self.budget_check else None,
            "quota_paused": self.quota_paused,
        }


def decide_dispatch(
    lane_hint: str | None,
    budget_tracker: LaneBudgetTracker | None = None,
    db_path: Path | str | None = None,
) -> DispatchDecision:
    """Given a lane hint from the curator's tag, decide whether to dispatch.

    Returns DispatchDecision with should_dispatch, lane, model, reason.
    """
    budget_tracker = budget_tracker or LaneBudgetTracker()

    if not lane_hint:
        return DispatchDecision(
            should_dispatch=False,
            reason="no lane hint — can't decide where to route",
        )

    if lane_hint not in LANE_MODEL:
        # Unknown lane — escalate to fred for triage
        return DispatchDecision(
            should_dispatch=False,
            reason=f"unknown lane {lane_hint!r} — needs triage by fred",
        )

    model = LANE_MODEL[lane_hint]
    limit = MODEL_DAILY_DISPATCH_LIMIT.get(model)
    if limit is not None:
        dispatch_count = get_daily_dispatch_count(model, db_path=db_path)
        if dispatch_count >= limit:
            return DispatchDecision(
                should_dispatch=False,
                lane=lane_hint,
                model=model,
                reason=f"quota paused: {model} daily limit of {limit} dispatches reached ({dispatch_count} spent)",
                quota_paused=True,
            )

    budget_check = budget_tracker.check(lane_hint)
    if not budget_check.allowed:
        return DispatchDecision(
            should_dispatch=False,
            lane=lane_hint,
            model=model,
            reason=budget_check.reason,
            budget_check=budget_check,
        )

    return DispatchDecision(
        should_dispatch=True,
        lane=lane_hint,
        model=model,
        reason=f"budget ok ({budget_check.reason})",
        budget_check=budget_check,
    )


def build_supervisor_cmd(issue_id: str, lane: str, model: str,
                         supervisor_path: str | None = None) -> list[str]:
    """Build the argv for spawning a supervisor that uses the given model.

    `supervisor_path` defaults to $PRISMATIC_SUPERVISOR_PATH if set.
    Tests can pass a tmp path.
    """
    path = supervisor_path or os.environ.get("PRISMATIC_SUPERVISOR_PATH")
    if not path:
        # Final fallback: relative to PRISMATIC_HOME (defaults to ~).
        home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
        path = os.path.join(home, ".hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py")
    return [
        "python3",
        path,
        "--issue", issue_id,
        "--from-linear",
        "--lane-mode", "auto",
        "--active-project", "pwp",
        "--backlog-age-days", "30",
        "--jitter", "5-10",
        "--backoff", "3-8",
        "--max-concurrent", "2",
        "--model", model,
        "--lane", lane,
    ]