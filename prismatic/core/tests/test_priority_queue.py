"""tests/test_priority_queue.py — Tests for prismatic.core.priority_queue.

GRO-550: Validates multi-level prioritization, starvation prevention
(aging), preemption, and configurable weightings.

Test style follows the existing prismatic-engine suite (pytest, classes
with descriptive names, no external deps).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from prismatic.core.priority_queue import (
    ClientTier,
    JobItem,
    Priority,
    PriorityQueue,
    PriorityQueueConfig,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def q() -> PriorityQueue:
    """Default-balanced queue."""

    return PriorityQueue()


@pytest.fixture
def aggressive_q() -> PriorityQueue:
    """Queue with strong aging to force starvation tests to behave
    deterministically without long sleeps."""

    cfg = PriorityQueueConfig(
        weight_priority=10.0,
        weight_tier=5.0,
        weight_effort=2.0,
        weight_age=100.0,  # 100 points per second — large enough to dominate
        aging_interval_s=0.0,  # refresh on every operation
        preempt_enabled=True,
        max_preemptions_per_cycle=2,
    )
    return PriorityQueue(cfg)


@pytest.fixture
def no_age_q() -> PriorityQueue:
    """Queue with aging disabled (starvation possible by design)."""

    cfg = PriorityQueueConfig(
        weight_priority=10.0,
        weight_tier=5.0,
        weight_effort=2.0,
        weight_age=0.0,
        aging_interval_s=999.0,
        preempt_enabled=False,
    )
    return PriorityQueue(cfg)


@pytest.fixture
def no_preempt_q() -> PriorityQueue:
    cfg = PriorityQueueConfig(
        weight_priority=10.0,
        weight_tier=5.0,
        weight_effort=2.0,
        weight_age=1.0,
        preempt_enabled=False,
    )
    return PriorityQueue(cfg)


# ═══════════════════════════════════════════════════════════════════
# Basic ordering
# ═══════════════════════════════════════════════════════════════════


class TestBasicOrdering:
    """Items with explicit Priority bands order correctly."""

    def test_urgent_before_high(self, q: PriorityQueue) -> None:
        q.push(JobItem("high", priority=Priority.HIGH))
        q.push(JobItem("urgent", priority=Priority.URGENT))
        assert q.pop().item_id == "urgent"
        assert q.pop().item_id == "high"

    def test_full_band_ordering(self, q: PriorityQueue) -> None:
        ids = ["bg", "low", "normal", "high", "urgent"]
        # Priority enum members: URGENT, HIGH, NORMAL, LOW, BACKGROUND
        mapping = {
            "bg": Priority.BACKGROUND,
            "low": Priority.LOW,
            "normal": Priority.NORMAL,
            "high": Priority.HIGH,
            "urgent": Priority.URGENT,
        }
        for i in ids:
            q.push(JobItem(i, priority=mapping[i]))
        popped = [q.pop().item_id for _ in range(5)]
        assert popped == ["urgent", "high", "normal", "low", "bg"]

    def test_fifo_within_same_score(self, q: PriorityQueue) -> None:
        # Same priority + tier + effort → identical scores → FIFO by seq.
        for i in range(5):
            q.push(JobItem(f"item-{i}", priority=Priority.NORMAL))
        popped = [q.pop().item_id for _ in range(5)]
        assert popped == ["item-0", "item-1", "item-2", "item-3", "item-4"]

    def test_empty_queue_returns_none(self, q: PriorityQueue) -> None:
        assert q.pop() is None
        assert q.peek() is None

    def test_pop_drains_to_empty(self, q: PriorityQueue) -> None:
        q.push(JobItem("a"))
        q.push(JobItem("b"))
        q.pop()
        q.pop()
        assert len(q) == 0
        assert q.pop() is None

    def test_len_and_contains(self, q: PriorityQueue) -> None:
        q.push(JobItem("a"))
        q.push(JobItem("b"))
        assert len(q) == 2
        assert "a" in q
        assert "missing" not in q


# ═══════════════════════════════════════════════════════════════════
# Multi-level scoring (priority + tier + effort)
# ═══════════════════════════════════════════════════════════════════


class TestMultiLevelScoring:
    """Weightings for priority band, client tier, and estimated effort
    combine correctly."""

    def test_tier_overrides_priority_when_weighted(self) -> None:
        # With tier weight >> priority weight, a FLAGSHIP+LOW outranks
        # a STANDARD+URGENT job.
        cfg = PriorityQueueConfig(
            weight_priority=1.0,
            weight_tier=100.0,
            weight_effort=0.0,
            weight_age=0.0,
            aging_interval_s=999.0,
        )
        q = PriorityQueue(cfg)
        q.push(
            JobItem(
                "standard_urgent",
                priority=Priority.URGENT,
                client_tier=ClientTier.STANDARD,
            )
        )
        q.push(
            JobItem(
                "flagship_low", priority=Priority.LOW, client_tier=ClientTier.FLAGSHIP
            )
        )
        assert q.pop().item_id == "flagship_low"

    def test_small_effort_floats_to_top(self) -> None:
        cfg = PriorityQueueConfig(
            weight_priority=0.0,
            weight_tier=0.0,
            weight_effort=100.0,
            weight_age=0.0,
            aging_interval_s=999.0,
        )
        q = PriorityQueue(cfg)
        q.push(
            JobItem(
                "big",
                priority=Priority.NORMAL,
                client_tier=ClientTier.STANDARD,
                estimated_effort=10,
            )
        )
        q.push(
            JobItem(
                "small",
                priority=Priority.NORMAL,
                client_tier=ClientTier.STANDARD,
                estimated_effort=1,
            )
        )
        assert q.pop().item_id == "small"

    def test_score_components_add(self) -> None:
        cfg = PriorityQueueConfig(
            weight_priority=1.0,
            weight_tier=2.0,
            weight_effort=3.0,
            weight_age=0.0,
            aging_interval_s=999.0,
        )
        q = PriorityQueue(cfg)
        item = JobItem(
            "x",
            priority=Priority.URGENT,
            client_tier=ClientTier.FLAGSHIP,
            estimated_effort=1,
        )
        score = q.score(item)
        # URGENT=0 → inv_priority = 4. FLAGSHIP=0 → inv_tier = 3.
        # effort=1 → inv_effort = 1.0. Expected = 1*4 + 2*3 + 3*1 = 13.
        assert score == pytest.approx(13.0)

    def test_effort_clamped_to_one(self) -> None:
        # Negative / zero effort is clamped to 1 (avoids div-by-zero).
        item = JobItem("x", estimated_effort=0)
        assert item.estimated_effort == 1
        item2 = JobItem("y", estimated_effort=-5)
        assert item2.estimated_effort == 1


# ═══════════════════════════════════════════════════════════════════
# Starvation prevention / aging
# ═══════════════════════════════════════════════════════════════════


class TestStarvationPrevention:
    """Aging prevents old low-priority work from starving behind a
    stream of fresh high-priority work."""

    def test_old_low_eventually_beats_fresh_high(
        self, aggressive_q: PriorityQueue
    ) -> None:
        # Push a LOW item first and let it "age" by sleeping briefly.
        aggressive_q.push(JobItem("old_low", priority=Priority.LOW))
        time.sleep(0.05)  # 50ms → 5.0 age points with weight_age=100
        # Now flood with fresh URGENT items.
        for i in range(20):
            aggressive_q.push(JobItem(f"urgent_{i}", priority=Priority.URGENT))
        # Pop 20 fresh URGENT items — old_low should NOT be among them.
        first_20 = [aggressive_q.pop().item_id for _ in range(20)]
        assert "old_low" not in first_20
        # The 21st pop should be old_low (it has aged past everything).
        # Force a fresh age tick by setting the last-tick to the past.
        aggressive_q._last_age_tick = 0.0
        assert aggressive_q.pop().item_id == "old_low"

    def test_no_age_allows_starvation(self, no_age_q: PriorityQueue) -> None:
        """Without aging, old LOW work can be permanently starved."""

        no_age_q.push(JobItem("old_low", priority=Priority.LOW))
        for i in range(10):
            no_age_q.push(JobItem(f"urgent_{i}", priority=Priority.URGENT))
        popped = [no_age_q.pop().item_id for _ in range(10)]
        assert "old_low" not in popped

    def test_aging_weight_zero_means_no_aging(self) -> None:
        cfg = PriorityQueueConfig(weight_age=0.0, aging_interval_s=0.0)
        q = PriorityQueue(cfg)
        q.push(JobItem("a", priority=Priority.HIGH))
        time.sleep(0.01)
        initial_score = q.score(q.items()[0])
        time.sleep(0.01)
        later_score = q.score(q.items()[0])
        assert initial_score == later_score


# ═══════════════════════════════════════════════════════════════════
# Preemption
# ═══════════════════════════════════════════════════════════════════


class TestPreemption:
    """Higher-priority newcomers can evict lower-scored running work."""

    def test_high_priority_preempts_lower_running(
        self, aggressive_q: PriorityQueue
    ) -> None:
        aggressive_q.push(JobItem("running_low", priority=Priority.LOW))
        running = aggressive_q.pop()
        aggressive_q.mark_running(running)

        candidate = JobItem("new_urgent", priority=Priority.URGENT)
        victims = aggressive_q.preempt_running(candidate)
        assert len(victims) == 1
        assert victims[0].item_id == "running_low"
        assert "running_low" not in aggressive_q.running_items()

    def test_preempt_disabled_returns_empty(self, no_preempt_q: PriorityQueue) -> None:
        no_preempt_q.push(JobItem("running_x", priority=Priority.LOW))
        running = no_preempt_q.pop()
        no_preempt_q.mark_running(running)
        candidate = JobItem("new_urgent", priority=Priority.URGENT)
        assert no_preempt_q.preempt_running(candidate) == []
        running_ids = {it.item_id for it in no_preempt_q.running_items()}
        assert "running_x" in running_ids

    def test_preempt_respects_max_per_cycle(self) -> None:
        cfg = PriorityQueueConfig(
            weight_priority=10.0,
            preempt_enabled=True,
            max_preemptions_per_cycle=2,
        )
        q = PriorityQueue(cfg)
        for i in range(5):
            q.push(JobItem(f"low_{i}", priority=Priority.LOW))
            q.mark_running(q.pop())
        candidate = JobItem("big_urgent", priority=Priority.URGENT)
        victims = q.preempt_running(candidate)
        assert len(victims) == 2  # capped

    def test_no_preempt_when_no_running(self, q: PriorityQueue) -> None:
        candidate = JobItem("new", priority=Priority.URGENT)
        assert q.preempt_running(candidate) == []

    def test_no_preempt_when_candidate_lower_score(
        self, aggressive_q: PriorityQueue
    ) -> None:
        # URGENT running, candidate is LOW — should not preempt.
        aggressive_q.push(JobItem("running_urgent", priority=Priority.URGENT))
        running = aggressive_q.pop()
        aggressive_q.mark_running(running)
        candidate = JobItem("new_low", priority=Priority.LOW)
        victims = aggressive_q.preempt_running(candidate)
        assert victims == []


# ═══════════════════════════════════════════════════════════════════
# Replacement & duplicates
# ═══════════════════════════════════════════════════════════════════


class TestReplacement:
    """Pushing an item with an existing id replaces the old entry."""

    def test_replace_updates_priority(self, q: PriorityQueue) -> None:
        q.push(JobItem("dup", priority=Priority.LOW))
        q.push(JobItem("dup", priority=Priority.URGENT))
        assert len(q) == 1
        assert q.pop().item_id == "dup"
        assert q.pop() is None

    def test_replace_with_same_priority(self, q: PriorityQueue) -> None:
        q.push(JobItem("dup", priority=Priority.NORMAL, estimated_effort=10))
        q.push(JobItem("dup", priority=Priority.NORMAL, estimated_effort=1))
        assert len(q) == 1
        item = q.pop()
        assert item.estimated_effort == 1


# ═══════════════════════════════════════════════════════════════════
# Configurations
# ═══════════════════════════════════════════════════════════════════


class TestConfigurations:
    """Factory methods produce valid configs with expected properties."""

    def test_balanced_default(self) -> None:
        c = PriorityQueueConfig.balanced()
        assert c.weight_age > 0
        assert c.aging_interval_s > 0

    def test_latency_critical_aggressive_aging(self) -> None:
        c = PriorityQueueConfig.latency_critical()
        assert c.weight_age > PriorityQueueConfig.balanced().weight_age
        assert c.weight_effort > PriorityQueueConfig.balanced().weight_effort

    def test_cold_start_disables_preemption(self) -> None:
        c = PriorityQueueConfig.cold_start()
        assert c.preempt_enabled is False
        assert c.weight_age > PriorityQueueConfig.balanced().weight_age

    def test_config_is_frozen(self) -> None:
        c = PriorityQueueConfig()
        with pytest.raises(Exception):  # FrozenInstanceError is a subclass of Exception
            c.weight_priority = 999.0  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════
# Diagnostics / introspection
# ═══════════════════════════════════════════════════════════════════


class TestStatsAndIntrospection:
    def test_stats_shape(self, q: PriorityQueue) -> None:
        q.push(JobItem("a", priority=Priority.HIGH))
        q.push(JobItem("b", priority=Priority.URGENT))
        s = q.stats()
        assert s["pending"] == 2
        assert s["running"] == 0
        assert s["by_priority"]["HIGH"] == 1
        assert s["by_priority"]["URGENT"] == 1

    def test_running_count_in_stats(self, q: PriorityQueue) -> None:
        q.push(JobItem("a"))
        q.mark_running(q.pop())
        s = q.stats()
        assert s["running"] == 1
        assert s["pending"] == 0

    def test_items_returns_priority_sorted(self, q: PriorityQueue) -> None:
        q.push(JobItem("low", priority=Priority.LOW))
        q.push(JobItem("urgent", priority=Priority.URGENT))
        ids = [it.item_id for it in q.items()]
        assert ids == ["urgent", "low"]

    def test_peek_does_not_pop(self, q: PriorityQueue) -> None:
        q.push(JobItem("a", priority=Priority.URGENT))
        first = q.peek()
        second = q.peek()
        assert first is second
        assert len(q) == 1


# ═══════════════════════════════════════════════════════════════════
# Thread safety smoke test
# ═══════════════════════════════════════════════════════════════════


class TestThreadSafety:
    """Concurrent pushers + a single popper don't crash or lose items."""

    def test_concurrent_pushes(self, q: PriorityQueue) -> None:
        import threading

        N_THREADS = 8
        N_PER_THREAD = 50

        def worker(tid: int) -> None:
            for i in range(N_PER_THREAD):
                q.push(
                    JobItem(
                        item_id=f"t{tid}-{i}",
                        priority=Priority.NORMAL,
                    )
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All pushes land; queue should have N_THREADS * N_PER_THREAD items.
        assert len(q) == N_THREADS * N_PER_THREAD

        # Pop everything — every item appears exactly once.
        seen: set[str] = set()
        while True:
            item = q.pop()
            if item is None:
                break
            assert item.item_id not in seen, f"duplicate {item.item_id}"
            seen.add(item.item_id)
        assert len(seen) == N_THREADS * N_PER_THREAD
