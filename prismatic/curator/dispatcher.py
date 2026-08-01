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
}

# Per-lane per-day budget caps (in USD)
LANE_DAILY_BUDGET_USD = {
    "fred": float(os.environ.get("PRISMATIC_BUDGET_FRED", "5.00")),
    "codex": float(os.environ.get("PRISMATIC_BUDGET_CODEX", "10.00")),
    "kai": float(os.environ.get("PRISMATIC_BUDGET_KAI", "3.00")),
    "jules": float(os.environ.get("PRISMATIC_BUDGET_JULES", "3.00")),
    "ned": float(os.environ.get("PRISMATIC_BUDGET_NED", "5.00")),
    "triage": float(os.environ.get("PRISMATIC_BUDGET_TRIAGE", "1.00")),
}

# Estimated cost per dispatch (USD). Conservative defaults; tune in prod.
LANE_ESTIMATED_COST_USD = {
    "fred": 0.50,    # opus is expensive
    "codex": 0.20,
    "kai": 0.10,
    "jules": 0.15,
    "ned": 0.15,
    "triage": 0.05,
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

@dataclass
class DispatchDecision:
    should_dispatch: bool
    lane: str | None = None
    model: str = "sonnet"
    reason: str = ""
    budget_check: BudgetCheck | None = None

    def to_dict(self) -> dict:
        return {
            "should_dispatch": self.should_dispatch,
            "lane": self.lane,
            "model": self.model,
            "reason": self.reason,
            "budget_check": self.budget_check.to_dict() if self.budget_check else None,
        }


def decide_dispatch(lane_hint: str | None, budget_tracker: LaneBudgetTracker | None = None) -> DispatchDecision:
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

    budget_check = budget_tracker.check(lane_hint)
    if not budget_check.allowed:
        return DispatchDecision(
            should_dispatch=False,
            lane=lane_hint,
            model=LANE_MODEL[lane_hint],
            reason=budget_check.reason,
            budget_check=budget_check,
        )

    return DispatchDecision(
        should_dispatch=True,
        lane=lane_hint,
        model=LANE_MODEL[lane_hint],
        reason=f"budget ok ({budget_check.reason})",
        budget_check=budget_check,
    )


def build_supervisor_cmd(
    issue_id: str,
    lane: str,
    model: str,
    supervisor_path: str | None = None,
    python_executable: str | None = None,
    expected_release_root: str | None = None,
) -> list[str]:
    """Build an exact single-issue supervisor command.

    ``lane`` remains part of the curator's durable dispatch decision, but it is
    intentionally not forwarded: curator agent lanes and supervisor scheduler
    lanes are different contracts. Production can set
    ``PRISMATIC_REQUIRE_PINNED_SUPERVISOR=1`` to require explicit interpreter,
    supervisor, and release-root provenance instead of compatibility fallbacks.
    """
    del lane
    if not isinstance(issue_id, str) or not issue_id or issue_id.startswith("-") or "\x00" in issue_id:
        raise ValueError("issue_id must be a nonempty non-option string without NUL bytes")
    if not isinstance(model, str) or not model or model.startswith("-") or "\x00" in model:
        raise ValueError("model must be a nonempty non-option string without NUL bytes")

    configured_path = supervisor_path or os.environ.get("PRISMATIC_SUPERVISOR_PATH")
    configured_python = python_executable or os.environ.get("PRISMATIC_SUPERVISOR_PYTHON")
    release_root = expected_release_root or os.environ.get("PRISMATIC_RELEASE_ROOT")
    require_pinned = os.environ.get("PRISMATIC_REQUIRE_PINNED_SUPERVISOR") == "1"

    if require_pinned and not (configured_path and configured_python and release_root):
        raise RuntimeError(
            "pinned supervisor dispatch requires path, interpreter, and release root"
        )

    if not configured_path:
        home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
        configured_path = os.path.join(
            home,
            ".hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py",
        )
    if not configured_python:
        configured_python = "python3"

    if release_root:
        root_path = Path(release_root).expanduser()
        supervisor_path_obj = Path(configured_path).expanduser()
        if require_pinned and not root_path.is_absolute():
            raise RuntimeError("pinned release root must be an absolute path")
        if require_pinned and not supervisor_path_obj.is_absolute():
            raise RuntimeError("pinned supervisor path must be an absolute path")
        try:
            root = root_path.resolve(strict=require_pinned)
        except FileNotFoundError as exc:
            raise RuntimeError(f"pinned release root does not exist: {root_path}") from exc
        if require_pinned and not root.is_dir():
            raise RuntimeError(f"pinned release root is not a directory: {root}")
        supervisor = supervisor_path_obj.resolve()
        if not supervisor.is_relative_to(root):
            raise RuntimeError(
                f"supervisor path {supervisor} is outside expected release root {root}"
            )
        if require_pinned and not supervisor.is_file():
            raise RuntimeError(
                f"pinned supervisor path is not an existing regular file: {supervisor}"
            )
        configured_path = str(supervisor)

    if require_pinned:
        interpreter_path = Path(configured_python).expanduser()
        if not interpreter_path.is_absolute():
            raise RuntimeError("pinned interpreter must be an absolute path")
        try:
            interpreter = interpreter_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"pinned interpreter does not exist: {interpreter_path}"
            ) from exc
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise RuntimeError(
                f"pinned interpreter is not an executable regular file: {interpreter}"
            )
        configured_python = str(interpreter)

    return [
        configured_python,
        configured_path,
        "--issue",
        issue_id,
        "--lane-mode",
        "auto",
        "--jitter",
        "5-10",
        "--backoff",
        "3-8",
        "--max-concurrent",
        "2",
        "--model",
        model,
    ]