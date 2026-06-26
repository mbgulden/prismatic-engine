"""tests/test_dispatcher_priority_queue.py — Dispatcher integration with PriorityQueue.

GRO-550: Validates that the dispatcher's helpers (priority mapping,
tier inference, effort estimation, build_priority_queue) work correctly
against realistic Linear issue dicts.
"""

from __future__ import annotations

import pytest

from prismatic.core.priority_queue import (
    ClientTier,
    JobItem,
    Priority,
    PriorityQueue,
)
from prismatic.dispatcher import (
    USE_PRIORITY_QUEUE,
    _effort_from_estimate,
    _linear_priority_to_band,
    _tier_from_labels,
    build_priority_queue,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


def make_issue(
    identifier: str,
    *,
    priority: int | None = 3,
    estimate: int | None = 5,
    labels: list[str] | None = None,
) -> dict:
    """Build a Linear-shaped issue dict for tests."""

    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Test issue {identifier}",
        "priority": priority,
        "estimate": estimate,
        "labels": [{"name": n} for n in (labels or [])],
    }


# ═══════════════════════════════════════════════════════════════════
# Linear → Priority band mapping
# ═══════════════════════════════════════════════════════════════════


class TestLinearPriorityMapping:
    def test_urgent(self) -> None:
        assert _linear_priority_to_band(1) == Priority.URGENT

    def test_high(self) -> None:
        assert _linear_priority_to_band(2) == Priority.HIGH

    def test_medium_is_normal(self) -> None:
        assert _linear_priority_to_band(3) == Priority.NORMAL

    def test_low(self) -> None:
        assert _linear_priority_to_band(4) == Priority.LOW

    def test_no_priority_collapses_to_normal(self) -> None:
        assert _linear_priority_to_band(0) == Priority.NORMAL
        assert _linear_priority_to_band(None) == Priority.NORMAL

    def test_unknown_value_falls_back_to_normal(self) -> None:
        assert _linear_priority_to_band(99) == Priority.NORMAL

    def test_invalid_input_returns_normal(self) -> None:
        assert _linear_priority_to_band("not-a-number") == Priority.NORMAL


# ═══════════════════════════════════════════════════════════════════
# Label → ClientTier inference
# ═══════════════════════════════════════════════════════════════════


class TestTierInference:
    def test_flagship_label(self) -> None:
        assert _tier_from_labels(["agent:ned", "flagship"]) == ClientTier.FLAGSHIP

    def test_strategic_label(self) -> None:
        assert _tier_from_labels(["strategic-q3"]) == ClientTier.FLAGSHIP

    def test_enterprise_label(self) -> None:
        assert _tier_from_labels(["enterprise-tier"]) == ClientTier.ENTERPRISE

    def test_internal_label(self) -> None:
        assert _tier_from_labels(["internal-tooling"]) == ClientTier.INTERNAL

    def test_default_is_standard(self) -> None:
        assert _tier_from_labels(["agent:ned", "bug"]) == ClientTier.STANDARD

    def test_empty_labels_is_standard(self) -> None:
        assert _tier_from_labels([]) == ClientTier.STANDARD

    def test_case_insensitive(self) -> None:
        assert _tier_from_labels(["FLAGSHIP"]) == ClientTier.FLAGSHIP


# ═══════════════════════════════════════════════════════════════════
# Estimate → effort
# ═══════════════════════════════════════════════════════════════════


class TestEffortEstimation:
    def test_estimate_one(self) -> None:
        assert _effort_from_estimate(1) == 1

    def test_estimate_ten(self) -> None:
        assert _effort_from_estimate(10) == 10

    def test_estimate_above_ten_clamped(self) -> None:
        assert _effort_from_estimate(15) == 10

    def test_estimate_zero_clamped_to_one(self) -> None:
        assert _effort_from_estimate(0) == 1

    def test_negative_clamped_to_one(self) -> None:
        assert _effort_from_estimate(-3) == 1

    def test_none_defaults_to_five(self) -> None:
        assert _effort_from_estimate(None) == 5

    def test_invalid_defaults_to_five(self) -> None:
        assert _effort_from_estimate("big") == 5


