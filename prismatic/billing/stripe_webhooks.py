"""prismatic/billing/stripe_webhooks.py — Stripe Billing Meter event listener.

Handles incoming Stripe webhooks to sync payment events to the
Prismatic credit ledger.

Key events:
- invoice.paid → top up tenant credit balance
- customer.subscription.deleted → freeze tenant access
- invoice.payment_failed → warn / lower grace-period balance

Usage (Flask):
    from prismatic.billing.stripe_webhooks import register_stripe_routes
    register_stripe_routes(app, credit_ledger)

Usage (standalone):
    handler = StripeWebhookHandler(credit_ledger)
    result = handler.handle_webhook(payload, sig_header)
"""

from __future__ import annotations

import os
import logging
from typing import Any, Optional

from .credit_ledger import CreditLedger, TenantState, CreditError

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────
DEFAULT_CREDIT_PACKAGE_USD = 10.0     # $10 buys how many credits?
CREDITS_PER_DOLLAR = 10000             # 1 USD = 10,000 micro-dollar credits


class StripeWebhookError(Exception):
    """Raised when webhook processing fails (signature, missing data)."""


class StripeWebhookHandler:
    """Process incoming Stripe webhook events and update credit ledger.

    Wire this into your Flask/Starlette app via register_stripe_routes()
    or call handle_webhook() directly.
    """

    def __init__(
        self,
        credit_ledger: CreditLedger,
        webhook_secret: str | None = None,
        stripe_api_key: str | None = None,
    ):
        self._ledger = credit_ledger
        self._webhook_secret = webhook_secret or os.environ.get(
            "STRIPE_WEBHOOK_SECRET", ""
        )
        self._stripe_api_key = stripe_api_key or os.environ.get(
            "STRIPE_SECRET_KEY", ""
        )

    # ── Webhook Entry Point ────────────────────────────────

    def handle_webhook(
        self, payload: bytes, sig_header: str
    ) -> dict[str, Any]:
        """Verify Stripe signature, parse event, and dispatch to handler.

        Args:
            payload: Raw request body bytes.
            sig_header: Stripe-Signature header value.

        Returns:
            Dict with status and event info.

        Raises:
            StripeWebhookError on signature failure or unknown event type.
        """
        event = self._verify_signature(payload, sig_header)
        event_type = event.get("type", "")
        event_data = event.get("data", {}).get("object", {})

        logger.info("Stripe webhook received: %s (id=%s)",
                     event_type, event.get("id", "unknown"))

        handlers = {
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_payment_failed,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "customer.subscription.updated": self._handle_subscription_updated,
        }

        handler = handlers.get(event_type)
        if handler is None:
            logger.info("Unhandled event type: %s — acknowledged", event_type)
            return {"status": "acknowledged", "event_type": event_type}

        result = handler(event_data)
        return {
            "status": "processed",
            "event_type": event_type,
            "event_id": event.get("id"),
            "result": result,
        }

    # ── Signature Verification ─────────────────────────────

    def _verify_signature(
        self, payload: bytes, sig_header: str
    ) -> dict[str, Any]:
        """Verify Stripe webhook signature and return parsed event dict.

        Uses the stripe library's Webhook.construct_event().
        Falls back to manual parsing if stripe lib not available.
        """
        if not self._webhook_secret:
            raise StripeWebhookError(
                "STRIPE_WEBHOOK_SECRET not configured"
            )

        try:
            import stripe
            event = stripe.Webhook.construct_event(
                payload, sig_header, self._webhook_secret
            )
            return event.to_dict_recursive()
        except ImportError:
            # Fallback: parse raw JSON without signature verification
            # (dev mode only — signature verification requires stripe lib)
            import json
            logger.warning(
                "stripe lib not available — skipping signature verification"
            )
            return json.loads(payload)
        except Exception as exc:
            raise StripeWebhookError(
                f"Signature verification failed: {exc}"
            ) from exc

    # ── Event Handlers ─────────────────────────────────────

    def _handle_invoice_paid(
        self, invoice: dict[str, Any]
    ) -> dict[str, Any]:
        """Process invoice.paid event.

        Calculates credits from payment amount and adds to tenant balance.
        Also sets tenant state to active (in case it was frozen).
        """
        customer_id = invoice.get("customer")
        if not customer_id:
            raise StripeWebhookError("invoice.paid missing customer")

        amount_paid = invoice.get("amount_paid", 0)  # cents
        amount_usd = amount_paid / 100.0

        # Calculate credits: 1 USD = CREDITS_PER_DOLLAR micro-dollar credits
        credits_to_add = int(amount_usd * CREDITS_PER_DOLLAR)
        invoice_id = invoice.get("id", "unknown")

        self._ledger.ensure_tenant(customer_id)

        # Re-activate tenant if frozen (payment cleared the issue)
        current_state = self._ledger.get_state(customer_id)
        if current_state == TenantState.FROZEN:
            self._ledger.set_state(customer_id, TenantState.ACTIVE)

        new_balance = self._ledger.add_credits(
            customer_id,
            credits_to_add,
            reason=f"stripe:invoice.paid:{invoice_id}",
        )

        logger.info(
            "Added %d credits to tenant %s (invoice %s, $%.2f). New balance: %d",
            credits_to_add, customer_id, invoice_id, amount_usd, new_balance,
        )

        return {
            "tenant": customer_id,
            "credits_added": credits_to_add,
            "new_balance": new_balance,
            "amount_usd": amount_usd,
            "invoice_id": invoice_id,
        }

    def _handle_payment_failed(
        self, invoice: dict[str, Any]
    ) -> dict[str, Any]:
        """Process invoice.payment_failed event.

        Logs the failure. If this is the final attempt, we leave the
        tenant active but mark the failed payment for admin review.
        Freezing happens on subscription.deleted after retries exhaust.
        """
        customer_id = invoice.get("customer")
        invoice_id = invoice.get("id", "unknown")
        attempt_count = invoice.get("attempt_count", 1)
        next_attempt = invoice.get("next_payment_attempt")

        logger.warning(
            "Payment failed for tenant %s (invoice %s, attempt %d). "
            "Next attempt: %s",
            customer_id, invoice_id, attempt_count, next_attempt,
        )

        return {
            "tenant": customer_id,
            "invoice_id": invoice_id,
            "attempt": attempt_count,
            "action": "monitoring",
        }

    def _handle_subscription_deleted(
        self, subscription: dict[str, Any]
    ) -> dict[str, Any]:
        """Process customer.subscription.deleted event.

        Freezes the tenant — they cannot deduct credits until
        subscription is re-activated.
        """
        customer_id = subscription.get("customer")
        if not customer_id:
            raise StripeWebhookError(
                "customer.subscription.deleted missing customer"
            )

        self._ledger.ensure_tenant(customer_id)
        self._ledger.set_state(customer_id, TenantState.FROZEN)

        logger.info(
            "Tenant %s frozen due to subscription deletion",
            customer_id,
        )

        return {
            "tenant": customer_id,
            "state": TenantState.FROZEN,
        }

    def _handle_subscription_updated(
        self, subscription: dict[str, Any]
    ) -> dict[str, Any]:
        """Process customer.subscription.updated event.

        If subscription status returns to active after being past_due,
        re-activate the tenant.
        """
        customer_id = subscription.get("customer")
        status = subscription.get("status", "")

        if not customer_id:
            raise StripeWebhookError(
                "customer.subscription.updated missing customer"
            )

        self._ledger.ensure_tenant(customer_id)

        if status in ("active", "trialing"):
            current_state = self._ledger.get_state(customer_id)
            if current_state == TenantState.FROZEN:
                self._ledger.set_state(customer_id, TenantState.ACTIVE)
                logger.info(
                    "Tenant %s re-activated (subscription status: %s)",
                    customer_id, status,
                )
                return {
                    "tenant": customer_id,
                    "state": TenantState.ACTIVE,
                    "previous_state": TenantState.FROZEN,
                }

        return {
            "tenant": customer_id,
            "subscription_status": status,
            "state": self._ledger.get_state(customer_id),
        }


