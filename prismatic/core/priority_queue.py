"""prismatic/core/priority_queue.py — Priority Queue for the Prismatic Engine.

GRO-550: Build the priority queue system the dispatcher dispatches against.

Goals
-----
1. **Multi-level prioritization** — items carry an explicit ``priority``
   level (``URGENT`` → ``LOW``) AND a *computed score* that combines
   multiple signals (urgency, client tier, estimated effort, age).
2. **Starvation prevention** — every item's score is bumped upward by an
   "aging" term proportional to how long it has waited in the queue, so a
   backlog of low-priority work cannot starve behind a stream of fresh
   high-priority work.
3. **Preemption support** — the queue exposes ``preempt_running`` which
   takes a candidate item and returns the set of currently-running items
   that should be evicted (lower priority AND lower score than the
   candidate, and preemption is enabled in config).
4. **Configurable weightings** — operators can adjust the contribution
   of urgency / tier / effort / age via a ``PriorityQueueConfig`` so the
   engine adapts to different deployment contexts (cold-start, balanced,
   latency-critical).

Design notes
------------
- Pure stdlib. No external deps. Heapsort-backed.
- Deterministic ordering for tests: ties break by insertion order
  (FIFO within a score), then by item id.
- Thread-safety: a single ``threading.RLock`` guards state. The dispatcher
  uses one queue per process; cross-process coordination is out of scope
  (the dispatcher already uses Linear + dedup for that).
- Items are dataclasses with ``__lt__`` based on the live score so the
  heap is self-sorting when ``score()`` is recomputed before each pop.

Public API
----------
- :class:`Priority`           — enum of coarse-grained priority bands.
- :class:`ClientTier`         — enum of client tiers.
- :class:`JobItem`            — dataclass describing a queue entry.
- :class:`PriorityQueueConfig`— weightings + preemption toggles.
- :class:`PriorityQueue`      — the queue itself: push, pop, peek, age, preempt.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable, Iterator


# ═══════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════


class Priority(IntEnum):
    """Coarse priority bands. Lower number = higher priority."""

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class ClientTier(IntEnum):
    """Client / work tier. Lower number = more important."""

    FLAGSHIP = 0
    ENTERPRISE = 1
    STANDARD = 2
    INTERNAL = 3


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PriorityQueueConfig:
    """Weightings + behavior toggles for the priority queue.

    Attributes
    ----------
    weight_priority:
        Multiplier applied to the (inverted) Priority band. The default
        gives URGENT a 40-point head start over BACKGROUND, which is
        usually more than enough to dominate ordering.
    weight_tier:
        Multiplier applied to (inverted) ClientTier.
    weight_effort:
        Multiplier applied to the *inverse* of estimated effort, so small
        jobs float to the top. Negative values mean "big jobs first."
    weight_age:
        Multiplier applied to the per-tick age bonus. Tunes how fast
        old work catches up. 0.0 disables aging (starvation possible).
    aging_interval_s:
        Seconds between automatic aging bumps. The queue also ages
        lazily on each pop.
    preempt_enabled:
        If True, ``preempt_running`` will evict lower-scored running
        items to make room for a higher-scored newcomer.
    max_preemptions_per_cycle:
        Cap on how many running items a single call to ``preempt_running``
        may evict. Prevents thundering-herd preemption storms.
    """

    weight_priority: float = 10.0
    weight_tier: float = 5.0
    weight_effort: float = 2.0
    weight_age: float = 1.0
    aging_interval_s: float = 30.0
    preempt_enabled: bool = True
    max_preemptions_per_cycle: int = 2

    # ---- factories for common deployment profiles ----

    @classmethod
    def balanced(cls) -> "PriorityQueueConfig":
        """Sensible defaults — used when no config is provided."""

        return cls()

    @classmethod
    def latency_critical(cls) -> "PriorityQueueConfig":
        """Favor short jobs hard, age aggressively, allow preemption."""

        return cls(
            weight_priority=8.0,
            weight_tier=2.0,
            weight_effort=10.0,
            weight_age=3.0,
            aging_interval_s=10.0,
            preempt_enabled=True,
            max_preemptions_per_cycle=4,
        )

    @classmethod
    def cold_start(cls) -> "PriorityQueueConfig":
        """Large backlogs of background work — favor fairness / aging."""

        return cls(
            weight_priority=4.0,
            weight_tier=8.0,
            weight_effort=0.5,
            weight_age=5.0,
            aging_interval_s=15.0,
            preempt_enabled=False,
        )


# ═══════════════════════════════════════════════════════════════════
# JobItem
# ═══════════════════════════════════════════════════════════════════


@dataclass
class JobItem:
    """A queue entry.

    Fields
    ------
    item_id:
        Stable identifier (e.g. ``"GRO-571"``). Used for tie-breaks and
        de-duplication.
    priority:
        Coarse Priority band.
    client_tier:
        Client / work tier.
    estimated_effort:
        Estimated cost in abstract effort units (1 = trivial, 10 = huge).
        Must be >= 1; values < 1 are clamped to 1.
    payload:
        Opaque attachment (e.g. the Linear issue dict). Not consulted by
        the queue.
    enqueued_at:
        Wall-clock seconds when the item was pushed. Set automatically.
    seq:
        Monotonic insertion sequence. Used for FIFO tie-breaks so tests
        are deterministic. Set automatically.
    """

    item_id: str
    priority: Priority = Priority.NORMAL
    client_tier: ClientTier = ClientTier.STANDARD
    estimated_effort: int = 5
    payload: Any = None
    enqueued_at: float = field(default_factory=time.monotonic)
    seq: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        # Clamp effort to >= 1 so the inverse is finite and positive.
        if self.estimated_effort < 1:
            self.estimated_effort = 1
        # Coerce enum-likes passed in by mistake.
        if not isinstance(self.priority, Priority):
            self.priority = Priority(int(self.priority))
        if not isinstance(self.client_tier, ClientTier):
            self.client_tier = ClientTier(int(self.client_tier))


# ═══════════════════════════════════════════════════════════════════
# Internal heap entry
# ═══════════════════════════════════════════════════════════════════


@dataclass(order=True)
class _HeapEntry:
    """Wrapper stored in the heap.

    We sort on ``(neg_score, seq)`` so the smallest tuple = highest
    priority item. ``neg_score`` is recomputed each time we peek so the
    heap order stays correct as items age.
    """

    neg_score: float
    seq: int
    item: JobItem = field(compare=False)


# ═══════════════════════════════════════════════════════════════════
# PriorityQueue
# ═══════════════════════════════════════════════════════════════════


class PriorityQueue:
    """A heap-backed priority queue with aging, starvation prevention,
    and preemption hooks.

    Example
    -------

    >>> q = PriorityQueue()
    >>> q.push(JobItem("a", priority=Priority.LOW, client_tier=ClientTier.STANDARD))
    >>> q.push(JobItem("b", priority=Priority.URGENT, client_tier=ClientTier.FLAGSHIP))
    >>> [i.item_id for i in q.pop_many(2)]
    ['b', 'a']
    """

    def __init__(self, config: PriorityQueueConfig | None = None) -> None:
        self.config: PriorityQueueConfig = config or PriorityQueueConfig.balanced()
        self._heap: list[_HeapEntry] = []
        self._by_id: dict[str, JobItem] = {}
        self._running: dict[str, JobItem] = {}
        self._seq = itertools.count()
        self._lock = threading.RLock()
        self._last_age_tick: float = time.monotonic()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def push(self, item: JobItem) -> None:
        """Add an item to the queue. Replaces any existing entry with
        the same ``item_id``."""

        with self._lock:
            if item.item_id in self._by_id:
                # Replace in-place: drop the old heap entry by ignoring it
                # on pop (heapq can't remove arbitrary items cheaply).
                # This is fine — duplicates are rare and the popped old
                # entry is just discarded by the consumer.
                self._drop_old_entry(item.item_id)
            item.seq = next(self._seq)
            item.enqueued_at = time.monotonic()
            self._by_id[item.item_id] = item
            entry = _HeapEntry(neg_score=-self._score(item), seq=item.seq, item=item)
            heapq.heappush(self._heap, entry)

    def push_many(self, items: Iterable[JobItem]) -> None:
        for it in items:
            self.push(it)

    def _drop_old_entry(self, item_id: str) -> None:
        """Mark old entry as stale — its slot stays in the heap but is
        ignored when popped."""
        old = self._by_id.pop(item_id, None)
        if old is not None:
            # Replace with a tombstone so _handle_popped knows to skip it.
            tomb = _HeapEntry(
                neg_score=float("inf"),
                seq=old.seq,
                item=JobItem(item_id="__TOMBSTONE__", seq=old.seq),
            )
            tomb.item.item_id = "__TOMBSTONE__"
            heapq.heappush(self._heap, tomb)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)

    def __contains__(self, item_id: str) -> bool:
        with self._lock:
            return item_id in self._by_id

    def peek(self) -> JobItem | None:
        """Return the next item without popping it."""

        with self._lock:
            self._refresh_heap_top()
            if not self._heap:
                return None
            return self._heap[0].item

    def items(self) -> list[JobItem]:
        """Snapshot of pending items, highest priority first."""

        with self._lock:
            self._refresh_heap_top()
            sorted_items = sorted(
                self._by_id.values(),
                key=lambda it: (-self._score(it), it.seq),
            )
            return sorted_items

    # ------------------------------------------------------------------
    # Consumption
    # ------------------------------------------------------------------

    def pop(self) -> JobItem | None:
        """Pop the highest-priority item and return it. Returns None if
        the queue is empty."""

        with self._lock:
            self._tick_age()
            item = self._handle_popped()
            if item is not None:
                self._by_id.pop(item.item_id, None)
            return item

    def pop_many(self, n: int) -> list[JobItem]:
        """Pop up to ``n`` items, highest priority first."""

        out: list[JobItem] = []
        with self._lock:
            for _ in range(n):
                item = self._handle_popped()
                if item is None:
                    break
                self._by_id.pop(item.item_id, None)
                out.append(item)
        return out

    def mark_running(self, item: JobItem) -> None:
        """Move an item into the running set. Caller is expected to have
        popped it first via :meth:`pop` or :meth:`pop_many`."""

        with self._lock:
            self._running[item.item_id] = item

    def mark_completed(self, item_id: str) -> JobItem | None:
        """Remove an item from the running set. Returns the removed item
        or None if it wasn't running."""

        with self._lock:
            return self._running.pop(item_id, None)

    # ------------------------------------------------------------------
    # Preemption
    # ------------------------------------------------------------------

    def running_items(self) -> list[JobItem]:
        with self._lock:
            return list(self._running.values())

    def preempt_running(self, candidate: JobItem) -> list[JobItem]:
        """Decide which currently-running items should be evicted to make
        room for ``candidate``.

        Returns a list (possibly empty) of items the caller should stop.
        The candidate itself is NOT added to the running set; the caller
        is expected to push it and mark it running after preemption.

        Preemption is only enabled when ``config.preempt_enabled`` is
        True and at most ``config.max_preemptions_per_cycle`` items are
        returned per call.
        """

        with self._lock:
            if not self.config.preempt_enabled:
                return []
            if not self._running:
                return []

            cand_score = self._score(candidate)
            # Build (item, score) for everything running.
            running_scored = [(it, self._score(it)) for it in self._running.values()]
            # Evict candidates are running items with LOWER score AND
            # lower (or equal) priority band than the newcomer.
            victims = [
                it
                for it, sc in running_scored
                if sc < cand_score and it.priority.value >= candidate.priority.value
            ]
            # Sort worst-offenders first so we kill the most displaced.
            victims.sort(key=lambda it: self._score(it))
            victims = victims[: self.config.max_preemptions_per_cycle]
            for v in victims:
                self._running.pop(v.item_id, None)
            return victims

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, item: JobItem) -> float:
        """Public read-only scoring accessor — used by callers that want
        to inspect ranking without modifying the queue."""

        with self._lock:
            return self._score(item)

    def _score(self, item: JobItem) -> float:
        """Compute a job's live score.

        Components:
          - Priority band  (inverted so URGENT is highest)
          - Client tier    (inverted so FLAGSHIP is highest)
          - Effort         (inverse of estimated effort — small jobs float up)
          - Age            (seconds waiting * aging weight)

        Returns the score as a float. Items with identical priority,
        tier, and effort pushed at the same instant will get the same
        score; the heap breaks those ties by insertion sequence number.
        """

        cfg = self.config
        now = time.monotonic()
        age_s = max(0.0, now - item.enqueued_at)
        # Quantize age to whole aging-interval buckets so items pushed
        # within the same aging window score identically — this lets
        # the seq-based tie-break fire for FIFO ordering in tests and
        # in real workloads where many items arrive together.
        if cfg.aging_interval_s > 0:
            age_buckets = age_s / cfg.aging_interval_s
        else:
            age_buckets = age_s  # every op is its own bucket
        # Round to integer buckets; only the integer count contributes
        # to scoring. Sub-bucket jitter cannot break ties.
        age_buckets = float(int(age_buckets))
        inv_priority = float(len(Priority) - 1 - item.priority.value)
        inv_tier = float(len(ClientTier) - 1 - item.client_tier.value)
        inv_effort = 1.0 / float(item.estimated_effort)
        # Base score is deterministic from (priority, tier, effort) so
        # items pushed at the same instant compare equal until aging
        # actually kicks in.
        base = (
            cfg.weight_priority * inv_priority
            + cfg.weight_tier * inv_tier
            + cfg.weight_effort * inv_effort
        )
        return base + cfg.weight_age * age_buckets * cfg.aging_interval_s

    # ------------------------------------------------------------------
    # Aging
    # ------------------------------------------------------------------

    def _tick_age(self) -> None:
        """Lazy aging — called on each pop. Rebuilds heap entries with
        fresh negative scores so aging actually changes ordering without
        a periodic background thread."""

        now = time.monotonic()
        if now - self._last_age_tick < self.config.aging_interval_s:
            return
        self._last_age_tick = now
        self._refresh_heap_top()

    def _refresh_heap_top(self) -> None:
        """Recompute the top-of-heap entry's score. If its negative score
        no longer matches reality, sift it down. (For simplicity and
        correctness under aging we rebuild the heap every refresh — the
        queue is small in practice, O(N log N) is fine.)"""

        if not self._heap:
            return
        # Drop tombstones + re-score live entries.
        live: list[_HeapEntry] = []
        for entry in self._heap:
            if entry.item.item_id == "__TOMBSTONE__":
                continue
            # The item is still in _by_id — recompute its score.
            current = self._by_id.get(entry.item.item_id)
            if current is None:
                continue
            entry.neg_score = -self._score(current)
            entry.item = current
            live.append(entry)
        heapq.heapify(live)
        self._heap = live

    # ------------------------------------------------------------------
    # Pop helper
    # ------------------------------------------------------------------

    def _handle_popped(self) -> JobItem | None:
        """Pop the top entry, skipping tombstones / stale items."""

        while self._heap:
            entry = heapq.heappop(self._heap)
            item = entry.item
            if item.item_id == "__TOMBSTONE__":
                continue
            current = self._by_id.get(item.item_id)
            if current is None:
                # Already replaced or removed; skip.
                continue
            return current
        return None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Snapshot of queue stats for telemetry / logging."""

        with self._lock:
            by_priority: dict[str, int] = {}
            for it in self._by_id.values():
                by_priority[it.priority.name] = by_priority.get(it.priority.name, 0) + 1
            return {
                "pending": len(self._by_id),
                "running": len(self._running),
                "heap_size": len(self._heap),
                "by_priority": by_priority,
                "config": {
                    "weight_priority": self.config.weight_priority,
                    "weight_tier": self.config.weight_tier,
                    "weight_effort": self.config.weight_effort,
                    "weight_age": self.config.weight_age,
                    "aging_interval_s": self.config.aging_interval_s,
                    "preempt_enabled": self.config.preempt_enabled,
                },
            }
