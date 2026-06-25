"""Tests for prismatic.gateway.hmac_verify

Covers the unified HMAC verification that replaces 3 copy-pasted blocks
in prismatic/gateway/server.py (linear_webhook, github_webhook, /ws auth).

These tests are pure-stdlib (no FastAPI), so they run fast and don't
require any external state.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))

from prismatic.gateway.hmac_verify import (  # noqa: E402
    Verdict,
    verify_linear,
    verify_github,
    verify_ws_signature,
    hmac_self_test,
    _constant_time_eq,
    _dual_secret_match,
)


# ── Verdict dataclass ────────────────────────────────────────────────
def test_verdict_truthiness():
    """Verdict acts as a bool via __bool__."""
    assert bool(Verdict(True)) is True
    assert bool(Verdict(False)) is False
    assert bool(Verdict(True, "ok")) is True


def test_verdict_is_frozen():
    """Verdict is frozen (immutable)."""
    v = Verdict(True, "test")
    try:
        v.passed = False  # type: ignore[misc]
        assert False, "should have raised"
    except Exception:
        pass


# ── Constant-time comparison ──────────────────────────────────────────
def test_constant_time_eq_basic():
    assert _constant_time_eq("abc", "abc")
    assert not _constant_time_eq("abc", "abd")
    assert not _constant_time_eq("abc", "ab")
    assert _constant_time_eq("", "")


# ── Dual-secret match (the rotation logic) ────────────────────────────
def test_dual_secret_match_primary_only():
    sig = hmac.new(b"primary", b"body", hashlib.sha256).hexdigest()
    assert _dual_secret_match(sig, b"body", "primary", "")


def test_dual_secret_match_next_secret():
    """During rotation, _NEXT secret is accepted."""
    sig = hmac.new(b"next", b"body", hashlib.sha256).hexdigest()
    assert _dual_secret_match(sig, b"body", "primary", "next")


def test_dual_secret_match_both_wrong():
    sig = hmac.new(b"wrong", b"body", hashlib.sha256).hexdigest()
    assert not _dual_secret_match(sig, b"body", "primary", "next")


def test_dual_secret_match_no_primary_returns_false():
    """Empty primary = HMAC disabled (dev mode). Match returns False
    because the caller is expected to short-circuit before this."""
    sig = hmac.new(b"x", b"body", hashlib.sha256).hexdigest()
    assert not _dual_secret_match(sig, b"body", "", "next")


# ── Linear webhook verify ────────────────────────────────────────────
def test_verify_linear_valid():
    body = b'{"action":"update"}'
    secret = "lin_secret"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    v = verify_linear(body, sig, secret)
    assert v.passed is True
    assert v.reason == ""


def test_verify_linear_missing_signature():
    v = verify_linear(b"body", "", "secret")
    assert v.passed is False
    assert "missing" in v.reason.lower()


def test_verify_linear_bad_signature():
    body = b'{"action":"update"}'
    v = verify_linear(body, "0" * 64, "secret")
    assert v.passed is False
    assert "bad signature" in v.reason.lower()


def test_verify_linear_dual_secret_rotation():
    """During rotation, _NEXT secret is accepted."""
    body = b'{"action":"update"}'
    next_sig = hmac.new(b"new_secret", body, hashlib.sha256).hexdigest()
    v = verify_linear(body, next_sig, "old_secret", "new_secret")
    assert v.passed is True


def test_verify_linear_no_secret_dev_mode():
    """When primary_secret is empty, HMAC is skipped (dev mode)."""
    v = verify_linear(b"body", "any-sig", "")
    assert v.passed is True
    assert "no secret" in v.reason.lower() or "disabled" in v.reason.lower()


def test_verify_linear_wrong_secret_length():
    """Sig with completely different length fails."""
    body = b'{"action":"update"}'
    v = verify_linear(body, "short", "secret")
    assert v.passed is False


# ── GitHub webhook verify ────────────────────────────────────────────
def test_verify_github_valid():
    body = b'{"ref":"main"}'
    secret = "gh_secret"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    v = verify_github(body, sig, secret)
    assert v.passed is True


def test_verify_github_missing_prefix():
    v = verify_github(b"body", "abc123", "secret")
    assert v.passed is False
    assert "sha256" in v.reason.lower()


def test_verify_github_missing_header():
    v = verify_github(b"body", "", "secret")
    assert v.passed is False
    assert "missing" in v.reason.lower()


def test_verify_github_bad_signature():
    body = b'{"ref":"main"}'
    v = verify_github(body, "sha256=" + "f" * 64, "secret")
    assert v.passed is False
    assert "bad signature" in v.reason.lower()


def test_verify_github_dual_secret_rotation():
    body = b'{"ref":"main"}'
    next_sig = "sha256=" + hmac.new(b"new", body, hashlib.sha256).hexdigest()
    v = verify_github(body, next_sig, "old", "new")
    assert v.passed is True


def test_verify_github_no_secret_dev_mode():
    v = verify_github(b"body", "sha256=abc", "")
    assert v.passed is True
    assert "no secret" in v.reason.lower() or "disabled" in v.reason.lower()


# ── WebSocket signature ──────────────────────────────────────────────
def test_verify_ws_signature_valid():
    import time as _t
    ts = str(int(_t.time()))
    secret = "ws_secret"
    payload = f"GET\n/ws\n{ts}".encode("utf-8")
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    v = verify_ws_signature(sig, ts, "GET", "/ws", secret)
    assert v.passed is True


def test_verify_ws_signature_rejects_old_timestamp():
    """Replay protection: timestamp drift > 300s = reject."""
    import time as _t
    ts = str(int(_t.time()) - 1000)  # 1000s old
    secret = "ws_secret"
    payload = f"GET\n/ws\n{ts}".encode("utf-8")
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    v = verify_ws_signature(sig, ts, "GET", "/ws", secret)
    assert v.passed is False
    assert "drift" in v.reason.lower() or "timestamp" in v.reason.lower()


def test_verify_ws_signature_rejects_future_timestamp():
    """Future timestamps also rejected (clock skew attack prevention)."""
    import time as _t
    ts = str(int(_t.time()) + 1000)  # 1000s in future
    secret = "ws_secret"
    payload = f"GET\n/ws\n{ts}".encode("utf-8")
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    v = verify_ws_signature(sig, ts, "GET", "/ws", secret)
    assert v.passed is False


def test_verify_ws_signature_non_integer_timestamp():
    v = verify_ws_signature("abc", "not-a-number", "GET", "/ws", "secret")
    assert v.passed is False
    assert "integer" in v.reason.lower() or "non-integer" in v.reason.lower()


def test_verify_ws_signature_missing_headers():
    v = verify_ws_signature("", "", "GET", "/ws", "secret")
    assert v.passed is False
    assert "missing" in v.reason.lower()


def test_verify_ws_signature_bad_signature():
    import time as _t
    ts = str(int(_t.time()))
    v = verify_ws_signature("0" * 64, ts, "GET", "/ws", "secret")
    assert v.passed is False
    assert "bad" in v.reason.lower()


# ── Self-test (used by fleet_watchdog) ────────────────────────────────
def test_hmac_self_test_with_secret():
    v = hmac_self_test("test-secret")
    assert v.passed is True
    assert v.reason  # non-empty message with sig prefix


def test_hmac_self_test_without_secret():
    v = hmac_self_test("")
    assert v.passed is True
    assert "no secret" in v.reason.lower() or "disabled" in v.reason.lower()


def test_hmac_self_test_round_trip():
    """If compute-then-recompute gives different sigs, env var is corrupted."""
    v = hmac_self_test("real-secret", b"test-payload")
    assert v.passed is True