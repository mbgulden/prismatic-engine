"""
tests/test_cost_attribution.py — Tests for Phase 4.4 Cost Attribution Engine.

Covers:
- billing_mapping CRUD
- Model pricing calculation
- Usage recording with client/project attribution
- Billing report generation (JSON, CSV)
- Cost projection (7-day rolling average)
- Telemetry client_id/project_id migration
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path


class TestCostAttributionEngine(unittest.TestCase):
    """Core billing engine tests."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_billing.db")
        # Pre-create tables
        from prismatic.billing.cost_attribution import CostAttributionEngine
        self.engine = CostAttributionEngine(db_path=self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ── Attribution Mapping ──────────────────────────────

    def test_set_and_get_attribution(self):
        self.engine.set_attribution("GRO-1234", "acme-corp", "website-v2")
        client, project = self.engine.get_attribution("GRO-1234")
        self.assertEqual(client, "acme-corp")
        self.assertEqual(project, "website-v2")

    def test_get_attribution_unknown_issue(self):
        client, project = self.engine.get_attribution("GRO-9999")
        self.assertEqual(client, "unknown-client")
        self.assertEqual(project, "unassigned-project")

    def test_set_attribution_overwrites(self):
        self.engine.set_attribution("GRO-1234", "acme-corp", "website-v2")
        self.engine.set_attribution("GRO-1234", "beta-inc", "mobile-app")
        client, project = self.engine.get_attribution("GRO-1234")
        self.assertEqual(client, "beta-inc")
        self.assertEqual(project, "mobile-app")

    def test_get_all_attributions(self):
        self.engine.set_attribution("GRO-1", "c1", "p1")
        self.engine.set_attribution("GRO-2", "c2", "p2")
        all_attr = self.engine.get_all_attributions()
        self.assertEqual(len(all_attr), 2)

    # ── Cost Calculation ────────────────────────────────

    def test_calculate_cost_known_model(self):
        cost = self.engine.calculate_cost("gpt-4", 1000, 500)
        # prompt: 1000 * 0.00003 = 0.03, completion: 500 * 0.00006 = 0.03
        self.assertAlmostEqual(cost, 0.06, places=6)

    def test_calculate_cost_claude_opus(self):
        cost = self.engine.calculate_cost("claude-3-opus", 1000, 1000)
        # prompt: 0.015, completion: 0.075
        self.assertAlmostEqual(cost, 0.09, places=6)

    def test_calculate_cost_gemini_flash(self):
        cost = self.engine.calculate_cost("gemini-1.5-flash", 100000, 50000)
        # prompt: 100k * 0.00000015 = 0.015, completion: 50k * 0.0000006 = 0.03
        self.assertAlmostEqual(cost, 0.045, places=6)

    def test_calculate_cost_unknown_model_fallback(self):
        cost = self.engine.calculate_cost("unknown-model-xyz", 1000, 1000)
        self.assertAlmostEqual(cost, 0.004, places=6)

    def test_calculate_cost_prefix_match(self):
        """gpt-4-0613 should match gpt-4 pricing."""
        cost = self.engine.calculate_cost("gpt-4-0613", 1000, 500)
        self.assertAlmostEqual(cost, 0.06, places=6)

    def test_calculate_cost_deepseek(self):
        cost = self.engine.calculate_cost("deepseek-v3", 1000000, 500000)
        # prompt: 1M * 0.00000027 = 0.27, completion: 500k * 0.0000011 = 0.55
        self.assertAlmostEqual(cost, 0.82, places=6)

    def test_calculate_cost_zero_tokens(self):
        cost = self.engine.calculate_cost("gpt-4", 0, 0)
        self.assertEqual(cost, 0.0)

    # ── Usage Recording ─────────────────────────────────

    def test_record_usage_with_attribution(self):
        self.engine.set_attribution("GRO-100", "client-a", "proj-x")
        cost = self.engine.record_usage(
            agent_id="agent:ned",
            issue_id="GRO-100",
            model="gpt-4",
            prompt_tokens=2000,
            completion_tokens=1000,
            provider="openai",
            run_id="test-run-001",
        )
        self.assertAlmostEqual(cost, 0.12, places=6)

        # Verify it's in the ledger
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT client_id, project_id, credits_spent FROM telemetry_credit_ledger WHERE run_id=?",
            ("test-run-001",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "client-a")
        self.assertEqual(row[1], "proj-x")
        # credits_spent stored as micro-dollars: 0.12 * 100000 = 12000
        self.assertEqual(row[2], 12000)

    def test_record_usage_unknown_issue(self):
        cost = self.engine.record_usage(
            agent_id="agent:ned",
            issue_id="GRO-9999",
            model="gpt-3.5-turbo",
            prompt_tokens=10000,
            completion_tokens=5000,
        )
        self.assertGreater(cost, 0)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT client_id, project_id FROM telemetry_credit_ledger WHERE agent=?",
            ("agent:ned",)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "unknown-client")
        self.assertEqual(row[1], "unassigned-project")

    def test_record_token_spend_direct(self):
        cost = self.engine.record_token_spend(
            client_id="direct-client",
            project_id="direct-project",
            agent_id="agent:jules",
            model="claude-3-sonnet",
            prompt_tokens=5000,
            completion_tokens=2000,
        )
        self.assertGreater(cost, 0)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT client_id, project_id FROM telemetry_credit_ledger WHERE agent=?",
            ("agent:jules",)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "direct-client")
        self.assertEqual(row[1], "direct-project")

    # ── Billing Reports ─────────────────────────────────

    def _seed_ledger_data(self):
        """Seed the credit ledger with test data across clients/projects."""
        now = datetime.now(timezone.utc)
        entries = [
            ("c1", "p1", "agent:agy", "gemini-1.5-pro", 50000, now),
            ("c1", "p1", "agent:agy", "gemini-1.5-pro", 30000, now - timedelta(hours=1)),
            ("c1", "p2", "agent:ned", "gpt-4", 20000, now - timedelta(hours=2)),
            ("c2", "p1", "agent:jules", "claude-3-opus", 100000, now - timedelta(days=1)),
            ("c2", "p1", "agent:jules", "claude-3-opus", 50000, now - timedelta(days=2)),
        ]
        conn = sqlite3.connect(self.db_path)
        for client_id, project_id, agent, model, credits, ts in entries:
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"run-{client_id}-{ts.timestamp()}", agent, "test",
                 model, credits, "test", ts.isoformat(), client_id, project_id),
            )
        conn.commit()
        conn.close()

    def test_generate_report_all(self):
        self._seed_ledger_data()
        reports = self.engine.generate_report()
        self.assertGreaterEqual(len(reports), 2)  # c1+p1, c1+p2, c2+p1

        # Find c1+p1
        c1p1 = [r for r in reports if r.client_id == "c1" and r.project_id == "p1"]
        self.assertEqual(len(c1p1), 1)
        # 50000 + 30000 = 80000 micro-dollars = $0.80
        self.assertAlmostEqual(c1p1[0].total_cost_usd, 0.80, places=4)

    def test_generate_report_filter_client(self):
        self._seed_ledger_data()
        reports = self.engine.generate_report(client_id="c2")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].client_id, "c2")
        # 100000 + 50000 = 150000 micro-dollars = $1.50
        self.assertAlmostEqual(reports[0].total_cost_usd, 1.50, places=4)

    def test_generate_report_csv(self):
        self._seed_ledger_data()
        csv_output = self.engine.generate_report_csv()
        self.assertIn("client_id", csv_output)
        self.assertIn("cost_usd", csv_output)

    def test_generate_report_json(self):
        self._seed_ledger_data()
        json_output = self.engine.generate_report_json()
        data = json.loads(json_output)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("client_id", data[0])
        self.assertIn("total_cost_usd", data[0])

    def test_generate_report_empty(self):
        reports = self.engine.generate_report()
        self.assertEqual(len(reports), 0)

    # ── Cost Projection ─────────────────────────────────

    def _seed_daily_data(self, days: int = 7):
        """Seed daily cost data for projection testing."""
        now = datetime.now(timezone.utc)
        base_credits = [10000, 12000, 9000, 15000, 11000, 13000, 14000]  # 7 days
        conn = sqlite3.connect(self.db_path)
        for i in range(min(days, len(base_credits))):
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"proj-run-{i}", "agent:agy", "test", "gemini-1.5-pro",
                 base_credits[i], "test",
                 (now - timedelta(days=days - 1 - i)).isoformat(),
                 "proj-client", "proj-test"),
            )
        conn.commit()
        conn.close()

    def test_project_costs_with_data(self):
        self._seed_daily_data(days=7)
        proj = self.engine.project_costs(
            client_id="proj-client", project_id="proj-test"
        )
        # Window = lookback_days days + today = 8 days
        self.assertGreaterEqual(len(proj.daily_costs), 7)
        self.assertGreater(proj.projected_monthly, 0)
        self.assertEqual(proj.confidence, "high")

    def test_project_costs_empty(self):
        proj = self.engine.project_costs()
        # Empty DB still returns full window worth of days (all $0)
        self.assertGreater(len(proj.daily_costs), 0)
        self.assertEqual(proj.projected_monthly, 0.0)
        self.assertEqual(proj.confidence, "low")

    def test_project_costs_rising_trend(self):
        """Rising trend: first half low, second half high."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.db_path)
        # Day 0-2: low cost (10000 each), Day 3-6: high cost (50000 each)
        costs = [10000, 10000, 10000, 50000, 50000, 50000, 50000]
        for i, credits in enumerate(costs):
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"trend-run-{i}", "agent:agy", "test", "gemini-1.5-pro",
                 credits, "test",
                 (now - timedelta(days=6 - i)).isoformat(),
                 "trend-client", "trend-proj"),
            )
        conn.commit()
        conn.close()

        proj = self.engine.project_costs(
            client_id="trend-client", project_id="trend-proj"
        )
        self.assertEqual(proj.trend, "rising")

    def test_project_costs_falling_trend(self):
        """Falling trend: first half high, second half low."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.db_path)
        costs = [50000, 50000, 50000, 10000, 10000, 10000, 10000]
        for i, credits in enumerate(costs):
            conn.execute(
                """INSERT INTO telemetry_credit_ledger
                   (run_id, agent, provider, model, credits_spent,
                    operation, recorded_at, client_id, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"fall-run-{i}", "agent:agy", "test", "gemini-1.5-pro",
                 credits, "test",
                 (now - timedelta(days=6 - i)).isoformat(),
                 "fall-client", "fall-proj"),
            )
        conn.commit()
        conn.close()

        proj = self.engine.project_costs(
            client_id="fall-client", project_id="fall-proj"
        )
        self.assertEqual(proj.trend, "falling")

    # ── Model Pricing Table ─────────────────────────────

    def test_model_pricing_exhaustive(self):
        """All entries in MODEL_PRICING have prompt and completion keys."""
        from prismatic.billing.cost_attribution import MODEL_PRICING
        for model, rates in MODEL_PRICING.items():
            with self.subTest(model=model):
                self.assertIn("prompt", rates, f"{model} missing prompt rate")
                self.assertIn("completion", rates, f"{model} missing completion rate")
                self.assertGreater(rates["prompt"], 0)
                self.assertGreater(rates["completion"], 0)

    # ── Enrichment ──────────────────────────────────────

    def test_enrich_credit_event(self):
        self.engine.set_attribution("GRO-500", "enrich-client", "enrich-proj")
        event = {"run_id": "r1", "issue_id": "GRO-500", "credits_spent": 100}
        enriched = self.engine.enrich_credit_event(event)
        self.assertEqual(enriched["client_id"], "enrich-client")
        self.assertEqual(enriched["project_id"], "enrich-proj")

    def test_enrich_credit_event_no_issue(self):
        event = {"run_id": "r1", "credits_spent": 100}
        enriched = self.engine.enrich_credit_event(event)
        self.assertEqual(enriched["client_id"], "unknown-client")
        self.assertEqual(enriched["project_id"], "unassigned-project")


class TestTelemetryClientIdMigration(unittest.TestCase):
    """Verify telemetry credit_ledger accepts client_id/project_id."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_migration.db")
        os.environ["PRISMATIC_STATE_DIR"] = self.tmp_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        os.environ.pop("PRISMATIC_STATE_DIR", None)

    def test_record_credit_with_client_id(self):
        from prismatic.telemetry import TelemetryCollector
        collector = TelemetryCollector(db_path=self.db_path)
        import time
        time.sleep(0.2)  # let drain thread start

        collector.record_credit(
            run_id="mig-test-001",
            agent="agent:ned",
            provider="openai",
            credits_spent=5000,
            model="gpt-4",
            operation="test_migration",
            client_id="mig-client",
            project_id="mig-proj",
        )
        time.sleep(0.2)  # let drain thread process

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT client_id, project_id FROM telemetry_credit_ledger WHERE run_id=?",
            ("mig-test-001",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "mig-client")
        self.assertEqual(row[1], "mig-proj")

    def test_record_credit_without_client_id(self):
        """Backward compatibility: no client_id/project_id → NULL."""
        from prismatic.telemetry import TelemetryCollector
        collector = TelemetryCollector(db_path=self.db_path)
        import time
        time.sleep(0.2)

        collector.record_credit(
            run_id="mig-test-002",
            agent="agent:fred",
            provider="openai",
            credits_spent=3000,
        )
        time.sleep(0.2)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT client_id, project_id FROM telemetry_credit_ledger WHERE run_id=?",
            ("mig-test-002",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row[0])  # client_id NULL when not provided
        self.assertIsNone(row[1])  # project_id NULL when not provided


if __name__ == "__main__":
    unittest.main()