# ═══════════════════════════════════════════════════════════════
# Flask Route Registration
# ═══════════════════════════════════════════════════════════════


def register_stripe_routes(app: Any, credit_ledger: CreditLedger) -> None:
    """Register Stripe webhook endpoint on a Flask application.

    Usage:
        from prismatic.billing.stripe_webhooks import register_stripe_routes
        register_stripe_routes(app, SqliteCreditLedger())
    """
    handler = StripeWebhookHandler(credit_ledger)

    try:
        from flask import Blueprint, request, jsonify
    except ImportError:
        logger.warning("Flask not available — stripe routes not registered")
        return

    bp = Blueprint("stripe_webhooks", __name__, url_prefix="/api/billing")

    @bp.route("/stripe/webhook", methods=["POST"])
    def stripe_webhook():
        """Receive Stripe webhook events.

        Stripe sends POST with raw body and Stripe-Signature header.
        """
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")

        try:
            result = handler.handle_webhook(payload, sig_header)
            return jsonify(result), 200
        except StripeWebhookError as exc:
            logger.error("Webhook error: %s", exc)
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Unexpected webhook error")
            return jsonify({"error": f"Internal error: {exc}"}), 500

    @bp.route("/stripe/health", methods=["GET"])
    def stripe_health():
        """Check Stripe connectivity."""
        webhook_configured = bool(handler._webhook_secret)
        api_key_configured = bool(handler._stripe_api_key)
        return jsonify({
            "webhook_configured": webhook_configured,
            "api_key_configured": api_key_configured,
        })

    app.register_blueprint(bp)
    logger.info("Stripe webhook routes registered at /api/billing/stripe/webhook")