# ═══════════════════════════════════════════════════════════════════
# build_priority_queue end-to-end
# ═══════════════════════════════════════════════════════════════════


class TestBuildPriorityQueue:
    def test_basic_priority_sorting(self) -> None:
        issues = [
            make_issue("GRO-1", priority=4),  # LOW
            make_issue("GRO-2", priority=1),  # URGENT
            make_issue("GRO-3", priority=2),  # HIGH
        ]
        q = build_priority_queue(issues)
        ids = [it.item_id for it in q.items()]
        assert ids == ["GRO-2", "GRO-3", "GRO-1"]

    def test_flagship_outranks_standard(self) -> None:
        issues = [
            make_issue(
                "GRO-A", priority=1, labels=["standard-customer"]
            ),  # URGENT + STANDARD
            make_issue("GRO-B", priority=3, labels=["flagship"]),  # NORMAL + FLAGSHIP
        ]
        # Default balanced config weights priority 10, tier 5 → URGENT
        # wins 40 vs FLAGSHIP wins 25+ → URGENT on top.
        q = build_priority_queue(issues)
        assert q.items()[0].item_id == "GRO-A"

    def test_small_effort_floats_up(self) -> None:
        # Both URGENT STANDARD; GRO-TINY has effort 1, GRO-HUGE has 10.
        issues = [
            make_issue("GRO-HUGE", priority=1, estimate=10),
            make_issue("GRO-TINY", priority=1, estimate=1),
        ]
        q = build_priority_queue(issues)
        assert q.items()[0].item_id == "GRO-TINY"

    def test_payload_round_trips(self) -> None:
        issue = make_issue("GRO-X", priority=2)
        q = build_priority_queue([issue])
        item = q.items()[0]
        assert item.payload is issue
        assert item.payload["title"] == "Test issue GRO-X"

    def test_empty_input(self) -> None:
        q = build_priority_queue([])
        assert len(q) == 0
        assert q.items() == []

    def test_issue_without_identifier_skipped(self) -> None:
        # No identifier AND no id → no item_id → skipped (defensive).
        bad = {
            "id": None,
            "identifier": None,
            "title": "x",
            "priority": 1,
            "estimate": 1,
            "labels": [],
        }
        q = build_priority_queue([bad])
        assert len(q) == 0

    def test_id_used_as_fallback(self) -> None:
        # When identifier is missing but id is present, id is used.
        issue = {
            "id": "uuid-fallback",
            "identifier": None,
            "title": "x",
            "priority": 2,
            "estimate": 5,
            "labels": [],
        }
        q = build_priority_queue([issue])
        assert len(q) == 1
        assert q.items()[0].item_id == "uuid-fallback"

    def test_latency_critical_profile(self) -> None:
        # With latency_critical config, effort weight is 10 (vs 2
        # balanced). Two NORMAL/STANDARD items with very different
        # effort should order by effort aggressively.
        issues = [
            make_issue("GRO-HUGE", priority=3, estimate=10),
            make_issue("GRO-TINY", priority=3, estimate=1),
        ]
        from prismatic.core.priority_queue import PriorityQueueConfig

        q = build_priority_queue(issues, config=PriorityQueueConfig.latency_critical())
        assert q.items()[0].item_id == "GRO-TINY"


# ═══════════════════════════════════════════════════════════════════
# Module-level configuration
# ═══════════════════════════════════════════════════════════════════


class TestDispatcherConfig:
    def test_priority_queue_is_enabled_by_default(self) -> None:
        # PRISMATIC_USE_PRIORITY_QUEUE defaults to "1" in dispatcher.py.
        assert USE_PRIORITY_QUEUE is True
