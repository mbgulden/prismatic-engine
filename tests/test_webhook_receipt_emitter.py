"""WR-2: webhook receipt emitter tests.

Covers the finish-line item 3 contract: GitHub-UI merges (which bypass
merge_executor) get signed merge receipts via the gateway event bus.

Fixtures are shaped on the real PR #573 (dispatch-cap fix, squash-merged
via the GitHub UI at 2026-09-28T03:37:39Z, zero receipts on record):
    merge_sha  a0c7b1ef8efc82aa0e277ae7cd9408e2d8da0c06
    base_sha   bde04270cc50dbecf7b9aa127bcaf08bc64436a9
    actor      mbgulden
    message    "fix(dispatcher): port GRO-2979 dispatch-cap trio into local
                EventRouterDedup (#573)"

Signing uses a throwaway Ed25519 key generated per test (never the
production key): ``PRISMATIC_MERGE_RECEIPT_KEY_FILE`` points at the temp
PEM and ``PRISMATIC_MERGE_RECEIPTS`` points at a temp JSONL log, so no
test touches ``~/.prismatic``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from prismatic.gateway import event_bus
from prismatic.gateway import server
from prismatic.gateway.event_bus import EventBus, SwarmEvent
from prismatic.gateway.webhook_receipt_emitter import (
    emit_receipt_for_push,
    extract_merge_info,
    handle_github_webhook_event,
)
from prismatic.verification import merge_receipt as mr
from prismatic.verification.attestation import canonicalize_receipt

MERGE_SHA_573 = "a0c7b1ef8efc82aa0e277ae7cd9408e2d8da0c06"
BASE_SHA_573 = "bde04270cc50dbecf7b9aa127bcaf08bc64436a9"


def _push_573() -> dict:
    """Real-#573-shaped GitHub push payload (squash-merged via UI)."""
    return {
        "ref": "refs/heads/main",
        "before": BASE_SHA_573,
        "after": MERGE_SHA_573,
        "created": False,
        "deleted": False,
        "forced": False,
        "repository": {
            "id": 123456789,
            "full_name": "mbgulden/prismatic-engine",
            "default_branch": "main",
        },
        "pusher": {"name": "mbgulden", "email": "mbgulden@gmail.com"},
        "sender": {"login": "mbgulden", "id": 987654},
        "head_commit": {
            "id": MERGE_SHA_573,
            "message": (
                "fix(dispatcher): port GRO-2979 dispatch-cap trio into local "
                "EventRouterDedup (#573)"
            ),
            "timestamp": "2026-09-28T03:37:39Z",
            "author": {"name": "mbgulden"},
        },
        "commits": [],
    }


@pytest.fixture()
def receipt_env(tmp_path, monkeypatch):
    """Isolate receipt log + signing key: throwaway Ed25519 key, tmp log."""
    log = tmp_path / "merge-receipts.jsonl"
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(log))
    monkeypatch.delenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", raising=False)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file = tmp_path / "test-receipt-key.pem"
    key_file.write_bytes(pem)
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(key_file))
    monkeypatch.delenv("PRISMATIC_WEBHOOK_RECEIPTS_ENABLED", raising=False)
    return {"log": log, "private_key": key,
            "public_key": key.public_key()}


def _log_rows(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in
            log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_signed(row: dict, public_key) -> None:
    att = row.get("signature_or_attestation") or {}
    assert att.get("algorithm") == "ed25519", "receipt must be Ed25519-signed"
    assert att.get("value"), "signature value must be present"
    check = dict(row)
    check["signature_or_attestation"] = dict(att, value="")
    public_key.verify(
        base64.b64decode(att["value"]),
        canonicalize_receipt(check),
    )


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body,
                                hashlib.sha256).hexdigest()


# ── Unit: extraction ──────────────────────────────────────────────


def test_extract_merge_info_parses_573_push():
    info = extract_merge_info(_push_573())
    assert info is not None
    assert info["merge_sha"] == MERGE_SHA_573
    assert info["base_sha"] == BASE_SHA_573
    assert info["repository"] == "mbgulden/prismatic-engine"
    assert info["actor"] == "mbgulden"
    assert info["pr_number"] == "573"


def test_extract_merge_info_skips_non_default_branch():
    payload = _push_573()
    payload["ref"] = "refs/heads/muse/dispatch-cap-fix"
    assert extract_merge_info(payload) is None


def test_extract_merge_info_skips_branch_deletion():
    payload = _push_573()
    payload["after"] = "0" * 40
    payload["deleted"] = True
    assert extract_merge_info(payload) is None


def test_extract_merge_info_skips_tag_push():
    payload = _push_573()
    payload["ref"] = "refs/tags/v0.2.0"
    assert extract_merge_info(payload) is None


# ── Unit: emit (idempotency + signing) ────────────────────────────


