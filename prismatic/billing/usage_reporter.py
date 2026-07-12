"""prismatic/billing/usage_reporter.py — Stripe Billing Meter usage reporter.

Reads completed GPU/LLM job metrics from the telemetry credit ledger
and reports them to Stripe Billing Meter API for usage-based billing.

Architecture:
    Job Complete → record_usage() in cost_attribution.py
    → UsageReporter.poll() picks up recent unbilled records
    → stripe.billing.MeterEvent.create() sends to Stripe

Two modes:
    - Real-time: report_job() called inline after job completion
    - Batch: poll() scans unbilled ledger entries every N minutes
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────
DEFAULT_METER_NAME = "prismatic_gpu_credits"
DEFAULT_BATCH_SIZE = 100
REPORT_WINDOW_HOURS = 1  # how far back to look for unbilled records


class UsageReporterError(Exception):
    """Raised on Stripe MeterEvent API failures."""


class UsageReporter:
    """Reports GPU/job credit usage to Stripe Billing Meter.

    Reads completed job records from the credit ledger and submits
    meter events to Stripe for usage-based billing.

    Args:
        stripe_api_key: Stripe secret key. Defaults to STRIPE_SECRET_KEY env.
        meter_name: Stripe Billing Meter name (default: prismatic_gpu_credits).
        ledger: Optional CreditLedger instance for reading transaction log.
    """

    def __init__(
        self,
        stripe_api_key: str | None = None,
        meter_name: str | None = None,
        ledger: Any | None = None,
    ):
        self._stripe_api_key = stripe_api_key or os.environ.get(
            "STRIPE_SECRET_KEY", ""
        )
        self._meter_name = meter_name or os.environ.get(
            "STRIPE_METER_NAME", DEFAULT_METER_NAME
        )
        self._ledger = ledger

    # ── Real-time Reporting ────────────────────────────────

    def report_job(
        self,
        customer_id: str,
        credits_consumed: int,
        job_id: str = "",
        model: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        """Report a single job's credit consumption to Stripe immediately.

        This is the real-time path — call from the job completion hook.

        Args:
            customer_id: Stripe customer ID (cus_xxx).
            credits_consumed: Credits consumed (micro-dollars).
            job_id: Unique run identifier.
            model: Model name for metadata.
            agent: Agent name for metadata.

        Returns:
            Stripe MeterEvent response dict.

        Raises:
            UsageReporterError on API failure.
        """
        return self._send_meter_event(
            customer_id=customer_id,
            value=credits_consumed,
            identifier=job_id or f"job-{int(time.time())}",
            metadata={
                "model": model or "unknown",
                "agent": agent or "unknown",
                "event_type": "gpu_credits",
            },
        )

    # ── Batch Reporting ────────────────────────────────────

    def poll(
        self,
        lookback_hours: int | None = None,
        max_events: int | None = None,
    ) -> dict[str, Any]:
        """Scan recent credit ledger entries and report unbilled usage.

        Looks for credit_deduct transactions in the last N hours that
        haven't been reported yet. Uses a simple marker approach:
        the last reported transaction ID is stored in a local file.

        Args:
            lookback_hours: How far back to scan (default REPORT_WINDOW_HOURS).
            max_events: Max events to submit in one batch (default BATCH_SIZE).

        Returns:
            Summary dict with sent count and any errors.
        """
        lookback = lookback_hours or REPORT_WINDOW_HOURS
        max_ev = max_events or DEFAULT_BATCH_SIZE

        if not self._ledger:
            return {
                "sent": 0,
                "error": "No credit ledger provided — cannot poll",
            }

        last_id = self._get_last_reported_id()

        # Get recent deductions from all tenants
        # We read from the ledger's transaction log per tenant.
        # For simplicity, iterate known tenants.
        # In production, this would be a SQL query JOINing on
        # credit_transactions with a reported marker column.

        unbilled: list[dict] = []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=lookback)
        ).isoformat()

        # Attempt to discover tenants from the ledger
        # (SQLite implementation keeps tenant_balance table)
        try:
            if hasattr(self._ledger, "_get_conn"):
                conn = self._ledger._get_conn()
                cursor = conn.execute(
                    "SELECT tenant_id FROM tenant_balance"
                )
                tenants = [r["tenant_id"] for r in cursor.fetchall()]
            else:
                tenants = []
        except Exception:
            tenants = []

        for tenant_id in tenants:
            try:
                txn_log = self._ledger.get_transaction_log(tenant_id, limit=200)
            except Exception:
                continue

            for txn in txn_log:
                if txn["id"] <= last_id:
                    continue
                if txn["delta"] >= 0:
                    continue  # only report deductions
                if txn.get("created_at", "") < cutoff:
                    continue

                unbilled.append({
                    "tenant_id": tenant_id,
                    "txn_id": txn["id"],
                    "credits": abs(txn["delta"]),
                    "reason": txn.get("reason", ""),
                    "created_at": txn.get("created_at", ""),
                })

                if len(unbilled) >= max_ev:
                    break

            if len(unbilled) >= max_ev:
                break

        if not unbilled:
            return {"sent": 0, "scanned_tenants": len(tenants)}

        # Send to Stripe
        sent = 0
        errors: list[str] = []
        max_txn_id = last_id

        for item in unbilled:
            try:
                self._send_meter_event(
                    customer_id=item["tenant_id"],
                    value=item["credits"],
                    identifier=f"txn-{item['txn_id']}",
                    metadata={
                        "reason": item["reason"][:100],
                        "event_type": "batch_gpu_credits",
                    },
                )
                sent += 1
                max_txn_id = max(max_txn_id, item["txn_id"])
            except UsageReporterError as exc:
                errors.append(f"tenant={item['tenant_id']}: {exc}")
                if len(errors) > 5:
                    errors.append("... (truncated)")
                    break

        # Persist last reported marker
        self._set_last_reported_id(max_txn_id)

        return {
            "sent": sent,
            "errors": errors,
            "scanned_tenants": len(tenants),
            "unbilled_found": len(unbilled),
            "last_reported_id": max_txn_id,
        }

    # ── Stripe API ─────────────────────────────────────────

    def _send_meter_event(
        self,
        customer_id: str,
        value: int,
        identifier: str = "",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Submit a meter event to Stripe Billing Meter API.

        Uses the raw REST API via requests (no stripe SDK dependency
        for the meter endpoint, which may require beta access).

        Stripe Billing Meter API:
        POST /v1/billing/meter_events
        """
        if not self._stripe_api_key:
            raise UsageReporterError(
                "STRIPE_SECRET_KEY not configured"
            )

        try:
            import requests
        except ImportError:
            raise UsageReporterError("requests library required")

        event_id = identifier or f"evt-{int(time.time()*1000)}"

        payload = {
            "event_name": self._meter_name,
            "payload": {
                "stripe_customer_id": customer_id,
                "value": str(value),
            },
            "identifier": event_id,
        }

        if metadata:
            payload["payload"]["metadata"] = json.dumps(metadata)

        try:
            resp = requests.post(
                "https://api.stripe.com/v1/billing/meter_events",
                auth=(self._stripe_api_key, ""),
                data={"json": json.dumps(payload)},
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                "Meter event sent: customer=%s value=%d id=%s",
                customer_id, value, event_id,
            )
            return result
        except requests.RequestException as exc:
            raise UsageReporterError(
                f"Stripe MeterEvent API error: {exc}"
            ) from exc

    # ── Checkpoint Tracking ────────────────────────────────

    def _get_checkpoint_path(self) -> str:
        """Path to the last-reported-ID checkpoint file."""
        state_dir = os.environ.get(
            "PRISMATIC_STATE_DIR", "./prismatic_state"
        )
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "usage_reporter_checkpoint.json")

    def _get_last_reported_id(self) -> int:
        """Read last reported transaction ID from checkpoint file."""
        path = self._get_checkpoint_path()
        try:
            with open(path) as f:
                data = json.load(f)
                return data.get("last_txn_id", 0)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return 0

    def _set_last_reported_id(self, txn_id: int) -> None:
        """Persist last reported transaction ID."""
        path = self._get_checkpoint_path()
        with open(path, "w") as f:
            json.dump({"last_txn_id": txn_id, "updated_at": datetime.now(
                timezone.utc
            ).isoformat()}, f)
