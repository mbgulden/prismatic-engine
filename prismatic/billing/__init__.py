"""prismatic/billing - Client Cost Attribution & Billing Engine (Phase 4.4)

Provides:
- Cost attribution (issue -> client/project mapping)
- Token-based billing reports (CSV + JSON)
- Cost projection (rolling 7-day model)
- ACID credit ledger (SQLite dev + PostgreSQL prod)
- Stripe webhook integration (invoice.paid, subscription events)
- Stripe Billing Meter usage reporting

Sub-modules:
    cost_attribution.py   -- CostAttributionEngine, billing reports
    credit_ledger.py      -- SqliteCreditLedger, PostgresCreditLedger
    stripe_webhooks.py    -- StripeWebhookHandler, register_stripe_routes
    usage_reporter.py     -- UsageReporter (Stripe Billing Meter sync)
"""

from .cost_attribution import (
    MODEL_PRICING,
    BillingReport,
    CostAttributionEngine,
    CostProjection,
)
from .credit_ledger import (
    CreditError,
    CreditLedger,
    PostgresCreditLedger,
    SqliteCreditLedger,
    TenantState,
)
from .stripe_webhooks import (
    StripeWebhookError,
    StripeWebhookHandler,
    register_stripe_routes,
)
from .usage_reporter import UsageReporter, UsageReporterError

__all__ = [
    # Cost Attribution
    "CostAttributionEngine",
    "MODEL_PRICING",
    "BillingReport",
    "CostProjection",
    # Credit Ledger
    "CreditLedger",
    "SqliteCreditLedger",
    "PostgresCreditLedger",
    "CreditError",
    "TenantState",
    # Stripe Webhooks
    "StripeWebhookHandler",
    "StripeWebhookError",
    "register_stripe_routes",
    # Usage Reporting
    "UsageReporter",
    "UsageReporterError",
]
