"""WR-2: webhook receipt emitter — signed merge receipts for UI-merged PRs.

GitHub-UI merges (and any push-merged commits) bypass
:class:`~prismatic.review_factory.merge_executor.MergeExecutor`, so no merge
receipt is emitted for them (finish-line item 3: the receipt contract must
cover every merge path, not just the executor path).

This module subscribes to the gateway event bus. The
``POST /api/gateway/github`` route HMAC-verifies each delivery and publishes
verified events to the bus; on ``push`` events to a repository's default
branch this subscriber builds, Ed25519-signs, and persists a merge receipt
using the existing :mod:`prismatic.verification.merge_receipt`
build/sign/persist functions — nothing is reimplemented here.

Idempotent on merge SHA: the receipts log is checked with
:func:`~prismatic.verification.merge_receipt.find_merge_receipts` before
emitting, so a redelivered or replayed push never writes a duplicate.

Signing key: :func:`~prismatic.verification.merge_receipt.sign_merge_receipt`
reads ``PRISMATIC_MERGE_RECEIPT_SIGNING_KEY`` (PEM text) or
``PRISMATIC_MERGE_RECEIPT_KEY_FILE`` (default
``~/.prismatic/keys/merge-receipt-ed25519.pem``). Tests must use a throwaway
generated key via those env overrides — never the production key.

Kill switch: set ``PRISMATIC_WEBHOOK_RECEIPTS_ENABLED=0`` to disable
emission without unsubscribing (receipts are skipped, deliveries still 200).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

ZERO_SHA40 = "0" * 40
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
# GitHub squash-merge commit messages end with " (#<pr>)", e.g.
# "fix(dispatcher): port GRO-2979 dispatch-cap trio into local
#  EventRouterDedup (#573)".
PR_NUMBER_RE = re.compile(r"\(#(\d+)\)\s*$")

ENABLED_ENV = "PRISMATIC_WEBHOOK_RECEIPTS_ENABLED"


def emitter_enabled() -> bool:
    """Kill switch for WR-2 emission (default on)."""
    return os.environ.get(ENABLED_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _is_sha40(value: Any) -> bool:
    return isinstance(value, str) and SHA40_RE.fullmatch(value) is not None


def extract_merge_info(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract merge fields from a GitHub ``push`` event payload.

    Returns ``None`` when the push is not a default-branch forward merge:
    tag pushes, non-default branches, branch deletions (zero ``after`` SHA),
    or malformed SHAs are all skipped.
    """
    if not isinstance(payload, Mapping):
        return None
    ref = payload.get("ref", "")
    after = payload.get("after", "")
    before = payload.get("before", "")
    if not _is_sha40(after) or after == ZERO_SHA40:
        return None
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        return None
    branch = ref[len("refs/heads/"):]
    repository = payload.get("repository") or {}
    default_branch = repository.get("default_branch") or "main"
    if branch != default_branch:
        return None
    full_name = repository.get("full_name") or ""
    pusher = payload.get("pusher") or {}
    sender = payload.get("sender") or {}
    actor = pusher.get("name") or sender.get("login") or "unknown"
    head_commit = payload.get("head_commit") or {}
    message = head_commit.get("message") or ""
    pr_match = PR_NUMBER_RE.search(message)
    pr_number = pr_match.group(1) if pr_match else ""
    return {
        "repository": full_name,
        "branch": branch,
        "merge_sha": after,
        "base_sha": before if _is_sha40(before) else "",
        "actor": actor,
        "pr_number": pr_number,
        "head_commit_message": message[:200],
        "pushed_at": head_commit.get("timestamp") or "",
    }


def emit_receipt_for_push(
    payload: Mapping[str, Any], *, log_path: Any | None = None
) -> dict[str, Any] | None:
    """Build, sign, and persist a merge receipt for a push payload.

    Returns the receipt dict on emission, ``None`` when skipped (disabled,
    not a default-branch merge) or when a receipt already exists for the
    merge SHA (idempotent). Never raises — receipt failure must not break
    the webhook/bus path.
    """
    try:
        if not emitter_enabled():
            logger.info("webhook receipt emitter disabled; skipping")
            return None
        info = extract_merge_info(payload)
        if info is None:
            return None
        from prismatic.verification.merge_receipt import (
            build_merge_receipt,
            find_merge_receipts,
            persist_merge_receipt,
            sign_merge_receipt,
        )

        merge_sha = info["merge_sha"]
        existing = find_merge_receipts(merge_sha=merge_sha, log_path=log_path)
        if existing:
            logger.info(
                "merge receipt already exists for %s; skipping duplicate",
                merge_sha,
            )
            return None
        task_id = f"PR-{info['pr_number']}" if info["pr_number"] else ""
        receipt = build_merge_receipt(
            repository=info["repository"],
            candidate_sha="",
            candidate_tree="",
            base_sha=info["base_sha"],
            merge_sha=merge_sha,
            actor=info["actor"],
            authorization_id="github-webhook",
            job_id=f"github-push:{merge_sha[:12]}",
            task_id=task_id,
            policy_version="wr-2",
            change_class="github-push",
        )
        receipt.setdefault("explicit_non_claims", []).extend(
            [
                "emitted from GitHub push webhook (WR-2); merge did not go "
                "through merge_executor",
                "candidate SHA/tree unavailable in push payload; base_sha is "
                "the pre-push branch head",
            ]
        )
        sign_merge_receipt(receipt)
        receipt_id = persist_merge_receipt(receipt, log_path=log_path)
        if receipt_id is None:
            logger.warning(
                "persist_merge_receipt failed for merge %s", merge_sha
            )
            return None
        logger.info(
            "emitted webhook merge receipt %s for %s", receipt_id, merge_sha
        )
        return receipt
    except Exception:
        logger.warning("emit_receipt_for_push failed", exc_info=True)
        return None


async def handle_github_webhook_event(event: Any) -> None:
    """Event-bus handler: emit merge receipts for verified GitHub pushes."""
    try:
        if getattr(event, "source", "") != "github":
            return
        if getattr(event, "type", "") != "push":
            return
        emit_receipt_for_push(getattr(event, "payload", None) or {})
    except Exception:
        logger.warning("handle_github_webhook_event failed", exc_info=True)


async def subscribe_webhook_receipt_emitter(bus: Any = None) -> None:
    """Subscribe the WR-2 handler to a bus (defaults to the gateway bus)."""
    if bus is None:
        from prismatic.gateway.event_bus import get_event_bus

        bus = get_event_bus()
    await bus.subscribe(handle_github_webhook_event)


__all__ = [
    "emitter_enabled",
    "extract_merge_info",
    "emit_receipt_for_push",
    "handle_github_webhook_event",
    "subscribe_webhook_receipt_emitter",
]
