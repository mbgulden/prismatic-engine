"""Semaphore-based concurrency governor for outbound network sessions."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable


DEFAULT_LANE_LIMITS = {
    "L4": 2,
    "lane_4": 2,
    "lead_gen": 2,
    "L5": 1,
    "lane_5": 1,
    "outreach": 1,
    "L6": 1,
    "lane_6": 1,
    "linkedin": 1,
}


@dataclass
class SessionLease:
    """Context manager returned by SessionGovernor.acquire."""

    governor: "SessionGovernor"
    lane_id: str
    acquired: bool = False

    def __enter__(self) -> "SessionLease":
        if not self.acquired:
            self.governor._acquire(self.lane_id)
            self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.release()

    def release(self) -> None:
        if self.acquired:
            self.governor.release(self.lane_id)
            self.acquired = False


class SessionGovernor:
    """Enforce global and per-lane outbound concurrency caps.

    Defaults match EDGE-WORK-001: max 5 global outbound requests, lane 4 max 2,
    lane 5 max 1, lane 6 max 1. Unknown lanes default to one concurrent request.
    """

    def __init__(
        self,
        *,
        global_limit: int = 5,
        lane_limits: dict[str, int] | None = None,
        on_change: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        if global_limit <= 0:
            raise ValueError("global_limit must be positive")
        self.global_limit = global_limit
        self.lane_limits = dict(DEFAULT_LANE_LIMITS)
        if lane_limits:
            self.lane_limits.update(lane_limits)
        self._global = threading.BoundedSemaphore(global_limit)
        self._lane_semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._active_by_lane: dict[str, int] = {}
        self._active_global = 0
        self._lock = threading.RLock()
        self._on_change = on_change

    def acquire(self, lane_id: str, *, blocking: bool = True, timeout: float | None = None) -> SessionLease:
        acquired = self._try_acquire(lane_id, blocking=blocking, timeout=timeout)
        return SessionLease(self, lane_id, acquired=acquired)

    def _acquire(self, lane_id: str) -> None:
        self._try_acquire(lane_id, blocking=True, timeout=None)

    def _try_acquire(self, lane_id: str, *, blocking: bool, timeout: float | None) -> bool:
        lane_key = self._normalize_lane(lane_id)
        lane_sem = self._get_lane_semaphore(lane_key)
        global_ok = self._global.acquire(blocking=blocking, timeout=timeout) if timeout is not None else self._global.acquire(blocking=blocking)
        if not global_ok:
            raise TimeoutError(f"global outbound session limit reached ({self.global_limit})")
        lane_ok = lane_sem.acquire(blocking=blocking, timeout=timeout) if timeout is not None else lane_sem.acquire(blocking=blocking)
        if not lane_ok:
            self._global.release()
            raise TimeoutError(f"lane outbound session limit reached for {lane_key}")
        with self._lock:
            self._active_global += 1
            self._active_by_lane[lane_key] = self._active_by_lane.get(lane_key, 0) + 1
            self._emit()
        return True

    def release(self, lane_id: str) -> None:
        lane_key = self._normalize_lane(lane_id)
        lane_sem = self._get_lane_semaphore(lane_key)
        with self._lock:
            if self._active_by_lane.get(lane_key, 0) <= 0:
                raise RuntimeError(f"release called for inactive lane {lane_key}")
            self._active_by_lane[lane_key] -= 1
            if self._active_by_lane[lane_key] == 0:
                del self._active_by_lane[lane_key]
            self._active_global -= 1
        lane_sem.release()
        self._global.release()
        with self._lock:
            self._emit()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            result = dict(self._active_by_lane)
            result["global"] = self._active_global
            return result

    def lane_limit(self, lane_id: str) -> int:
        return self.lane_limits.get(self._normalize_lane(lane_id), 1)

    def _normalize_lane(self, lane_id: str) -> str:
        return str(lane_id).strip() or "unknown"

    def _get_lane_semaphore(self, lane_key: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._lane_semaphores.get(lane_key)
            if sem is None:
                sem = threading.BoundedSemaphore(self.lane_limit(lane_key))
                self._lane_semaphores[lane_key] = sem
            return sem

    def _emit(self) -> None:
        if self._on_change:
            self._on_change(self.snapshot())
