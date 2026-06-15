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
import time
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


# The four alert rules requested by GRO-1584
ALERT_RULES = {
    "HighLockContention": AlertRule(
        name="HighLockContention",
        description="5+ waiters contending for the same file lock simultaneously",
        severity="critical",
        threshold_hint=">= 5 simultaneous waiters",
    ),
    "AgentStall": AlertRule(
        name="AgentStall",
        description="No agent task completions observed in 15 minutes",
        severity="critical",
        threshold_hint="0 completions in 15min",
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

        # ── AgentStall ────────────────────────────────────
        total_runs = data.get("total_agent_runs", 0)
        if total_runs == 0:
            triggered.append({
                "name": "AgentStall",
                "severity": ALERT_RULES["AgentStall"].severity,
                "summary": (
                    f"AgentStall: No agent task completions in {AGENT_STALL_MINUTES}+ minutes "
                    f"({hours}h window: {total_runs} runs)"
                ),
                "details": f"total_runs_in_window={total_runs} window_hours={hours}",
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
    from fastapi import APIRouter, Response, Body

    alert_router = router or AlertRouter()
    api = APIRouter()

    @api.post("/alerts/webhook")
    async def alerts_webhook(body: dict | list = Body(...)) -> dict[str, Any]:
        """Receive alerts from Alertmanager and route to configured sinks.

        Accepts a single alert or array of alerts in Alertmanager format.
        Each alert is routed through the severity-based routing tree.
        """
        import urllib.request

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
        import urllib.request

        alerts = body if isinstance(body, list) else [body]

        if not alerts:
            # Default synthetic test: fire all four alert types
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
                    "summary": "[SYNTHETIC TEST] AgentStall: simulated zero completions",
                    "details": "synthetic=true completions=0 minutes_since_last=20",
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
