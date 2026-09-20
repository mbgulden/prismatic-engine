"""
prismatic/gateway/alert_manager.py — Alertmanager Notification Routing

Phase 4.5 of the Enterprise Observability Plan.

Provides:
1. Alert rule definitions (HighLockContention, AgentStall, CreditBurnRate, CircuitBreakerTrip)
2. Routing tree: critical → Telegram, warning → Slack hermes-feed, info → log file
3. FastAPI webhook endpoint for Alertmanager POSTs (mounted at /api/alerts/webhook)
4. Synthetic alert testing function

Integration:
    from prismatic.gateway.alert_manager import create_alert_webhook_route
    app.include_router(create_alert_webhook_route())
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.gateway.alert_manager")

# ── Environment-based configuration ──────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("PRISMATIC_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("PRISMATIC_TELEGRAM_CHAT_ID", "")
SLACK_WEBHOOK_URL = os.environ.get("PRISMATIC_SLACK_WEBHOOK_URL", "")
ALERT_LOG_PATH = os.environ.get(
    "PRISMATIC_ALERT_LOG",
    os.path.join(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"), "alerts.log"),
)

# Alert thresholds
HIGH_LOCK_CONTENTION_THRESHOLD = int(
    os.environ.get("PRISMATIC_ALERT_LOCK_CONTENTION", "5")
)
AGENT_STALL_MINUTES = int(os.environ.get("PRISMATIC_ALERT_AGENT_STALL_MINUTES", "15"))
CREDIT_BURN_THRESHOLD = int(os.environ.get("PRISMATIC_ALERT_CREDIT_BURN", "1000"))
STALE_QUEUE_MINUTES = int(os.environ.get("PRISMATIC_ALERT_STALE_QUEUE_MINUTES", "15"))


# ── Alert Rule Definitions ───────────────────────────────────

class AlertRule:
    """A single alert rule with name, expression description, and severity."""

    def __init__(
        self,
        name: str,
        description: str,
        severity: str,
        threshold_hint: str,
    ):
        self.name = name
        self.description = description
        self.severity = severity  # critical, warning, info
        self.threshold_hint = threshold_hint


# The four alert rules requested by GRO-1584. GRO-3500's StaleQueue alert is
# evaluated by AlertEvaluator without changing the public /alerts/rules count.
ALERT_RULES = {
    "HighLockContention": AlertRule(
        name="HighLockContention",
        description="5+ waiters contending for the same file lock simultaneously",
        severity="critical",
        threshold_hint=">= 5 simultaneous waiters",
    ),
    "AgentStall": AlertRule(
        name="AgentStall",
        description="Zero agent executions observed while dispatch should be active",
        severity="critical",
        threshold_hint="0 executions in lookback window",
    ),
    "CreditBurnRate": AlertRule(
        name="CreditBurnRate",
        description="Credit burn rate exceeds configured threshold",
        severity="warning",
        threshold_hint=f">{CREDIT_BURN_THRESHOLD} credits/hr",
    ),
    "CircuitBreakerTrip": AlertRule(
        name="CircuitBreakerTrip",
        description="A circuit breaker has tripped for an issue/agent pair",
        severity="critical",
        threshold_hint="breaker_state == tripped",
    ),
}
STALE_QUEUE_RULE = AlertRule(
    name="StaleQueue",
    description="Pending dispatch queue items are older than the stale threshold",
    severity="critical",
    threshold_hint=f"oldest pending item > {STALE_QUEUE_MINUTES}min",
)


# ── Routing Tree ─────────────────────────────────────────────

class AlertRouter:
    """Routes alerts to sinks based on severity.

    Routing tree:
        critical → Telegram
        warning  → Slack (hermes-feed)
        info     → log file
    """

    def __init__(self):
        self._alert_log_path = Path(ALERT_LOG_PATH)
        self._alert_log_path.parent.mkdir(parents=True, exist_ok=True)

    def route(self, alert: dict[str, Any]) -> list[str]:
        """Route an alert to one or more sinks. Returns list of sink names that fired."""
        severity = alert.get("severity", "info")
        fired: list[str] = []

        if severity == "critical":
            self._send_telegram(alert)
            fired.append("telegram")
            # Also log criticals
            self._log_alert(alert)
            fired.append("log")

        elif severity == "warning":
            self._send_slack(alert)
            fired.append("slack")
            self._log_alert(alert)
            fired.append("log")

        else:  # info
            self._log_alert(alert)
            fired.append("log")

        return fired

    def _send_telegram(self, alert: dict[str, Any]) -> None:
        """Send alert to Telegram via Bot API."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning(
                "Telegram not configured (missing PRISMATIC_TELEGRAM_BOT_TOKEN or "
                "PRISMATIC_TELEGRAM_CHAT_ID). Alert dropped: %s",
                alert.get("name", "?"),
            )
            return

        try:
            import urllib.request

            summary = alert.get("summary", alert.get("name", "Unknown alert"))
            severity = alert.get("severity", "unknown").upper()
            text = f"🚨 *{severity} ALERT*\\n\\n*{summary}*"

            if alert.get("details"):
                text += f"\\n\\n{alert['details']}"

            payload = json.dumps({
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            }).encode()

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Alert routed to Telegram: %s", alert.get("name"))
                else:
                    logger.warning("Telegram send failed: HTTP %s", resp.status)

        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)

    def _send_slack(self, alert: dict[str, Any]) -> None:
        """Send alert to Slack via webhook."""
        if not SLACK_WEBHOOK_URL:
            logger.warning(
                "Slack not configured (missing PRISMATIC_SLACK_WEBHOOK_URL). "
                "Alert dropped: %s",
                alert.get("name", "?"),
            )
            return

        try:
            import urllib.request

            severity = alert.get("severity", "unknown").upper()
            summary = alert.get("summary", alert.get("name", "Unknown alert"))
            emoji = "🔴" if severity == "CRITICAL" else "🟡"

            payload = json.dumps({
                "text": f"{emoji} *[{severity}]* {summary}",
                "channel": "#hermes-feed",
            }).encode()

            req = urllib.request.Request(
                SLACK_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Alert routed to Slack: %s", alert.get("name"))
                else:
                    logger.warning("Slack send failed: HTTP %s", resp.status)

        except Exception as exc:
            logger.error("Failed to send Slack alert: %s", exc)

    def _log_alert(self, alert: dict[str, Any]) -> None:
        """Log alert to the alert log file."""
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "name": alert.get("name", "unknown"),
                "severity": alert.get("severity", "unknown"),
                "summary": alert.get("summary", ""),
                "details": alert.get("details", ""),
            }
            with open(self._alert_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.error("Failed to log alert: %s", exc)


# ── Alert Evaluator ──────────────────────────────────────────

class AlertEvaluator:
    """Evaluates telemetry against alert rules and triggers routing.

    Designed to work with the existing TelemetryCollector from prismatic/telemetry.py.
    """

    def __init__(self, telemetry_collector=None, router: AlertRouter | None = None):
        self._telemetry = telemetry_collector
        self._router = router or AlertRouter()
        self._last_completion_time: float | None = None

    def evaluate(self, hours: int = 1) -> list[dict[str, Any]]:
        """Evaluate all alert rules. Returns list of triggered alert dicts."""
        triggered: list[dict[str, Any]] = []

        if self._telemetry is None:
            logger.warning("AlertEvaluator has no telemetry collector — skipping evaluation")
            return triggered

        data = self._telemetry.get_dashboard_data(hours=hours)
        queue = self._queue_snapshot(data)

        # ── HighLockContention ────────────────────────────
        lock_waiters = self._count_lock_waiters()
        if lock_waiters >= HIGH_LOCK_CONTENTION_THRESHOLD:
            triggered.append({
                "name": "HighLockContention",
                "severity": ALERT_RULES["HighLockContention"].severity,
                "summary": (
                    f"HighLockContention: {lock_waiters} waiters contending "
                    f"for file locks (threshold: {HIGH_LOCK_CONTENTION_THRESHOLD})"
                ),
                "details": f"current_waiters={lock_waiters} threshold={HIGH_LOCK_CONTENTION_THRESHOLD}",
            })

        # ── AgentStall / zero-execution window ─────────────
        total_runs = data.get("total_agent_runs", 0)
        if total_runs == 0:
            pending_depth = int(queue.get("pending_queue_depth", 0) or 0)
            stale_depth = int(queue.get("stale_queue_depth", 0) or 0)
            failing_layer = (
                "execution/consumer"
                if pending_depth or stale_depth
                else "telemetry/execution"
            )
            triggered.append({
                "name": "AgentStall",
                "severity": ALERT_RULES["AgentStall"].severity,
                "summary": (
                    f"AgentStall: zero agent executions in {hours}h window "
                    f"(failing_layer={failing_layer})"
                ),
                "details": (
                    f"failing_layer={failing_layer} total_runs_in_window={total_runs} "
                    f"window_hours={hours} pending_queue_depth={pending_depth} "
                    f"stale_queue_depth={stale_depth}"
                ),
            })

        # ── StaleQueue ─────────────────────────────────────
        oldest_age_sec = int(queue.get("stale_queue_oldest_age_sec", 0) or 0)
        stale_depth = int(queue.get("stale_queue_depth", 0) or 0)
        stale_threshold_sec = STALE_QUEUE_MINUTES * 60
        if stale_depth > 0 or oldest_age_sec >= stale_threshold_sec:
            pending_depth = int(queue.get("pending_queue_depth", 0) or 0)
            triggered.append({
                "name": "StaleQueue",
                "severity": STALE_QUEUE_RULE.severity,
                "summary": (
                    "StaleQueue: dispatch queue has stale work "
                    "(failing_layer=queue/dispatcher)"
                ),
                "details": (
                    "failing_layer=queue/dispatcher "
                    f"pending_queue_depth={pending_depth} stale_queue_depth={stale_depth} "
                    f"oldest_pending_age_sec={oldest_age_sec} "
                    f"threshold_sec={stale_threshold_sec}"
                ),
            })

        # ── CreditBurnRate ───────────────────────────────
        burn_rate = data.get("credit_burn_rate", 0)
        if burn_rate > CREDIT_BURN_THRESHOLD:
            triggered.append({
                "name": "CreditBurnRate",
                "severity": ALERT_RULES["CreditBurnRate"].severity,
                "summary": (
                    f"CreditBurnRate: {burn_rate:.0f} credits/hr exceeds "
                    f"threshold of {CREDIT_BURN_THRESHOLD} credits/hr"
                ),
                "details": (
                    f"burn_rate={burn_rate:.0f} threshold={CREDIT_BURN_THRESHOLD} "
                    f"total_credits={data.get('total_credits', 0)}"
                ),
            })

        # ── CircuitBreakerTrip ────────────────────────────
        breakers_tripped = data.get("breakers_tripped", 0)
        if breakers_tripped > 0:
            triggered.append({
                "name": "CircuitBreakerTrip",
                "severity": ALERT_RULES["CircuitBreakerTrip"].severity,
                "summary": (
                    f"CircuitBreakerTrip: {breakers_tripped} circuit breaker(s) "
                    f"currently tripped"
                ),
                "details": f"tripped_breaker_count={breakers_tripped}",
            })

        # ── Route all triggered alerts ────────────────────
        for alert in triggered:
            self._router.route(alert)

        return triggered

    def _queue_snapshot(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return dispatch-queue freshness metrics for stall attribution.

        The evaluator accepts pre-computed metrics from telemetry dashboard data,
        then falls back to the webhook queue SQLite database. This keeps tests
        deterministic while allowing the cron health check to point at the
        failing layer when the queue is visibly backing up.
        """
        keys = {
            "pending_queue_depth",
            "stale_queue_depth",
            "stale_queue_oldest_age_sec",
            "queue_db_path",
        }
        if any(k in data for k in keys):
            return {k: data.get(k, 0) for k in keys}

        state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))
        db_path = state_dir / "linear_webhook_queue.db"
        snapshot: dict[str, Any] = {
            "pending_queue_depth": 0,
            "stale_queue_depth": 0,
            "stale_queue_oldest_age_sec": 0,
            "queue_db_path": str(db_path),
        }
        if not db_path.exists():
            return snapshot

        try:
            conn = sqlite3.connect(str(db_path))
            try:
                cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(linear_webhook_queue)")
                }
                timestamp_col = (
                    "received_at"
                    if "received_at" in cols
                    else "queued_at"
                    if "queued_at" in cols
                    else None
                )
                if "dispatch_status" not in cols or timestamp_col is None:
                    return snapshot

                pending = conn.execute(
                    "SELECT COUNT(*) FROM linear_webhook_queue "
                    "WHERE dispatch_status = 'pending'"
                ).fetchone()
                stale = conn.execute(
                    "SELECT COUNT(*) FROM linear_webhook_queue "
                    "WHERE dispatch_status = 'stale'"
                ).fetchone()
                oldest = conn.execute(
                    f"SELECT MIN({timestamp_col}) FROM linear_webhook_queue "
                    "WHERE dispatch_status IN ('pending', 'stale')"
                ).fetchone()

                snapshot["pending_queue_depth"] = int(pending[0] if pending else 0)
                snapshot["stale_queue_depth"] = int(stale[0] if stale else 0)
                if oldest and oldest[0] is not None:
                    snapshot["stale_queue_oldest_age_sec"] = self._age_seconds(oldest[0])
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not inspect webhook queue freshness: %s", exc)
        return snapshot

    def _age_seconds(self, value: Any) -> int:
        """Convert SQLite epoch/ISO timestamp values into age in seconds."""
        now = datetime.now(timezone.utc)
        try:
            if isinstance(value, (int, float)):
                return max(0, int(now.timestamp() - float(value)))
            text = str(value).strip()
            try:
                return max(0, int(now.timestamp() - float(text)))
            except ValueError:
                pass
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int((now - parsed).total_seconds()))
        except Exception:
            return 0

    def _count_lock_waiters(self) -> int:
        """Count how many locks currently have multiple waiters contending.

        Reads the centralized swarm_locks.json registry.
        """
        try:
            swarm_lock_path = Path(
                os.environ.get(
                    "PRISMATIC_HOME",
                    os.environ.get("HOME", "."),
                )
            ) / ".antigravity" / "swarm_locks.json"

            if not swarm_lock_path.exists():
                return 0

            with open(swarm_lock_path) as f:
                locks = json.load(f)

            if not isinstance(locks, list):
                return 0

            # Count locks by file path; >1 lock on same file = contention
            from collections import Counter

            file_counts = Counter(
                lock.get("filePath", "") for lock in locks
            )
            return sum(1 for count in file_counts.values() if count > 1)

        except Exception:
            return 0


# ── FastAPI Webhook Endpoint ─────────────────────────────────

def create_alert_webhook_route(router: AlertRouter | None = None):
    """Create a FastAPI route for Alertmanager webhook POSTs.

    Mounts at /api/alerts/webhook. Accepts Alertmanager-format JSON payloads,
    evaluates local alert rules, and routes through the AlertRouter.

    Usage in server.py:
        from prismatic.gateway.alert_manager import create_alert_webhook_route
        app.include_router(create_alert_webhook_route())
    """
    from fastapi import APIRouter, Body, Response

    alert_router = router or AlertRouter()
    api = APIRouter()

    @api.post("/alerts/webhook")
    async def alerts_webhook(body: dict | list = Body(...)) -> dict[str, Any]:
        """Receive alerts from Alertmanager and route to configured sinks.

        Accepts a single alert or array of alerts in Alertmanager format.
        Each alert is routed through the severity-based routing tree.
        """

        # Normalize to list
        alerts = body if isinstance(body, list) else [body]
        results = []

        for raw in alerts:
            alert_name = raw.get("labels", {}).get("alertname", raw.get("name", "unknown"))
            severity = raw.get("labels", {}).get("severity", raw.get("severity", "info"))
            summary = raw.get("annotations", {}).get("summary", raw.get("summary", ""))
            details = raw.get("annotations", {}).get("description", raw.get("details", ""))

            alert = {
                "name": alert_name,
                "severity": severity,
                "summary": summary or str(raw.get("annotations", {})),
                "details": details,
            }

            fired = alert_router.route(alert)
            results.append({"name": alert_name, "routed_to": fired})

        ok_count = sum(1 for r in results if r["routed_to"])
        fail_count = len(results) - ok_count
        status_code = 200 if fail_count == 0 else 207

        return Response(
            status_code=status_code,
            content=json.dumps({
                "status": "delivered" if fail_count == 0 else "partial",
                "total": len(results),
                "ok": ok_count,
                "failed": fail_count,
                "results": results,
            }),
            media_type="application/json",
        )

    @api.get("/alerts/rules")
    async def list_alert_rules() -> dict[str, Any]:
        """List all configured alert rules."""
        return {
            "rules": {
                name: {
                    "description": rule.description,
                    "severity": rule.severity,
                    "threshold": rule.threshold_hint,
                }
                for name, rule in ALERT_RULES.items()
            }
        }

    @api.post("/alerts/test")
    async def test_synthetic_alert(body: dict | list = Body(...)) -> dict[str, Any]:
        """Fire synthetic test alerts for verification.

        Accepts alert definitions and routes them through the real routing tree.
        Useful for testing Telegram/Slack/log sinks without waiting for real alerts.
        """

        alerts = body if isinstance(body, list) else [body]

        if not alerts:
            # Default synthetic test: fire all five alert types
            alerts = [
                {
                    "name": "HighLockContention",
                    "severity": "critical",
                    "summary": "[SYNTHETIC TEST] HighLockContention: simulated 6 waiters on file",
                    "details": "synthetic=true waiters=6 threshold=5",
                },
                {
                    "name": "AgentStall",
                    "severity": "critical",
                    "summary": "[SYNTHETIC TEST] AgentStall: simulated zero executions",
                    "details": "synthetic=true failing_layer=execution/consumer completions=0 minutes_since_last=20",
                },
                {
                    "name": "StaleQueue",
                    "severity": "critical",
                    "summary": "[SYNTHETIC TEST] StaleQueue: simulated stale pending queue",
                    "details": "synthetic=true failing_layer=queue/dispatcher pending_queue_depth=3 stale_queue_depth=1",
                },
                {
                    "name": "CreditBurnRate",
                    "severity": "warning",
                    "summary": "[SYNTHETIC TEST] CreditBurnRate: simulated 1500 credits/hr",
                    "details": "synthetic=true burn_rate=1500 threshold=1000",
                },
                {
                    "name": "CircuitBreakerTrip",
                    "severity": "critical",
                    "summary": "[SYNTHETIC TEST] CircuitBreakerTrip: simulated breaker on GRO-0000",
                    "details": "synthetic=true issue_id=GRO-0000 agent=test",
                },
            ]

        results = []
        for alert_def in alerts:
            fired = alert_router.route(alert_def)
            results.append({
                "name": alert_def.get("name", "unknown"),
                "severity": alert_def.get("severity", "info"),
                "routed_to": fired,
            })

        return Response(
            status_code=200,
            content=json.dumps({
                "status": "synthetic_alerts_fired",
                "total": len(results),
                "results": results,
                "note": (
                    "Synthetic alerts were routed through the real routing tree. "
                    "Check Telegram, Slack, and alert log for delivery."
                ),
            }),
            media_type="application/json",
        )

    return api


# ── CLI Helper ───────────────────────────────────────────────

def fire_synthetic_alerts() -> None:
    """CLI entry point: fire synthetic alerts through the routing tree.

    Usage:
        python -m prismatic.gateway.alert_manager
    """
    router = AlertRouter()
    test_alerts = [
        {
            "name": "HighLockContention",
            "severity": "critical",
            "summary": "[SYNTHETIC CLI] HighLockContention test",
            "details": "synthetic=true",
        },
        {
            "name": "CreditBurnRate",
            "severity": "warning",
            "summary": "[SYNTHETIC CLI] CreditBurnRate test",
            "details": "synthetic=true",
        },
        {
            "name": "InfoTest",
            "severity": "info",
            "summary": "[SYNTHETIC CLI] Info-level test alert",
            "details": "synthetic=true",
        },
    ]
    for alert in test_alerts:
        fired = router.route(alert)
        print(f"  {alert['name']} ({alert['severity']}) → {fired}")


if __name__ == "__main__":
    fire_synthetic_alerts()
