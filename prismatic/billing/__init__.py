"""
prismatic/billing — Client Cost Attribution Engine (Phase 4.4)

Layers cost attribution on top of the existing telemetry_credit_ledger table:
- Maps issues to client/project billing profiles
- Tracks token consumption per client/project/agent
- Generates monthly billing reports (CSV + JSON)
- Provides a rolling 7-day cost projection model
"""

from .cost_attribution import (
    CostAttributionEngine,
    MODEL_PRICING,
    BillingReport,
    CostProjection,
)

__all__ = [
    "CostAttributionEngine",
    "MODEL_PRICING",
    "BillingReport",
    "CostProjection",
]
