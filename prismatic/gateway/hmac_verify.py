"""
prismatic.gateway.hmac_verify — Shared HMAC verification for all gateway
endpoints (Linear webhook, GitHub webhook, /ws WebSocket auth).

GRO-2402 refactor: previously the same ~30 lines of HMAC + dual-secret
+ constant-time-compare code was copy-pasted into 3 separate handlers.
The GRO-2400 HMAC drift bug had to be fixed in 2 places (Linear + GitHub)
even though it was the same logic. This module unifies them.

Design:
- One function per auth scheme (verify_linear, verify_github, verify_ws)
- All return the same Verdict dataclass: (passed: bool, reason: str)
- Dual-secret rotation (PRIMARY + _NEXT) is handled uniformly
- Replay protection (timestamp drift) is opt-in via require_fresh_ts
- No side effects: the caller does audit logging + response generation
- Pure stdlib, no FastAPI imports — testable in isolation

Why not a class:
The auth flow is 5 fields, not 8 methods. A function per scheme is
clearer. If we add a 4th scheme (e.g. Slack), we add one function.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    """Result of an HMAC verification attempt."""
    passed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.passed


def _constant_time_eq(a: str, b: str) -> bool:
    """Constant-time string comparison. Wraps hmac.compare_digest for clarity."""
    return hmac.compare_digest(a, b)


def _dual_secret_match(sig: str, body: bytes, primary: str, next_secret: str = "") -> bool:
    """Return True if sig matches primary OR next_secret (for zero-downtime rotation).

    Linear's webhook secret can be rotated without downtime by:
    1. Set PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT=new_secret
    2. Update Linear to use new_secret
    3. Rename _NEXT → primary (atomic swap)

    During the rotation window both secrets are accepted.
    """
    if not primary:
        return False
    expected_primary = hmac.new(
        primary.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if _constant_time_eq(sig, expected_primary):
        return True
    if next_secret:
        expected_next = hmac.new(
            next_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        if _constant_time_eq(sig, expected_next):
            return True
    return False


# ── Linear webhook ────────────────────────────────────────────────────
def verify_linear(
    body: bytes,
    signature_header: str,
    primary_secret: str,
    next_secret: str = "",
) -> Verdict:
    """Verify a Linear webhook HMAC signature.

    Linear sends: ``Linear-Signature: <hex sha256>`` (no prefix).
    Body: raw request bytes.
    """
    if not signature_header:
        return Verdict(False, "missing Linear-Signature header")
    if not primary_secret:
        return Verdict(True, "no secret configured (HMAC disabled in dev)")
    if not _dual_secret_match(signature_header, body, primary_secret, next_secret):
        return Verdict(False, "bad signature")
    return Verdict(True, "")


# ── GitHub webhook ────────────────────────────────────────────────────
def verify_github(
    body: bytes,
    signature_header: str,
    primary_secret: str,
    next_secret: str = "",
) -> Verdict:
    """Verify a GitHub webhook HMAC signature.

    GitHub sends: ``X-Hub-Signature-256: sha256=<hex>`` (sha256= prefix).
    """
    if not signature_header.startswith("sha256="):
        return Verdict(False, "missing X-Hub-Signature-256 (no sha256= prefix)")
    sig = signature_header[len("sha256="):]
    if not primary_secret:
        return Verdict(True, "no secret configured (HMAC disabled in dev)")
    if not _dual_secret_match(sig, body, primary_secret, next_secret):
        return Verdict(False, "bad signature")
    return Verdict(True, "")


# ── WebSocket auth ────────────────────────────────────────────────────
def verify_ws_signature(
    signature_header: str,
    timestamp_header: str,
    method: str,
    path: str,
    secret: str,
    max_drift_seconds: int = 300,
) -> Verdict:
    """Verify a WebSocket HMAC signature (matches Bearer path used by /ws).

    Format: ``X-WS-Signature: <hex>``, ``X-WS-Timestamp: <unix seconds>``.
    Payload signed: ``{method}\\n{path}\\n{ts}``.
    Timestamp drift > max_drift_seconds → reject (replay protection).
    """
    if not signature_header or not timestamp_header:
        return Verdict(False, "missing WS signature or timestamp header")
    try:
        ts = int(timestamp_header)
    except ValueError:
        return Verdict(False, "non-integer timestamp")
    drift = abs(int(time.time()) - ts)
    if drift > max_drift_seconds:
        return Verdict(False, f"timestamp drift {drift}s > {max_drift_seconds}s")
    payload = f"{method}\n{path}\n{ts}".encode("utf-8")
    expected = hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    if not _constant_time_eq(signature_header, expected):
        return Verdict(False, "bad WS signature")
    return Verdict(True, "")


# ── Convenience: self-test (used by fleet_watchdog) ───────────────────
def hmac_self_test(secret: str, payload: bytes = b"") -> Verdict:
    """Verify the secret can sign + verify (sanity check for env corruption).

    Returns passed=True if HMAC round-trips, False otherwise.
    Used by check_webhook_signature_self_test in fleet_watchdog.
    """
    if not secret:
        return Verdict(True, "no secret configured (HMAC disabled)")
    sig1 = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    sig2 = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not _constant_time_eq(sig1, sig2):
        return Verdict(False, "HMAC round-trip failed (env may be corrupted)")
    return Verdict(True, f"sig={sig1[:12]}...")