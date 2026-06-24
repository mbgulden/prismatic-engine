"""Tests for Linear webhook label extraction + HMAC validation flow.

Covers the regression where:
1. `data.labels` is a Linear GraphQL connection object ({"nodes": [...]})
   not a flat list. The old code iterated expecting a list of dicts, which
   silently yielded no labels, so dispatch never fired even when HMAC passed.
2. HMAC validation flow against the dual-secret (PRIMARY + _NEXT) system.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make prismatic package importable
_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.gateway.server import linear_webhook  # noqa: E402


def _now_iso() -> str:
    """Current UTC time in Linear's ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _call_linear_webhook(body: bytes, signature_header: str | None) -> dict[str, Any]:
    """Call linear_webhook() and return the result as a dict.

    The handler returns either:
    - a plain dict on success (with status="queued"/"dispatched"/etc.)
    - a JSONResponse on rejection (with status_code 401 and body dict)
    """
    result = await linear_webhook(_make_request(body, signature_header))
    # Success path: result is already a dict
    if isinstance(result, dict):
        return result
    # Rejection path: result is a JSONResponse; decode its body
    if hasattr(result, "body") and isinstance(result.body, bytes):
        return json.loads(result.body)
    if hasattr(result, "body_iterator"):
        chunks: list[bytes] = []
        async for chunk in result.body_iterator:  # type: ignore[attr-defined]
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return json.loads(b"".join(chunks))
    # Last resort: try to convert
    return {"status": "unknown", "raw": str(result)}


def _make_request(body_bytes: bytes, signature_header: str | None = "sig"):
    """Build a minimal FastAPI/Starlette Request mock."""
    from starlette.requests import Request
    from starlette.datastructures import Headers

    headers_raw = []
    if signature_header:
        headers_raw.append((b"linear-signature", signature_header.encode()))
    headers = Headers(raw=headers_raw)
    # The Request needs a receive callable to read the body
    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/gateway/linear",
        "headers": headers.raw,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 9000),
    }
    return Request(scope, receive)


# ── Label extraction regression (was the silent break) ──────────────
def test_extract_labels_paginated_shape(monkeypatch):
    """Linear's standard webhook ships data.labels as a GraphQL connection."""
    # Disable HMAC by clearing the env
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {
            "identifier": "GRO-1234",
            "labels": {
                "nodes": [
                    {"name": "agent:fred"},
                    {"name": "type:infra-readonly"},
                ],
            },
        },
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=None))
    assert result["status"] in ("queued", "queued_no_label", "skipped_no_agent_label")
    assert result.get("identifier") == "GRO-1234"


def test_extract_labels_flat_shape(monkeypatch):
    """Older webhook format uses flat list of dicts — must still work."""
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {
            "identifier": "GRO-5678",
            "labels": [{"name": "agent:fred"}],  # flat list
        },
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=None))
    assert result.get("identifier") == "GRO-5678"


def test_extract_labels_empty_dict(monkeypatch):
    """data.labels = {} (connection with no nodes) → empty label list, no crash."""
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)
    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {
            "identifier": "GRO-9999",
            "labels": {},
        },
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=None))
    assert result.get("identifier") == "GRO-9999"


def test_extract_labels_missing(monkeypatch):
    """No labels key at all → empty list, no crash."""
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)
    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {"identifier": "GRO-0001"},
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=None))
    assert result.get("identifier") == "GRO-0001"


def test_extract_labels_null(monkeypatch):
    """data.labels = None → empty list, no crash."""
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)
    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {"identifier": "GRO-0002", "labels": None},
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=None))
    assert result.get("identifier") == "GRO-0002"


# ── HMAC validation flow ───────────────────────────────────────────
def test_hmac_primary_secret(monkeypatch):
    """HMAC signed with PRIMARY secret → 200."""
    secret = "test-primary-secret"
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", secret)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {
            "identifier": "GRO-HMAC-1",
            "labels": {"nodes": [{"name": "agent:fred"}]},
        },
    }).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    result = asyncio.run(_call_linear_webhook(body, signature_header=sig))
    assert result["status"] in ("queued", "dispatched")
    assert result.get("identifier") == "GRO-HMAC-1"


def test_hmac_next_secret_during_rotation(monkeypatch):
    """HMAC signed with _NEXT secret → 200 (zero-downtime rotation)."""
    primary = "test-primary"
    next_secret = "test-next-secret"
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", primary)
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", next_secret)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {
            "identifier": "GRO-HMAC-2",
            "labels": {"nodes": [{"name": "agent:fred"}]},
        },
    }).encode()
    sig = hmac.new(next_secret.encode(), body, hashlib.sha256).hexdigest()
    result = asyncio.run(_call_linear_webhook(body, signature_header=sig))
    assert result["status"] in ("queued", "dispatched")


def test_hmac_bad_signature_rejected(monkeypatch):
    """Wrong signature → 401 'bad signature'."""
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", "real-secret")
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {"identifier": "GRO-BAD-SIG", "labels": {"nodes": []}},
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header="0" * 64))
    assert result.get("status") == "rejected"
    assert "bad signature" in result.get("reason", "").lower()


def test_hmac_missing_signature_rejected(monkeypatch):
    """No signature header + secret set → 401 'missing Linear-Signature'."""
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", "real-secret")
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": _now_iso(),
        "data": {"identifier": "GRO-NO-SIG", "labels": {"nodes": []}},
    }).encode()
    result = asyncio.run(_call_linear_webhook(body, signature_header=""))
    assert result.get("status") == "rejected"
    assert "missing" in result.get("reason", "").lower()


def test_replay_protection_stale_event(monkeypatch):
    """Events older than REPLAY_WINDOW_SECONDS → 401 'event too old'."""
    secret = "test-replay-secret"
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", secret)
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    # Event from 1 hour ago — beyond the 5min replay window
    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": "2026-06-24T17:00:00Z",
        "data": {"identifier": "GRO-STALE", "labels": {"nodes": []}},
    }).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    result = asyncio.run(_call_linear_webhook(body, signature_header=sig))
    assert result.get("status") == "rejected"
    assert "too old" in result.get("reason", "").lower()