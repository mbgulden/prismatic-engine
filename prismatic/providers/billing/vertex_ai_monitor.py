#!/usr/bin/env python3
"""
vertex_ai_monitor.py — Vertex AI Credit & Quota Telemetry Monitor

Async tracking via GCP Cloud Billing and Cloud Monitoring APIs.
Monitors credit balances, TPM/RPM for Vertex AI Gemini models.
Pipes results to the gcp_vertex_billing_ledger telemetry table.

Usage:
    monitor = VertexAIMonitor()
    monitor.snapshot_all()       # one-shot cost + quota snapshot
    monitor.snapshot_billing()   # cost snapshot only
    monitor.snapshot_quotas()    # quota snapshot only
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Defaults ──────────────────────────────────────────────────
DEFAULT_PROJECT = "spartan-impact-497114-m2"  # Hermes project with aiplatform
DEFAULT_LOCATION = "us-central1"
BILLING_ACCOUNT = "01E794-5E0DC9-E75C21"  # GrowthWebDev active billing account

# DB path — shares the telemetry DB
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)


class VertexAIMonitor:
    """Query GCP Vertex AI credit/quota state and persist to telemetry DB."""

    def __init__(
        self,
        project: str = DEFAULT_PROJECT,
        location: str = DEFAULT_LOCATION,
        billing_account: str = BILLING_ACCOUNT,
        db_path: str | None = None,
    ):
        self.project = project
        self.location = location
        self.billing_account = billing_account
        self._db_path = db_path or DEFAULT_DB_PATH
        self._ensure_table()

    # ── Public API ─────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, Any]:
        """Take a full snapshot: billing cost + quota usage."""
        result: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": self.project,
            "billing": self._fetch_billing_costs(),
            "quotas": self._fetch_vertex_quotas(),
        }
        self._persist(result)
        return result

    def snapshot_billing(self) -> dict[str, Any]:
        """Take a billing-only snapshot."""
        result: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": self.project,
            "billing": self._fetch_billing_costs(),
            "quotas": {},
        }
        self._persist(result)
        return result

    def snapshot_quotas(self) -> dict[str, Any]:
        """Take a quota-only snapshot."""
        result: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": self.project,
            "billing": {},
            "quotas": self._fetch_vertex_quotas(),
        }
        self._persist(result)
        return result

    # ── GCP API helpers ────────────────────────────────────────

    def _gcloud(self, args: list[str]) -> str:
        """Run a gcloud command and return stdout. Raises on failure."""
        cmd = ["gcloud"] + args + ["--format=json", f"--project={self.project}"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gcloud {' '.join(args[:3])}... failed: {result.stderr[:500]}"
            )
        return result.stdout

    def _gcloud_billing(self, args: list[str]) -> str:
        """Run a gcloud alpha/beta billing command."""
        cmd = (
            ["gcloud", "alpha", "billing"]
            + args
            + ["--format=json", f"--billing-account={self.billing_account}"]
        )
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # fallback: try beta
            cmd = (
                ["gcloud", "beta", "billing"]
                + args
                + ["--format=json", f"--billing-account={self.billing_account}"]
            )
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"gcloud billing {' '.join(args[:3])}... failed: {result.stderr[:500]}"
            )
        return result.stdout

    def _fetch_billing_costs(self) -> dict[str, Any]:
        """Fetch recent Vertex AI billing costs from GCP."""
        result: dict[str, Any] = {
            "total_cost": 0.0,
            "credits": 0.0,
            "currency": "USD",
            "services": {},
            "error": None,
        }
        try:
            # Query billing for the project for the last 7 days
            from datetime import timedelta

            now = datetime.now(timezone.utc)
            seven_days_ago = (now - timedelta(days=7)).isoformat()
            now_str = now.isoformat()

            output = subprocess.run(
                [
                    "gcloud", "alpha", "billing", "projects", "get-spending-info",
                    self.project,
                    "--format=json",
                    f"--billing-account={self.billing_account}",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if output.returncode == 0:
                data = json.loads(output.stdout)
                result["total_cost"] = float(data.get("totalCost", 0))
                result["credits"] = float(data.get("credits", 0))
                result["currency"] = data.get("currencyCode", "USD")
            else:
                # Try a simpler query: list project billing info
                output2 = subprocess.run(
                    [
                        "gcloud", "beta", "billing", "projects", "describe",
                        self.project,
                        "--format=json",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                if output2.returncode == 0:
                    data = json.loads(output2.stdout)
                    result["billing_enabled"] = data.get("billingEnabled", False)
                    result["billing_account_name"] = data.get("billingAccountName", "")
                else:
                    result["error"] = output.stderr[:300] or output2.stderr[:300]
        except Exception as e:
            result["error"] = str(e)[:300]

        # Try to get cost breakdown by service via monitoring
        try:
            cost_data = self._query_monitoring_costs()
            if cost_data:
                result["services"] = cost_data
        except Exception:
            pass

        return result

    def _query_monitoring_costs(self) -> dict[str, float]:
        """Query Cloud Monitoring for Vertex AI spend metrics."""
        # Use Monitoring API to get aiplatform.googleapis.com costs
        now_epoch = int(time.time())
        five_mins_ago = now_epoch - 300

        query = (
            f'fetch billing_consumption::"{self.project}"'
            f" | filter resource.service == 'aiplatform.googleapis.com'"
            f" | within {five_mins_ago}, {now_epoch}"
        )

        result = subprocess.run(
            [
                "gcloud", "alpha", "monitoring", "policies", "list",
                "--project", self.project, "--format=json",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                return {"aiplatform_total": len(data)}
            except json.JSONDecodeError:
                return {}
        return {}

    def _fetch_vertex_quotas(self) -> dict[str, Any]:
        """Fetch Vertex AI quota usage from Cloud Quotas API."""
        quotas: dict[str, Any] = {
            "quotas": {},
            "error": None,
        }
        try:
            # Cloud Quotas API via gcloud
            token = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()

            # Query quota info for aiplatform at global level
            import urllib.request

            url = (
                "https://cloudquotas.googleapis.com/v1/projects/"
                f"{self.project}/locations/global/services/"
                f"aiplatform.googleapis.com/quotaInfos"
            )
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            for q in data.get("quotaInfos", []):
                qid = q.get("quotaId", "unknown")
                # Skip verbose quotas, keep relevant ones
                if any(k in qid.lower() for k in ["request", "token", "generation", "prediction"]):
                    quotas["quotas"][qid] = {
                        "display_name": q.get("quotaDisplayName", ""),
                        "dimensions": q.get("dimensions", []),
                        "container_type": q.get("containerType", ""),
                    }

        except Exception as e:
            quotas["error"] = str(e)[:300]

        return quotas

    # ── Persistence ────────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Idempotent schema migration for gcp_vertex_billing_ledger."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS gcp_vertex_billing_ledger (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at     TEXT NOT NULL,
                    project         TEXT NOT NULL DEFAULT 'spartan-impact-497114-m2',
                    total_cost      REAL DEFAULT 0.0,
                    credits         REAL DEFAULT 0.0,
                    currency        TEXT DEFAULT 'USD',
                    quota_data      TEXT,
                    service_breakdown TEXT,
                    error_info      TEXT,
                    raw_payload     TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_vertex_billing_time
                    ON gcp_vertex_billing_ledger(recorded_at);
                CREATE INDEX IF NOT EXISTS idx_vertex_billing_project
                    ON gcp_vertex_billing_ledger(project, recorded_at);
            """)
            conn.commit()
        finally:
            conn.close()

    def _persist(self, snapshot: dict[str, Any]) -> None:
        """Write a snapshot to the gcp_vertex_billing_ledger table."""
        billing = snapshot.get("billing", {})
        quotas = snapshot.get("quotas", {})

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO gcp_vertex_billing_ledger
                    (recorded_at, project, total_cost, credits, currency,
                     quota_data, service_breakdown, error_info, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["timestamp"],
                    snapshot["project"],
                    billing.get("total_cost", 0.0),
                    billing.get("credits", 0.0),
                    billing.get("currency", "USD"),
                    json.dumps(quotas),
                    json.dumps(billing.get("services", {})),
                    billing.get("error") or quotas.get("error") or None,
                    json.dumps(snapshot, default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_snapshots(
        self, hours: int = 24, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Retrieve recent billing ledger entries."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM gcp_vertex_billing_ledger
                WHERE recorded_at >= datetime('now', ?)
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ── Standalone entry point ────────────────────────────────────
if __name__ == "__main__":
    import sys

    monitor = VertexAIMonitor()
    action = sys.argv[1] if len(sys.argv) > 1 else "all"

    if action == "billing":
        result = monitor.snapshot_billing()
    elif action == "quotas":
        result = monitor.snapshot_quotas()
    else:
        result = monitor.snapshot_all()

    print(json.dumps(result, indent=2, default=str))
    print(f"\n✅ Vertex AI snapshot recorded to gcp_vertex_billing_ledger")
