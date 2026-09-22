"""Exact-hash memoization for decisions. Opt-in, bounded, thread-safe.

The memo key is the SHA-256 of the canonical JSON of
``(schema_version, backend, model, question wire shapes, canonical state)`` —
an EXACT match only. A merely *similar* state never hits: returning a cached
decision for similar-but-different state would violate the no-invention rule
(the primitive must never present a decision it did not actually compute for
this state).

What this deliberately does NOT do:

- No semantic caching (embedding similarity, fuzzy matching). Refused: the
  no-invention rule forbids it.
- No cross-backend sharing: the backend+model are part of the key, so a
  decision computed by OpenRouter is never served as a TypeSafe decision.
- No caching of deterministic-fallback results (pointless: they are already
  free to recompute, and caching them would mask staleness of defaults).

The key is a hash only — it never contains state values or credentials.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any


def memo_key(
    *,
    schema_version: str,
    backend_name: str,
    model: str | None,
    questions_wire: dict[str, Any],
    state_canonical_json: str,
) -> str:
    """Canonical memo key: exact-match only, hash-only (no values inside)."""
    canonical = json.dumps(
        {
            "schema_version": schema_version,
            "backend": backend_name,
            "model": model,
            "questions": questions_wire,
            "state": state_canonical_json,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoCache:
    """Bounded, TTL'd, thread-safe exact-match cache for DecisionResults."""

    def __init__(self, *, max_entries: int = 1024, ttl_s: float = 3600.0):
        self._max_entries = max(1, max_entries)
        self._ttl_s = ttl_s if ttl_s > 0 else 3600.0
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def lookup(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, result = entry
            if now - stored_at > self._ttl_s:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return result

    def store(self, key: str, result: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
