"""
prismatic/billing/cost_attribution.py — Client Cost Attribution Engine

Layers client/project cost attribution on top of the existing
telemetry_credit_ledger table in event_router.db. Provides:

1. Issue → Client/Project mapping (billing_mapping table)
2. Per-token cost recording with client_id/project_id dimensions
3. Monthly billing report generation (CSV + JSON)
4. Rolling 7-day cost projection model
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ── Default paths ──────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# ── Model pricing per token (USD) ──────────────────────────
# Values are per-token costs. Multiply by token count directly.
# Example: gpt-4 prompt = $0.03/1K tokens → $0.00003/token
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4":              {"prompt": 0.00003, "completion": 0.00006},
    "gpt-4-turbo":        {"prompt": 0.00001, "completion": 0.00003},
    "gpt-4o":             {"prompt": 0.000005, "completion": 0.000015},
    "gpt-4o-mini":        {"prompt": 0.00000015, "completion": 0.0000006},
    "gpt-3.5-turbo":      {"prompt": 0.0000015, "completion": 0.000002},
    "claude-3-opus":      {"prompt": 0.000015, "completion": 0.000075},
    "claude-3-sonnet":    {"prompt": 0.000003, "completion": 0.000015},
    "claude-3-haiku":     {"prompt": 0.00000025, "completion": 0.00000125},
    "claude-3.5-sonnet":  {"prompt": 0.000003, "completion": 0.000015},
    "gemini-1.5-pro":     {"prompt": 0.000007, "completion": 0.000021},
    "gemini-1.5-flash":   {"prompt": 0.00000015, "completion": 0.0000006},
    "gemini-2.5-pro":     {"prompt": 0.00000125, "completion": 0.00001},
    "gemini-2.5-flash":   {"prompt": 0.00000015, "completion": 0.0000006},
    "deepseek-v3":        {"prompt": 0.00000027, "completion": 0.0000011},
    "deepseek-r1":        {"prompt": 0.00000055, "completion": 0.00000219},
    "llama-3-70b":        {"prompt": 0.00000059, "completion": 0.00000079},
    "llama-3-8b":         {"prompt": 0.00000006, "completion": 0.00000006},
}


@dataclass
class BillingReport:
    """Aggregated billing data for a client/project."""
    client_id: str
    project_id: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    agent_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    period_start: str = ""
    period_end: str = ""


@dataclass
class CostProjection:
    """Rolling 7-day cost projection."""
    daily_costs: list[float] = field(default_factory=list)
    projected_monthly: float = 0.0
    average_daily: float = 0.0
    trend: str = "stable"  # "rising", "falling", "stable"
    confidence: str = "low"  # based on days of data available


class CostAttributionEngine:
    """Cost attribution layer on top of telemetry_credit_ledger.

    Manages billing_mapping (issue_id → client_id, project_id) and
    extends credit ledger entries with client/project dimensions.
    Generates billing reports and cost projections.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._ensure_tables()

    # ── Schema ─────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        """Create billing tables and migrate credit_ledger if needed."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        try:
            # ── billing_mapping: maps issues to client/project ──
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS billing_mapping (
                    issue_id    TEXT PRIMARY KEY,
                    client_id   TEXT NOT NULL,
                    project_id  TEXT NOT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_billing_mapping_client
                    ON billing_mapping(client_id);
                CREATE INDEX IF NOT EXISTS idx_billing_mapping_project
                    ON billing_mapping(project_id);
            """)

            # ── Ensure credit_ledger table exists (depended on by billing) ──
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS telemetry_credit_ledger (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    provider        TEXT NOT NULL,
                    model           TEXT,
                    credits_spent   INTEGER NOT NULL,
                    operation       TEXT,
                    recorded_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_credit_ledger_run
                    ON telemetry_credit_ledger(run_id);
            """)

            # ── Migrate credit_ledger: add client_id/project_id ──
            cursor = conn.execute("PRAGMA table_info(telemetry_credit_ledger)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "client_id" not in existing_cols:
                conn.execute(
                    "ALTER TABLE telemetry_credit_ledger "
                    "ADD COLUMN client_id TEXT"
                )
            if "project_id" not in existing_cols:
                conn.execute(
                    "ALTER TABLE telemetry_credit_ledger "
                    "ADD COLUMN project_id TEXT"
                )
            conn.commit()
        finally:
            conn.close()

    # ── Issue Attribution ──────────────────────────────────

    def set_attribution(
        self, issue_id: str, client_id: str, project_id: str
    ) -> None:
        """Map an issue to a client and project billing profile."""
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO billing_mapping
                   (issue_id, client_id, project_id)
                   VALUES (?, ?, ?)""",
                (issue_id, client_id, project_id),
            )
            conn.commit()

    def get_attribution(self, issue_id: str) -> tuple[str, str]:
        """Resolve client_id and project_id for a given issue.

        Returns ("unknown-client", "unassigned-project") if no mapping exists.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            cursor = conn.execute(
                "SELECT client_id, project_id FROM billing_mapping "
                "WHERE issue_id = ?",
                (issue_id,),
            )
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
            return "unknown-client", "unassigned-project"

    def get_all_attributions(self) -> list[dict[str, str]]:
        """Return all issue → client/project mappings."""
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT issue_id, client_id, project_id, created_at "
                "FROM billing_mapping ORDER BY created_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    # ── Cost Calculation ───────────────────────────────────

    @staticmethod
    def calculate_cost(
        model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Calculate USD cost for token usage by model.

        MODEL_PRICING stores per-token rates. Multiply directly.
        Falls back to $0.000002/token for unknown models.
        """
        # Normalize model name for lookup
        model_lower = model.lower().strip() if model else ""

        # Try exact match first
        rates = MODEL_PRICING.get(model_lower)
        if rates is None:
            # Try prefix match (e.g., "gpt-4-0613" → "gpt-4")
            for known in MODEL_PRICING:
                if model_lower.startswith(known):
                    rates = MODEL_PRICING[known]
                    break

        if rates is None:
            rates = {"prompt": 0.000002, "completion": 0.000002}

        prompt_cost = prompt_tokens * rates["prompt"]
        completion_cost = completion_tokens * rates["completion"]
        return round(prompt_cost + completion_cost, 6)

    # ── Usage Recording ────────────────────────────────────

    def record_usage(
        self,
        agent_id: str,
        issue_id: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        provider: str = "",
        run_id: str = "",
    ) -> float:
        """Record token usage with client/project attribution.

        Returns the calculated USD cost.
        Also pushes to the Prometheus TOKEN_SPEND_USD counter if available.
        """
        client_id, project_id = self.get_attribution(issue_id)
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)

        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id or f"billing-{issue_id}-{datetime.now(timezone.utc).timestamp()}",
                    agent_id,
                    provider or "billing-engine",
                    model,
                    int(cost * 100000),  # store as micro-dollars for integer precision
                    "token_usage",
                    datetime.now(timezone.utc).isoformat(),
                    client_id,
                    project_id,
                ),
            )
            conn.commit()

        # ── Update Prometheus counter ──
        try:
            from prismatic.telemetry.metrics import TOKEN_SPEND_USD
            TOKEN_SPEND_USD.labels(
                agent_id=agent_id, model=model, client=client_id
            ).inc(cost)
        except Exception:
            pass

        return cost

    def record_token_spend(
        self,
        client_id: str,
        project_id: str,
        agent_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Record token spend directly with known client/project (no issue lookup)."""
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)

        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"billing-direct-{datetime.now(timezone.utc).timestamp()}",
                    agent_id,
                    "billing-engine",
                    model,
                    int(cost * 100000),
                    "token_usage_direct",
                    datetime.now(timezone.utc).isoformat(),
                    client_id,
                    project_id,
                ),
            )
            conn.commit()
        return cost

    # ── Billing Reports ────────────────────────────────────

    def generate_report(
        self,
        client_id: str | None = None,
        project_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[BillingReport]:
        """Generate aggregated billing reports.

        Args:
            client_id: Filter by client (None = all clients)
            project_id: Filter by project (None = all projects)
            start_date: ISO date string for report start (None = 30 days ago)
            end_date: ISO date string for report end (None = now)

        Returns:
            List of BillingReport dataclasses, one per client/project combo.
        """
        if not start_date:
            start_date = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()
        if not end_date:
            end_date = datetime.now(timezone.utc).isoformat()

        # Query: aggregate by client + project from credit_ledger
        query = """
            SELECT
                COALESCE(client_id, 'unknown-client') as client_id,
                COALESCE(project_id, 'unassigned-project') as project_id,
                agent,
                model,
                SUM(credits_spent) as total_credits,
                COUNT(*) as entry_count
            FROM telemetry_credit_ledger
            WHERE recorded_at >= ? AND recorded_at <= ?
        """
        params: list[Any] = [start_date, end_date]

        if client_id:
            query += " AND client_id = ?"
            params.append(client_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        query += " GROUP BY client_id, project_id, agent, model"

        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        # Aggregate into BillingReport objects
        reports: dict[str, BillingReport] = {}

        for row in rows:
            key = f"{row['client_id']}::{row['project_id']}"
            if key not in reports:
                reports[key] = BillingReport(
                    client_id=row["client_id"],
                    project_id=row["project_id"],
                    period_start=start_date,
                    period_end=end_date,
                )

            report = reports[key]
            credits = row["total_credits"] or 0
            # Convert micro-dollars back to USD
            cost_usd = credits / 100000.0
            report.total_cost_usd += cost_usd

            # Agent breakdown
            agent = row["agent"]
            if agent not in report.agent_breakdown:
                report.agent_breakdown[agent] = {"cost_usd": 0.0, "entries": 0}
            report.agent_breakdown[agent]["cost_usd"] += cost_usd
            report.agent_breakdown[agent]["entries"] += row["entry_count"]

            # Model breakdown
            model = row["model"] or "unknown"
            if model not in report.model_breakdown:
                report.model_breakdown[model] = {"cost_usd": 0.0, "entries": 0}
            report.model_breakdown[model]["cost_usd"] += cost_usd
            report.model_breakdown[model]["entries"] += row["entry_count"]

        return sorted(reports.values(), key=lambda r: r.total_cost_usd, reverse=True)

    def generate_report_csv(self, client_id: str | None = None,
                            project_id: str | None = None) -> str:
        """Generate billing report as CSV string."""
        reports = self.generate_report(client_id=client_id, project_id=project_id)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "client_id", "project_id", "agent", "model",
            "cost_usd", "entries", "period_start", "period_end"
        ])

        for report in reports:
            for agent, adata in report.agent_breakdown.items():
                # Find primary model for this agent
                primary_model = max(
                    report.model_breakdown.items(),
                    key=lambda x: x[1]["cost_usd"],
                )[0] if report.model_breakdown else "unknown"

                writer.writerow([
                    report.client_id, report.project_id, agent, primary_model,
                    f"{adata['cost_usd']:.6f}", adata["entries"],
                    report.period_start, report.period_end,
                ])

        return output.getvalue()

    def generate_report_json(self, client_id: str | None = None,
                             project_id: str | None = None) -> str:
        """Generate billing report as JSON string."""
        reports = self.generate_report(client_id=client_id, project_id=project_id)

        result = []
        for report in reports:
            result.append({
                "client_id": report.client_id,
                "project_id": report.project_id,
                "total_cost_usd": round(report.total_cost_usd, 6),
                "agent_breakdown": {
                    k: {"cost_usd": round(v["cost_usd"], 6), "entries": v["entries"]}
                    for k, v in report.agent_breakdown.items()
                },
                "model_breakdown": {
                    k: {"cost_usd": round(v["cost_usd"], 6), "entries": v["entries"]}
                    for k, v in report.model_breakdown.items()
                },
                "period_start": report.period_start,
                "period_end": report.period_end,
            })

        return json.dumps(result, indent=2)

    # ── Cost Projection ────────────────────────────────────

    def project_costs(
        self,
        client_id: str | None = None,
        project_id: str | None = None,
        lookback_days: int = 7,
    ) -> CostProjection:
        """Project monthly cost based on rolling 7-day average.

        Args:
            client_id: Filter by client (None = all)
            project_id: Filter by project (None = all)
            lookback_days: Window for rolling average (default 7)

        Returns:
            CostProjection with daily costs, projected monthly, and trend.
        """
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)

        # Query daily cost totals
        query = """
            SELECT
                DATE(recorded_at) as day,
                SUM(credits_spent) as daily_credits
            FROM telemetry_credit_ledger
            WHERE recorded_at >= ? AND recorded_at <= ?
        """
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]

        if client_id:
            query += " AND client_id = ?"
            params.append(client_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        query += " GROUP BY DATE(recorded_at) ORDER BY day"

        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        # Fill in all days in the window (even zero-cost days)
        daily_costs: list[float] = []
        day_map: dict[str, float] = {
            row["day"]: (row["daily_credits"] or 0) / 100000.0
            for row in rows
        }

        current = start_date
        while current <= end_date:
            day_str = current.strftime("%Y-%m-%d")
            daily_costs.append(day_map.get(day_str, 0.0))
            current += timedelta(days=1)

        # Calculate projection
        if not daily_costs:
            return CostProjection(
                daily_costs=[],
                projected_monthly=0.0,
                average_daily=0.0,
                trend="stable",
                confidence="low",
            )

        # Average daily cost (only count days with data)
        non_zero = [c for c in daily_costs if c > 0]
        if len(non_zero) > 0:
            average_daily = sum(daily_costs) / len(daily_costs)
        else:
            average_daily = 0.0

        projected_monthly = average_daily * 30

        # Trend detection: compare first half vs second half
        mid = len(daily_costs) // 2
        if mid > 0:
            first_half_avg = sum(daily_costs[:mid]) / mid
            second_half_avg = sum(daily_costs[mid:]) / (len(daily_costs) - mid)

            if second_half_avg > first_half_avg * 1.2:
                trend = "rising"
            elif second_half_avg < first_half_avg * 0.8:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Confidence based on days of data
        days_with_data = len(non_zero)
        if days_with_data >= 7:
            confidence = "high"
        elif days_with_data >= 3:
            confidence = "medium"
        else:
            confidence = "low"

        return CostProjection(
            daily_costs=daily_costs,
            projected_monthly=round(projected_monthly, 4),
            average_daily=round(average_daily, 4),
            trend=trend,
            confidence=confidence,
        )

    # ── Telemetry Dimension Helpers ────────────────────────

    def enrich_credit_event(
        self, event: dict[str, Any]
    ) -> dict[str, Any]:
        """Enrich a credit event dict with client_id/project_id.

        If the event has an issue_id, resolves attribution automatically.
        Otherwise leaves client_id/project_id as-is or defaults.
        """
        if "client_id" not in event or event["client_id"] is None:
            issue_id = event.get("issue_id", "")
            if issue_id:
                client_id, project_id = self.get_attribution(issue_id)
                event["client_id"] = client_id
                event["project_id"] = project_id
            else:
                event.setdefault("client_id", "unknown-client")
                event.setdefault("project_id", "unassigned-project")
        return event
