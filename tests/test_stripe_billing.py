"""tests/test_stripe_billing.py — Integration tests for Stripe billing pipeline.

Covers:
- SqliteCreditLedger ACID semantics (concurrent deductions, insufficient funds)
- StripeWebhookHandler event processing (invoice.paid, subscription.deleted)
- UsageReporter meter event flow (real-time report, batch poll)
- End-to-end: mock invoice.paid -> credit added -> verify balance
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure the project root is on sys.path
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from prismatic.billing.credit_ledger import (
    SqliteCreditLedger,
    CreditError,
    TenantState,
)
from prismatic.billing.stripe_webhooks import (
    StripeWebhookHandler,
    StripeWebhookError,
)
from prismatic.billing.usage_reporter import UsageReporter, UsageReporterError


# ═══════════════════════════════════════════════════════════════
# Credit Ledger Tests
# ═══════════════════════════════════════════════════════════════


class TestSqliteCreditLedger(unittest.TestCase):
    """Test ACID semantics, balance operations, tenant lifecycle."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_credit.db")
        self.ledger = SqliteCreditLedger(db_path=self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ensure_tenant_creates_with_zero_balance(self):
        self.ledger.ensure_tenant("cus_test123")
        self.assertEqual(self.ledger.get_balance("cus_test123"), 0)
        self.assertEqual(
            self.ledger.get_state("cus_test123"), TenantState.ACTIVE
        )

    def test_ensure_tenant_idempotent(self):
        self.ledger.ensure_tenant("cus_dup")
        self.ledger.ensure_tenant("cus_dup")  # should not raise
        self.ledger.add_credits("cus_dup", 5000)
        self.ledger.ensure_tenant("cus_dup")  # should not reset balance
        self.assertEqual(self.ledger.get_balance("cus_dup"), 5000)

    def test_add_credits(self):
        self.ledger.ensure_tenant("cus_test")
        new_balance = self.ledger.add_credits(
            "cus_test", 10000, reason="test:add"
        )
        self.assertEqual(new_balance, 10000)
        self.assertEqual(self.ledger.get_balance("cus_test"), 10000)

    def test_deduct_credits_success(self):
        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 10000)
        new_balance = self.ledger.deduct_credits(
            "cus_test", 3000, reason="test:deduct"
        )
        self.assertEqual(new_balance, 7000)
        self.assertEqual(self.ledger.get_balance("cus_test"), 7000)

    def test_deduct_insufficient_funds_raises(self):
        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 1000)
        with self.assertRaises(CreditError):
            self.ledger.deduct_credits("cus_test", 2000)
        # Balance unchanged after failed deduction
        self.assertEqual(self.ledger.get_balance("cus_test"), 1000)

    def test_deduct_frozen_tenant_raises(self):
        self.ledger.ensure_tenant("cus_frozen")
        self.ledger.add_credits("cus_frozen", 5000)
        self.ledger.set_state("cus_frozen", TenantState.FROZEN)
        with self.assertRaises(CreditError):
            self.ledger.deduct_credits("cus_frozen", 100)
        # Balance unchanged
        self.assertEqual(self.ledger.get_balance("cus_frozen"), 5000)

    def test_deduct_nonexistent_tenant_raises(self):
        with self.assertRaises(CreditError):
            self.ledger.deduct_credits("cus_ghost", 100)

    def test_has_sufficient_credits(self):
        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 5000)
        self.assertTrue(self.ledger.has_sufficient_credits("cus_test", 3000))
        self.assertFalse(self.ledger.has_sufficient_credits("cus_test", 6000))
        self.assertFalse(self.ledger.has_sufficient_credits("cus_unknown", 100))

    def test_has_sufficient_credits_frozen(self):
        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 5000)
        self.ledger.set_state("cus_test", TenantState.FROZEN)
        self.assertFalse(self.ledger.has_sufficient_credits("cus_test", 100))

    def test_set_state_round_trip(self):
        self.ledger.ensure_tenant("cus_test")
        self.ledger.set_state("cus_test", TenantState.FROZEN)
        self.assertEqual(
            self.ledger.get_state("cus_test"), TenantState.FROZEN
        )
        self.ledger.set_state("cus_test", TenantState.ACTIVE)
        self.assertEqual(
            self.ledger.get_state("cus_test"), TenantState.ACTIVE
        )
        self.ledger.set_state("cus_test", TenantState.SUSPENDED)
        self.assertEqual(
            self.ledger.get_state("cus_test"), TenantState.SUSPENDED
        )

    def test_transaction_log(self):
        self.ledger.ensure_tenant("cus_log")
        self.ledger.add_credits("cus_log", 10000, reason="initial")
        self.ledger.deduct_credits("cus_log", 2000, reason="job:run1")
        self.ledger.deduct_credits("cus_log", 3000, reason="job:run2")

        log = self.ledger.get_transaction_log("cus_log")
        self.assertEqual(len(log), 3)

        # Most recent first
        self.assertEqual(log[0]["delta"], -3000)
        self.assertEqual(log[0]["balance_after"], 5000)
        self.assertEqual(log[1]["delta"], -2000)
        self.assertEqual(log[1]["balance_after"], 8000)
        self.assertEqual(log[2]["delta"], 10000)
        self.assertEqual(log[2]["balance_after"], 10000)

    # ── ACID: Concurrent Deductions ───────────────────────

    def test_concurrent_deductions_no_double_spend(self):
        """Verify that 10 concurrent deductions of 1000 from a 5000 balance
        result in exactly 5000 deducted and 5 of 10 succeeding."""
        self.ledger.ensure_tenant("cus_concurrent")
        self.ledger.add_credits("cus_concurrent", 5000)

        errors = []
        results = []
        lock = threading.Lock()

        def deduct_thread(worker_id: int):
            try:
                bal = self.ledger.deduct_credits(
                    "cus_concurrent", 1000,
                    reason=f"concurrent:worker-{worker_id}",
                )
                with lock:
                    results.append(bal)
            except CreditError as e:
                with lock:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=deduct_thread, args=(i,))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Exactly 5 should succeed (5000 / 1000 = 5 deductions)
        self.assertEqual(len(results), 5, f"Expected 5 success, got {len(results)}")
        self.assertEqual(len(errors), 5, f"Expected 5 failures, got {len(errors)}")

        # Final balance should be 0
        final_balance = self.ledger.get_balance("cus_concurrent")
        self.assertEqual(final_balance, 0)

    def test_negative_amounts_raise(self):
        self.ledger.ensure_tenant("cus_test")
        with self.assertRaises(ValueError):
            self.ledger.add_credits("cus_test", -100)
        with self.assertRaises(ValueError):
            self.ledger.deduct_credits("cus_test", -100)
        with self.assertRaises(ValueError):
            self.ledger.deduct_credits("cus_test", 0)

    def test_add_then_deduct_then_add(self):
        """Full lifecycle: add 5000, deduct 2000, add 3000 = 6000"""
        self.ledger.ensure_tenant("cus_lifecycle")
        self.assertEqual(self.ledger.add_credits("cus_lifecycle", 5000), 5000)
        self.assertEqual(self.ledger.deduct_credits("cus_lifecycle", 2000), 3000)
        self.assertEqual(self.ledger.add_credits("cus_lifecycle", 3000), 6000)

    def test_credit_exhaustion(self):
        """Drain account to zero and verify next deduction fails."""
        self.ledger.ensure_tenant("cus_drain")
        self.ledger.add_credits("cus_drain", 3000)
        self.ledger.deduct_credits("cus_drain", 3000)
        self.assertEqual(self.ledger.get_balance("cus_drain"), 0)
        with self.assertRaises(CreditError):
            self.ledger.deduct_credits("cus_drain", 1)


