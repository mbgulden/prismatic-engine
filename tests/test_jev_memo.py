"""Tests for exact-hash memoization: exact match only, bounded, TTL'd."""

import threading
import time

from prismatic.jev.memo import MemoCache, memo_key


def _key(**overrides):
    base = dict(
        schema_version="1.0",
        backend_name="openrouter",
        model="typesafe/jev-1.13",
        questions_wire={"v": {"type": "noul", "wire_version": "noul.v1"}},
        state_canonical_json='{"a": 1}',
    )
    base.update(overrides)
    return memo_key(**base)


def test_memo_key_is_deterministic_hex():
    k1, k2 = _key(), _key()
    assert k1 == k2
    assert len(k1) == 64
    int(k1, 16)  # valid hex


def test_memo_key_changes_with_state():
    assert _key() != _key(state_canonical_json='{"a": 2}')


def test_memo_key_changes_with_backend_and_model():
    assert _key() != _key(backend_name="typesafe")
    assert _key() != _key(model="other-model")


def test_memo_key_changes_with_questions():
    assert _key() != _key(questions_wire={"v": {"type": "score"}})


def test_memo_key_carries_no_values():
    # hash-only: no state values, no credentials leak into the key
    assert "secret" not in _key(state_canonical_json='{"k": "secret-value"}')


def test_cache_hit_and_miss():
    cache = MemoCache()
    assert cache.lookup("nope") is None
    cache.store("k", "result")
    assert cache.lookup("k") == "result"


def test_cache_ttl_expiry():
    cache = MemoCache(ttl_s=0.05)
    cache.store("k", "result")
    assert cache.lookup("k") == "result"
    time.sleep(0.06)
    assert cache.lookup("k") is None


def test_cache_lru_eviction():
    cache = MemoCache(max_entries=2)
    cache.store("a", 1)
    cache.store("b", 2)
    assert len(cache) == 2
    cache.store("c", 3)  # evicts least-recently-used "a"
    assert len(cache) == 2
    assert cache.lookup("a") is None
    assert cache.lookup("b") == 2
    assert cache.lookup("c") == 3


def test_cache_is_thread_safe():
    cache = MemoCache(max_entries=100)
    errors = []

    def worker(n):
        try:
            for i in range(50):
                cache.store(f"k-{n}-{i}", i)
                cache.lookup(f"k-{n}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