def test_ui_merge_push_builds_signs_persists_receipt(receipt_env):
    receipt = emit_receipt_for_push(_push_573())
    assert receipt is not None
    assert receipt["merge_sha"] == MERGE_SHA_573
    assert receipt["marker"] == mr.MERGE_RECEIPT_MARKER
    assert receipt["task_id"] == "PR-573"
    assert receipt["authorization_id"] == "github-webhook"
    rows = _log_rows(receipt_env["log"])
    assert len(rows) == 1
    assert rows[0]["merge_sha"] == MERGE_SHA_573
    _assert_signed(rows[0], receipt_env["public_key"])


def test_push_for_already_receipted_sha_emits_no_duplicate(receipt_env):
    first = emit_receipt_for_push(_push_573())
    assert first is not None
    second = emit_receipt_for_push(_push_573())
    assert second is None, "duplicate push must not re-emit"
    rows = _log_rows(receipt_env["log"])
    assert len(rows) == 1
    assert rows[0]["receipt_id"] == first["receipt_id"]


def test_handler_ignores_non_push_events(receipt_env):
    asyncio.run(handle_github_webhook_event(
        SwarmEvent("pull_request", "github", {"action": "closed"})))
    asyncio.run(handle_github_webhook_event(
        SwarmEvent("push", "linear", _push_573())))
    assert _log_rows(receipt_env["log"]) == []


def test_emitter_kill_switch(receipt_env, monkeypatch):
    monkeypatch.setenv("PRISMATIC_WEBHOOK_RECEIPTS_ENABLED", "0")
    assert emit_receipt_for_push(_push_573()) is None
    assert _log_rows(receipt_env["log"]) == []


# ── Route-level: HMAC gate before receipt logic ───────────────────
#
# These run the real route + a real EventBus inside one asyncio loop via
# httpx.ASGITransport (TestClient runs the app in a separate portal loop,
# which would trip asyncio's loop-bound locks on the bus).


def _run(coro):
    return asyncio.run(coro)


async def _post_push_asgi(payload: dict, secret: str, *, valid: bool,
                          bus: EventBus):
    from httpx import ASGITransport, AsyncClient

    body = json.dumps(payload).encode()
    sig = _signature(secret, body) if valid else "sha256=" + "0" * 64
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport,
                           base_url="http://testserver") as client:
        return await client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "11111111-2222-3333-4444-555555555555",
                "X-Hub-Signature-256": sig,
            },
        )


async def _wired_bus() -> EventBus:
    bus = EventBus()
    await bus.subscribe(handle_github_webhook_event)
    return bus


def test_bad_hmac_rejected_before_any_receipt_logic(receipt_env, monkeypatch):
    """A forged delivery must 401 at the route; the bus (and the emitter)
    never see it, so no receipt can be written."""
    secret = "test-github-webhook-secret"
    monkeypatch.setattr(server, "get_github_secrets", lambda: [secret])

    async def scenario():
        bus = await _wired_bus()
        monkeypatch.setattr(event_bus, "get_event_bus", lambda: bus)
        return await _post_push_asgi(_push_573(), secret, valid=False, bus=bus)

    response = _run(scenario())
    assert response.status_code == 401
    assert response.json()["status"] == "auth-failed"
    assert _log_rows(receipt_env["log"]) == []


def test_valid_hmac_push_emits_receipt_end_to_end(receipt_env, monkeypatch):
    """Verified push delivery -> bus -> emitter -> signed persisted receipt."""
    secret = "test-github-webhook-secret"
    monkeypatch.setattr(server, "get_github_secrets", lambda: [secret])

    async def scenario():
        bus = await _wired_bus()
        monkeypatch.setattr(event_bus, "get_event_bus", lambda: bus)
        return await _post_push_asgi(_push_573(), secret, valid=True, bus=bus)

    response = _run(scenario())
    assert response.status_code == 200
    rows = _log_rows(receipt_env["log"])
    assert len(rows) == 1
    assert rows[0]["merge_sha"] == MERGE_SHA_573
    assert rows[0]["task_id"] == "PR-573"
    _assert_signed(rows[0], receipt_env["public_key"])


def test_redelivered_push_after_receipt_stays_single(receipt_env, monkeypatch):
    """GitHub redeliveries of the same push must not duplicate receipts."""
    secret = "test-github-webhook-secret"
    monkeypatch.setattr(server, "get_github_secrets", lambda: [secret])

    async def scenario():
        bus = await _wired_bus()
        monkeypatch.setattr(event_bus, "get_event_bus", lambda: bus)
        first = await _post_push_asgi(_push_573(), secret, valid=True, bus=bus)
        second = await _post_push_asgi(_push_573(), secret, valid=True, bus=bus)
        return first, second

    first, second = _run(scenario())
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(_log_rows(receipt_env["log"])) == 1
