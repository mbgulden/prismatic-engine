"""
Webhook receiver router for the Prismatic Gateway.

Handles incoming webhooks from:
  - GitHub (PR opened, synchronized, review submitted)
  - Linear (issue status changes)

All endpoints verify HMAC signatures before processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Request, Response

logger = logging.getLogger("prismatic.gateway.webhook_router")

router = APIRouter()

# ── Signature Verification ──────────────────────────────


def _verify_github_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify GitHub HMAC-SHA256 signature."""
    if not signature_header:
        return False
    expected_prefix = "sha256="
    if not signature_header.startswith(expected_prefix):
        return False
    received_sig = signature_header[len(expected_prefix):]
    computed_sig = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_sig, received_sig)


def _verify_linear_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify Linear HMAC-SHA256 signature (same scheme as GitHub)."""
    if not signature_header:
        return False
    computed_sig = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_sig, signature_header)


# ── GitHub Webhook ──────────────────────────────────────


@router.post("/github")
async def github_webhook(request: Request) -> Response:
    """Receive GitHub webhook events (PR opened, synchronized, review submitted)."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if not _verify_github_signature(body, signature, secret):
        logger.warning("GitHub webhook: invalid signature")
        return Response(status_code=401, content=json.dumps({"error": "invalid signature"}), media_type="application/json")

    event = request.headers.get("x-github-event", "unknown")
    payload = json.loads(body)
    action = payload.get("action", "unknown")

    logger.info("GitHub webhook received: event=%s action=%s", event, action)

    # Route PR events
    if event == "pull_request":
        pr_number = payload.get("pull_request", {}).get("number")
        pr_action = payload.get("action")
        logger.info("PR #%s %s — queued for dispatcher processing", pr_number, pr_action)
        # TODO: route to dispatcher queue (GRO-1573 integration)

    elif event == "pull_request_review":
        pr_number = payload.get("pull_request", {}).get("number")
        review_state = payload.get("review", {}).get("state")
        logger.info("PR #%s review: %s — queued for dispatcher", pr_number, review_state)
        # TODO: route to dispatcher

    return Response(status_code=202, content=json.dumps({"status": "accepted", "event": event, "action": action}), media_type="application/json")


# ── Linear Webhook ──────────────────────────────────────


@router.post("/linear")
async def linear_webhook(request: Request) -> Response:
    """Receive Linear webhook events (issue status changes, comments)."""
    secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
    body = await request.body()
    signature = request.headers.get("linear-signature", "")

    if not _verify_linear_signature(body, signature, secret):
        logger.warning("Linear webhook: invalid signature")
        return Response(status_code=401, content=json.dumps({"error": "invalid signature"}), media_type="application/json")

    payload = json.loads(body)
    action = payload.get("action", "unknown")
    data = payload.get("data", {})

    logger.info("Linear webhook received: action=%s type=%s", action, payload.get("type", "unknown"))

    # Route issue updates
    if payload.get("type") == "Issue":
        issue_id = data.get("id")
        state_name = data.get("state", {}).get("name") if data.get("state") else None
        logger.info("Issue %s → state: %s — queued for dispatcher", issue_id, state_name)
        # TODO: route to dispatcher queue (GRO-1573 integration)

    return Response(status_code=202, content=json.dumps({"status": "accepted", "action": action}), media_type="application/json")