# ═══════════════════════════════════════════════════════════════
# Stripe Webhook Tests
# ═══════════════════════════════════════════════════════════════

# Sample Stripe event payloads (simplified)
SAMPLE_INVOICE_PAID = {
    "id": "evt_test_invoice_paid",
    "type": "invoice.paid",
    "data": {
        "object": {
            "id": "in_test_123",
            "customer": "cus_test_customer",
            "amount_paid": 2000,  # $20.00 in cents
            "currency": "usd",
            "paid": True,
            "status": "paid",
        }
    },
}

SAMPLE_SUBSCRIPTION_DELETED = {
    "id": "evt_test_sub_deleted",
    "type": "customer.subscription.deleted",
    "data": {
        "object": {
            "id": "sub_test_456",
            "customer": "cus_test_customer",
            "status": "canceled",
        }
    },
}

SAMPLE_PAYMENT_FAILED = {
    "id": "evt_test_payment_failed",
    "type": "invoice.payment_failed",
    "data": {
        "object": {
            "id": "in_test_fail",
            "customer": "cus_test_customer",
            "amount_due": 2000,
            "attempt_count": 2,
            "status": "open",
        }
    },
}

SAMPLE_UNKNOWN_EVENT = {
    "id": "evt_test_unknown",
    "type": "charge.succeeded",
    "data": {"object": {"id": "ch_test_789"}},
}


