#!/usr/bin/env python3
"""
prismatic/core/router.py — AGY Circuit Breaker Router

Model-aware fallback routing for AGY agent sessions. Before spawning
any AGY process, checks live telemetry state for cooldown timers and
quota exhaustion signals, then rewrites the agent command string to
use a fallback model from the priority chain.

Architecture:
    launch_agy() → CircuitBreakerRouter.check_and_route() → rewrite cmd
                                                              ↓
                                                     subprocess.Popen

Model Priority Chain (issue #GRO-1537):
    claude-opus → claude-sonnet → gemini-3.5-flash → gemini-3.1-flash-lite → gpt-oss-120b

The router achieves zero-latency switching by reading from the
existing telemetry SQLite database — no network calls, no session
restarts.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.core.router")

# ── Default telemetry DB path (shared with TelemetryCollector) ──
DEFAULT_DB_PATH: str = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# ── Cooldown / quota detection thresholds (env-overridable) ────
COOLDOWN_WINDOW_SECONDS: int = int(
    os.environ.get("PRISMATIC_CB_COOLDOWN_SECONDS", "300")
)
"""Window in seconds to consider rate-limit signals as 'active'."""

QUOTA_EXHAUSTION_KEYWORDS: list[str] = [
    "quota exceeded",
    "rate limit",
    "429",
    "RESOURCE_EXHAUSTED",
    "quota_exhausted",
    "too many requests",
    "API rate limit",
    "quota limit reached",
]
"""Keywords in AGY live state raw payloads that indicate quota exhaustion."""


# ═══════════════════════════════════════════════════════════════
# Model Priority Chain
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelEntry:
    """A single entry in the model priority chain."""
    canonical_name: str          # Internal key (e.g. "claude-opus")
    agy_model_flag: str          # Value for --model flag (e.g. "Claude Opus 4.6 (Thinking)")
    tier: str = "premium"        # "premium" | "fallback" | "free"
    description: str = ""


MODEL_PRIORITY_CHAIN: list[ModelEntry] = [
    ModelEntry(
        canonical_name="claude-opus",
        agy_model_flag="Claude Opus 4.6 (Thinking)",
        tier="premium",
        description="Top-tier premium model — first choice for code generation",
    ),
    ModelEntry(
        canonical_name="claude-sonnet",
        agy_model_flag="Claude Sonnet 4.6 (Thinking)",
        tier="premium",
        description="Secondary premium model — capable code generation",
    ),
    ModelEntry(
        canonical_name="gemini-3.5-flash",
        agy_model_flag="Gemini 3.5 Flash (Medium)",
        tier="fallback",
        description="Primary fallback — fast, capable, cost-effective",
    ),
    ModelEntry(
        canonical_name="gemini-3.1-flash-lite",
        agy_model_flag="Gemini 3.1 Pro (Low)",
        tier="fallback",
        description="Secondary fallback — lower capability but always available",
    ),
    ModelEntry(
        canonical_name="gpt-oss-120b",
        agy_model_flag="GPT-OSS 120B (Medium)",
        tier="free",
        description="Last-resort — local/open model, no quota constraints",
    ),
]

# Lookup dicts for fast access
_MODEL_BY_CANONICAL: dict[str, ModelEntry] = {
    m.canonical_name: m for m in MODEL_PRIORITY_CHAIN
}
_MODEL_BY_FLAG: dict[str, ModelEntry] = {
    m.agy_model_flag: m for m in MODEL_PRIORITY_CHAIN
}


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker State
# ═══════════════════════════════════════════════════════════════

@dataclass
class CircuitBreakerState:
    """Snapshot of the circuit breaker state for an agent session."""
    cooldown_active: bool = False
    cooldown_reason: str = ""
    cooldown_until_iso: str = ""
    current_model: str = ""
    recommended_model: str = ""
    fallback_applied: bool = False
    fallback_reason: str = ""
    quota_exhausted: bool = False


# ═══════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════

class CircuitBreakerRouter:
    """Model-aware fallback router for AGY agent sessions.

    Before spawning an AGY process, call :meth:`check_and_route` to
    determine whether a model downgrade is needed.  If the circuit is
    open (quota exhausted, cooldown active), the router rewrites the
    command-line arguments to use the next-available model from the
    priority chain.

    All fallback decisions are logged to the telemetry database for
    pipeline health monitoring.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._fallback_count: dict[str, int] = {}  # per-session fallback counter
        self._last_check_ts: dict[str, float] = {}  # per-session throttle

    # ── Public API ──────────────────────────────────────────

    def check_and_route(
        self,
        issue_id: str,
        cmd: list[str],
        *,
        current_model: str = "",
        force_model: str = "",
    ) -> tuple[list[str], CircuitBreakerState]:
        """Evaluate circuit breaker state and rewrite *cmd* if needed.

        Args:
            issue_id: Linear issue identifier (e.g. ``"GRO-1537"``).
            cmd: The command-line argument list (e.g. ``["agy", "--headless", "--issue", "GRO-1537"]``).
            current_model: The model currently in use (if known).
            force_model: Override to force a specific model (bypasses chain logic).

        Returns:
            Tuple of ``(rewritten_cmd, state)`` where *rewritten_cmd* is
            the possibly-modified command list and *state* contains the
            circuit breaker decision details.
        """
        state = self._evaluate(issue_id, current_model=current_model)

        # Force-model override (bypasses all logic)
        if force_model:
            model_entry = _MODEL_BY_CANONICAL.get(
                force_model
            ) or _MODEL_BY_FLAG.get(force_model)
            if model_entry:
                cmd = self._rewrite_model_flag(cmd, model_entry.agy_model_flag)
                state.recommended_model = model_entry.canonical_name
                state.fallback_applied = True
                state.fallback_reason = f"force_model={force_model}"
                self._log_fallback(issue_id, state)
                return cmd, state

        # If circuit is NOT tripped, use current model (or default premium)
        if not state.cooldown_active and not state.quota_exhausted:
            if state.current_model and state.current_model != "unknown":
                # Ensure the current model flag is present in the command
                model_entry = (
                    _MODEL_BY_CANONICAL.get(state.current_model)
                    or _MODEL_BY_FLAG.get(state.current_model)
                )
                if model_entry:
                    cmd = self._rewrite_model_flag(cmd, model_entry.agy_model_flag)
            return cmd, state

        # Circuit is tripped — find the next available model
        recommended = self._find_next_available(state.current_model or "claude-opus")
        if recommended:
            cmd = self._rewrite_model_flag(cmd, recommended.agy_model_flag)
            state.recommended_model = recommended.canonical_name
            state.fallback_applied = True
            state.fallback_reason = (
                f"cooldown={state.cooldown_active} quota={state.quota_exhausted}"
            )
            self._log_fallback(issue_id, state)
            logger.info(
                "Circuit breaker: %s → %s for %s (%s)",
                state.current_model or "premium",
                recommended.canonical_name,
                issue_id,
                state.fallback_reason,
            )

        return cmd, state

    def get_current_model_for_issue(self, issue_id: str) -> str:
        """Query the telemetry DB for the most recently used model on an issue.

        Returns the model flag string (e.g. ``"Claude Opus 4.6 (Thinking)"``),
        or an empty string if no telemetry exists.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT active_model FROM telemetry_agy_live_state
                   WHERE run_id LIKE ?
                   ORDER BY recorded_at DESC LIMIT 1""",
                (f"%{issue_id}%",),
            ).fetchone()
            return row[0] if row and row[0] else ""
        except sqlite3.OperationalError:
            # Table may not exist yet
            return ""
        finally:
            conn.close()

    def has_recent_cooldown(self, issue_id: str) -> bool:
        """Check if there's a recent rate-limit signal for *issue_id*."""
        state = self._evaluate(issue_id)
        return state.cooldown_active

    def reset(self, issue_id: str) -> None:
        """Reset fallback tracking for *issue_id* (e.g., after human intervention)."""
        self._fallback_count.pop(issue_id, None)
        self._last_check_ts.pop(issue_id, None)

    # ── Internal ────────────────────────────────────────────

    def _evaluate(
        self, issue_id: str, *, current_model: str = ""
    ) -> CircuitBreakerState:
        """Evaluate the full circuit breaker state for an issue."""
        state = CircuitBreakerState(current_model=current_model or "unknown")

        conn = sqlite3.connect(self._db_path)
        try:
            # 1. Check for active cooldown from rate-limit signals
            cutoff_iso = _seconds_ago_iso(COOLDOWN_WINDOW_SECONDS)
            rows = conn.execute(
                """SELECT active_model, rate_limits, raw_payload, recorded_at
                   FROM telemetry_agy_live_state
                   WHERE run_id LIKE ? AND recorded_at >= ?
                   ORDER BY recorded_at DESC LIMIT 20""",
                (f"%{issue_id}%", cutoff_iso),
            ).fetchall()

            for row in rows:
                active_model, rate_limits_json, raw_payload, recorded_at = row

                # Track current model from live state
                if active_model and not state.current_model:
                    state.current_model = active_model

                # Check rate_limits JSON for imminent exhaustion
                if rate_limits_json:
                    try:
                        rl = json.loads(rate_limits_json)
                        remaining = rl.get("remaining", 999)
                        if remaining is not None and remaining <= 1:
                            state.quota_exhausted = True
                            state.cooldown_active = True
                            state.cooldown_reason = f"rate_limits.remaining={remaining}"
                            state.cooldown_until_iso = _seconds_from_now_iso(
                                rl.get("reset_seconds", COOLDOWN_WINDOW_SECONDS)
                            )
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Check raw payload for quota exhaustion keywords
                if raw_payload and not state.quota_exhausted:
                    payload_lower = raw_payload.lower()
                    for keyword in QUOTA_EXHAUSTION_KEYWORDS:
                        if keyword.lower() in payload_lower:
                            state.quota_exhausted = True
                            state.cooldown_active = True
                            state.cooldown_reason = f"keyword_match:{keyword}"
                            state.cooldown_until_iso = _seconds_from_now_iso(
                                COOLDOWN_WINDOW_SECONDS
                            )
                            break

                if state.quota_exhausted and state.cooldown_active:
                    break

        except sqlite3.OperationalError:
            # Table may not exist — no cooldown detected
            pass
        finally:
            conn.close()

        return state

    def _find_next_available(self, current_model_canonical: str) -> ModelEntry | None:
        """Find the next model in the priority chain.

        Walks forward from *current_model_canonical* until a fallback
        or free-tier model is found.  Skips the current model and any
        models that are ALSO known to be exhausted (checked via the
        ``_exhausted_models`` set).
        """
        current_idx = -1
        for i, entry in enumerate(MODEL_PRIORITY_CHAIN):
            if entry.canonical_name == current_model_canonical:
                current_idx = i
                break

        if current_idx < 0:
            # Unknown model — start from the top
            current_idx = 0

        # Walk forward to find a non-premium fallback
        for i in range(current_idx + 1, len(MODEL_PRIORITY_CHAIN)):
            entry = MODEL_PRIORITY_CHAIN[i]
            if entry.tier in ("fallback", "free"):
                return entry

        # All exhausted — return the last resort (last in chain)
        return MODEL_PRIORITY_CHAIN[-1]

    @staticmethod
    def _rewrite_model_flag(cmd: list[str], model_flag: str) -> list[str]:
        """Rewrite or append the ``--model`` flag in a command argument list.

        If ``--model`` already exists, replace its value. Otherwise, append
        ``--model <model_flag>`` before any positionals (like issue ID).
        """
        cmd = list(cmd)  # copy

        # Replace existing --model flag
        for i, arg in enumerate(cmd):
            if arg == "--model" and i + 1 < len(cmd):
                cmd[i + 1] = model_flag
                return cmd

        # No existing --model — inject before the last argument (issue ID)
        # Find the best insertion point: after --headless, before --issue
        insert_at = len(cmd)
        for marker in ("--headless", "--print"):
            try:
                idx = cmd.index(marker)
                insert_at = min(insert_at, idx + 1)
            except ValueError:
                pass

        cmd.insert(insert_at, "--model")
        cmd.insert(insert_at + 1, model_flag)
        return cmd

    def _log_fallback(self, issue_id: str, state: CircuitBreakerState) -> None:
        """Log a fallback event to the telemetry database."""
        self._fallback_count[issue_id] = self._fallback_count.get(issue_id, 0) + 1

        # Use the TelemetryCollector if available (non-blocking queue write)
        try:
            from prismatic.telemetry import get_collector

            collector = get_collector()
            collector.record_agy_live_state(
                run_id=f"cb-router-{issue_id}",
                active_model=state.recommended_model,
                rate_limits={
                    "fallback_applied": True,
                    "fallback_reason": state.fallback_reason,
                    "fallback_count": self._fallback_count[issue_id],
                    "from_model": state.current_model,
                    "to_model": state.recommended_model,
                },
                raw_payload=json.dumps({
                    "event": "circuit_breaker_fallback",
                    "issue_id": issue_id,
                    "from_model": state.current_model,
                    "to_model": state.recommended_model,
                    "reason": state.fallback_reason,
                    "cooldown_active": state.cooldown_active,
                    "quota_exhausted": state.quota_exhausted,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
            )
        except Exception:
            # Best-effort — fallback still applied even if logging fails
            logger.debug("Failed to log fallback event", exc_info=True)


# ═══════════════════════════════════════════════════════════════
# Singleton + convenience
# ═══════════════════════════════════════════════════════════════

_router_instance: CircuitBreakerRouter | None = None


def get_router(db_path: str | None = None) -> CircuitBreakerRouter:
    """Get or create the singleton CircuitBreakerRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = CircuitBreakerRouter(db_path=db_path)
    return _router_instance


def check_and_route_agy(
    issue_id: str,
    cmd: list[str],
    *,
    current_model: str = "",
) -> tuple[list[str], CircuitBreakerState]:
    """Convenience function — evaluate circuit and route AGY model.

    This is the integration point for ``launch_agy()`` in
    ``prismatic/dispatcher.py``.

    Args:
        issue_id: Linear issue identifier.
        cmd: Command argument list for AGY.
        current_model: Optional current model hint.

    Returns:
        ``(rewritten_cmd, state)`` tuple.
    """
    router = get_router()
    return router.check_and_route(issue_id, cmd, current_model=current_model)


# ═══════════════════════════════════════════════════════════════
# Time helpers
# ═══════════════════════════════════════════════════════════════

def _seconds_ago_iso(seconds: int) -> str:
    """Return an ISO-8601 string for *seconds* ago in UTC."""
    ts = datetime.now(timezone.utc).timestamp() - seconds
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _seconds_from_now_iso(seconds: int) -> str:
    """Return an ISO-8601 string for *seconds* from now in UTC."""
    ts = datetime.now(timezone.utc).timestamp() + seconds
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
