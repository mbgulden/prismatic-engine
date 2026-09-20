"""prismatic/gateway/webhook_auth.py — HMAC webhook authentication (fail-closed).

Two schemes live here:

1. **Vendor schemes** (GitHub ``X-Hub-Signature-256``, Linear ``linear-signature``):
   the vendor signs the raw request body with a shared secret. Use
   :func:`verify_vendor_hmac` — it fails closed (missing secret, missing
   signature, or bad signature all reject).

2. **Prismatic generic scheme** for first-party ``/webhooks/*`` integrations
   (Zapier, PWP Stripe listener, future senders):

   - Header: ``X-Prismatic-Signature: t=<unix_timestamp>,v1=<hex>``
   - Signed bytes: ``b"<timestamp>.<raw_body>"`` (HMAC-SHA256)
   - Secrets: ``PRISMATIC_WEBHOOK_SECRET`` (+ ``_SECONDARY`` for rotation)
   - Freshness: ``PRISMATIC_WEBHOOK_MAX_AGE_SECONDS`` (default 300)

   Every rejection path returns a short machine-readable reason code so the
   caller can log it to the audit ledger without leaking secret material.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Prismatic-Signature"
DEFAULT_MAX_AGE_SECONDS = 300


# ── Configuration ────────────────────────────────────────────────────


def get_webhook_secrets() -> list[str]:
    """Read PRIMARY + SECONDARY Prismatic webhook signing secrets.

    Supports 2-slot rotation: PRIMARY is current, SECONDARY is the previous
    or next secret during rotation. Both are accepted for verification.
    Secrets come from the environment only — never committed, never logged.
    """
    seen: set[str] = set()
    out: list[str] = []
    for key in (
        "PRISMATIC_WEBHOOK_SECRET",
        "PRISMATIC_WEBHOOK_SECRET_SECONDARY",
    ):
        value = os.environ.get(key, "")
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def get_max_age_seconds() -> int:
    """Freshness window for signed deliveries (seconds)."""
    raw = os.environ.get("PRISMATIC_WEBHOOK_MAX_AGE_SECONDS", "")
    try:
        window = int(raw)
    except (TypeError, ValueError):
        window = DEFAULT_MAX_AGE_SECONDS
    if window < 1:
        logger.warning(
            "PRISMATIC_WEBHOOK_MAX_AGE_SECONDS=%r invalid; using default %d",
            raw,
            DEFAULT_MAX_AGE_SECONDS,
        )
        return DEFAULT_MAX_AGE_SECONDS
    return window


# ── Prismatic generic scheme ─────────────────────────────────────────


def sign_delivery(
    secret: str, body: bytes, timestamp: int | None = None
) -> str:
    """Build an ``X-Prismatic-Signature`` header value for a delivery.

    Operator/test helper — this is how a sender signs a webhook delivery.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    mac = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={mac}"


def _parse_signature_header(value: str) -> tuple[int, str] | None:
    """Parse ``t=<ts>,v1=<hex>``. Returns ``(timestamp, hex)`` or None."""
    try:
        parts = dict(
            item.split("=", 1) for item in value.split(",") if "=" in item
        )
        ts = int(parts["t"].strip())
        presented = parts["v1"].strip().lower()
    except (KeyError, ValueError, AttributeError):
        return None
    if len(presented) != 64 or any(
        c not in "0123456789abcdef" for c in presented
    ):
        return None
    return ts, presented


def verify_delivery(
    body: bytes,
    signature_header: str | None,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Verify a Prismatic-scheme webhook delivery. Fail-closed.

    Returns ``(True, "ok")`` or ``(False, reason)`` where reason is one of:
    ``no-secret-configured``, ``missing-signature``, ``malformed-signature``,
    ``stale-timestamp``, ``bad-signature``.
    """
    secrets = get_webhook_secrets()
    if not secrets:
        return False, "no-secret-configured"
    if not signature_header:
        return False, "missing-signature"
    parsed = _parse_signature_header(signature_header)
    if parsed is None:
        return False, "malformed-signature"
    ts, presented = parsed
    current = now if now is not None else time.time()
    if abs(current - ts) > get_max_age_seconds():
        return False, "stale-timestamp"
    signed = f"{ts}.".encode("utf-8") + body
    for secret in secrets:
        expected = hmac.new(
            secret.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, presented):
            return True, "ok"
    return False, "bad-signature"


# ── Vendor schemes (GitHub / Linear) ─────────────────────────────────


def verify_vendor_hmac(
    body: bytes, presented_hex: str | None, secrets: list[str]
) -> tuple[bool, str]:
    """Verify a vendor HMAC (raw-body) signature. Fail-closed.

    ``presented_hex`` is the hex digest from the vendor header
    (``sha256=`` prefix already stripped). Returns ``(ok, reason)`` with
    reason in ``no-secret-configured`` / ``missing-signature`` /
    ``bad-signature`` / ``ok``. Comparison is constant-time across every
    configured secret so timing does not reveal which slot matched.
    """
    if not secrets:
        return False, "no-secret-configured"
    if not presented_hex:
        return False, "missing-signature"
    presented = presented_hex.strip().lower()
    match = False
    for secret in secrets:
        expected = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        # compare_digest across every secret: no early exit, no slot oracle.
        if hmac.compare_digest(expected, presented):
            match = True
    return (True, "ok") if match else (False, "bad-signature")


# ── Audit logging (best-effort, never raises) ────────────────────────


def record_ledger_auth_failure(
    source: str, reason: str, *, remote: str | None = None
) -> None:
    """Write a rejected webhook delivery to the hypervisor audit ledger.

    Best-effort by design — logging must never break the request path or
    leak secret material. The ledger payload carries only the source,
    the machine-readable reason code, and (optionally) the remote address.
    Callers pair this with the redacted event-bus notification.
    """
    payload = {"source": source, "reason": reason}
    if remote:
        payload["remote"] = remote
    try:
        from prismatic.hypervisor.ledger import get_hypervisor_ledger

        get_hypervisor_ledger().record_event(
            task_id=f"webhook-auth-{source}-{uuid.uuid4().hex[:12]}",
            producer="gateway",
            action="WEBHOOK_AUTH_FAILED",
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — logging is best-effort
        logger.warning("webhook auth-failure ledger write failed: %s", exc)