class TestStripeWebhookHandler(unittest.TestCase):
    """Test webhook event routing and credit ledger integration."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_webhook.db")
        self.ledger = SqliteCreditLedger(db_path=self.db_path)
        self.handler = StripeWebhookHandler(
            credit_ledger=self.ledger,
            webhook_secret="whsec_test_secret",
            stripe_api_key="sk_test_dummy",
        )
        # Monkey-patch _verify_signature to bypass actual signature check
        self.handler._verify_signature = lambda payload, sig: json.loads(payload)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_invoice_paid_adds_credits(self):
        payload = json.dumps(SAMPLE_INVOICE_PAID).encode()
        result = self.handler.handle_webhook(payload, "fake_sig")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["event_type"], "invoice.paid")

        # $20.00 = 20 * 10000 = 200,000 credits
        self.assertEqual(result["result"]["credits_added"], 200000)
        self.assertEqual(result["result"]["new_balance"], 200000)

        # Verify via ledger directly
        balance = self.ledger.get_balance("cus_test_customer")
        self.assertEqual(balance, 200000)

    def test_subscription_deleted_freezes_tenant(self):
        # First give them some credits
        self.ledger.ensure_tenant("cus_test_customer")
        self.ledger.add_credits("cus_test_customer", 50000)

        payload = json.dumps(SAMPLE_SUBSCRIPTION_DELETED).encode()
        result = self.handler.handle_webhook(payload, "fake_sig")

        self.assertEqual(result["event_type"], "customer.subscription.deleted")
        self.assertEqual(result["result"]["state"], TenantState.FROZEN)

        # Verify ledger state
        self.assertEqual(
            self.ledger.get_state("cus_test_customer"), TenantState.FROZEN
        )

        # Verify deductions are blocked
        with self.assertRaises(CreditError):
            self.ledger.deduct_credits("cus_test_customer", 1000)

    def test_payment_failure_logged_no_state_change(self):
        self.ledger.ensure_tenant("cus_test_customer")
        self.ledger.add_credits("cus_test_customer", 50000)

        payload = json.dumps(SAMPLE_PAYMENT_FAILED).encode()
        result = self.handler.handle_webhook(payload, "fake_sig")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["event_type"], "invoice.payment_failed")

        # Tenant should still be active
        self.assertEqual(
            self.ledger.get_state("cus_test_customer"), TenantState.ACTIVE
        )

    def test_unknown_event_acknowledged(self):
        payload = json.dumps(SAMPLE_UNKNOWN_EVENT).encode()
        result = self.handler.handle_webhook(payload, "fake_sig")
        self.assertEqual(result["status"], "acknowledged")
        self.assertEqual(result["event_type"], "charge.succeeded")

    def test_invoice_paid_reactivates_frozen_tenant(self):
        # Start frozen
        self.ledger.ensure_tenant("cus_frozen")
        self.ledger.set_state("cus_frozen", TenantState.FROZEN)

        # Payment comes in
        invoice_data = dict(SAMPLE_INVOICE_PAID)
        invoice_data["data"]["object"]["customer"] = "cus_frozen"
        payload = json.dumps(invoice_data).encode()
        result = self.handler.handle_webhook(payload, "fake_sig")

        self.assertEqual(result["status"], "processed")
        # Tenant should be active again
        self.assertEqual(
            self.ledger.get_state("cus_frozen"), TenantState.ACTIVE
        )

    def test_missing_customer_raises_error(self):
        bad_invoice = dict(SAMPLE_INVOICE_PAID)
        bad_invoice["data"]["object"]["customer"] = None
        payload = json.dumps(bad_invoice).encode()
        with self.assertRaises(StripeWebhookError):
            self.handler.handle_webhook(payload, "fake_sig")


# ═══════════════════════════════════════════════════════════════
# Usage Reporter Tests
# ═══════════════════════════════════════════════════════════════


class TestUsageReporter(unittest.TestCase):
    """Test Stripe Meter event submission and batch polling."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_usage.db")
        # Isolate checkpoint file per test
        self._old_state_dir = os.environ.get("PRISMATIC_STATE_DIR")
        os.environ["PRISMATIC_STATE_DIR"] = self.tmp_dir
        self.ledger = SqliteCreditLedger(db_path=self.db_path)
        self.reporter = UsageReporter(
            stripe_api_key="***",
            meter_name="test_meter",
            ledger=self.ledger,
        )

    def tearDown(self):
        import shutil
        if self._old_state_dir:
            os.environ["PRISMATIC_STATE_DIR"] = self._old_state_dir
        else:
            os.environ.pop("PRISMATIC_STATE_DIR", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("requests.post")
    def test_report_job_sends_meter_event(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "meter_event_test", "object": "billing.meter_event"},
        )
        mock_post.return_value.raise_for_status = lambda: None

        result = self.reporter.report_job(
            customer_id="cus_test",
            credits_consumed=5000,
            job_id="job-001",
            model="deepseek-v4",
            agent="ned",
        )

        self.assertIn("id", result)
        self.assertEqual(result["id"], "meter_event_test")

        # Verify the request payload
        call_args = mock_post.call_args
        self.assertIsNotNone(call_args)
        url = call_args[0][0]
        self.assertIn("billing/meter_events", url)

    @patch("requests.post")
    def test_report_job_missing_api_key_raises(self, mock_post):
        bad_reporter = UsageReporter(
            stripe_api_key="",
            meter_name="test_meter",
        )
        with self.assertRaises(UsageReporterError):
            bad_reporter.report_job("cus_test", 1000)

    @patch("requests.post")
    def test_meter_event_api_error_raises(self, mock_post):
        import requests
        mock_response = MagicMock(status_code=402, url="https://api.stripe.com/v1/billing/meter_events")
        mock_response.json.return_value = {"error": {"message": "Insufficient balance"}}
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "402 Client Error", response=mock_response
        )
        mock_post.return_value = mock_response

        with self.assertRaises(UsageReporterError):
            self.reporter.report_job("cus_test", 5000, job_id="job-fail")

    def test_poll_no_unbilled(self):
        """Poll with empty ledger returns zero sent."""
        result = self.reporter.poll(lookback_hours=24)
        self.assertEqual(result["sent"], 0)

    def test_poll_with_unbilled_but_no_stripe_key(self):
        """Poll with data but no stripe key returns error."""
        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 50000, reason="initial")
        self.ledger.deduct_credits("cus_test", 10000, reason="job:test")

        # Reporter with no API key
        bad_reporter = UsageReporter(
            stripe_api_key="",
            meter_name="test_meter",
            ledger=self.ledger,
        )
        result = bad_reporter.poll(lookback_hours=24)
        # The poll finds unbilled but gets an error when trying to send
        # (no stripe key). We just check the structure.
        self.assertIn("sent", result)
        self.assertIn("unbilled_found", result)

    @patch("requests.post")
    def test_poll_with_billed_and_unbilled(self, mock_post):
        """Verify poll only sends unbilled transactions."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "me_test", "object": "billing.meter_event"},
        )
        mock_post.return_value.raise_for_status = lambda: None

        self.ledger.ensure_tenant("cus_test")
        self.ledger.add_credits("cus_test", 50000, reason="initial")
        self.ledger.deduct_credits("cus_test", 10000, reason="job:first")
        self.ledger.deduct_credits("cus_test", 5000, reason="job:second")

        # First poll should send 2 events
        result = self.reporter.poll(lookback_hours=24)
        self.assertEqual(result["sent"], 2)

        # Second poll should send 0 (all already reported)
        result2 = self.reporter.poll(lookback_hours=24)
        self.assertEqual(result2["sent"], 0)


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
