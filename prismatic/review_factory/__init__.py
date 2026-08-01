"""Prismatic Engine Review/Merge Factory V1.

Event-driven review and merge pipeline for the Prismatic Engine.
Replaces serial George-protocol hand-review with a parallel,
queue-based, policy-driven factory.

Architecture
------------
- ``queue``   — RF-1: review_jobs + leases + state machine
- ``verifier``— RF-2: deterministic verification worker
- ``reviewer``— RF-3: reviewer capability (wraps pr_reviewer)
- ``merge``   — RF-4: merge executor + standing policy
- ``policy``  — risk tier classifier + YAML policy loader
- ``models``  — canonical record schemas (5 tables)
- ``db``      — SQLite helpers + migrations

Canonical OKF: okf-review-factory-v1.md
Base commit:   21be7812 (post-PR-382 merge)
"""

from prismatic.review_factory.models import (
    MergeAuthorization,
    MergeScope,
    RepairPacket,
    ReviewDecision,
    ReviewJob,
    ReviewJobState,
    ReviewVerdict,
    RiskTier,
    VerificationClassification,
    VerificationReceipt,
)

__all__ = [
    "ReviewJob",
    "ReviewJobState",
    "RiskTier",
    "VerificationReceipt",
    "VerificationClassification",
    "ReviewDecision",
    "ReviewVerdict",
    "MergeAuthorization",
    "MergeScope",
    "RepairPacket",
]
