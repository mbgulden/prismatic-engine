"""Control receipts for portal control-plane actions (Portal Plan P0 #1).

Extends the verification-receipts pattern (immutable who/when/what records)
to portal control actions: deploy trigger / rollback today, config changes
and the rest in later phases. Receipts are append-only JSONL under the
deploy state dir so they sit next to alerts.log and the deploy records;
the future ``GET /api/audit`` unifies their shape.

A receipt is written for the *control decision* (who asked for what, when,
and the before/after state known at decision time), never for the
downstream outcome -- the outcome already lands in alerts.log, the deploy
manifest, and (soon) deploy.* events.

Never raises: a receipt failure is logged and swallowed, exactly like
``emit_deploy_alert``. ``log_path`` and the state-dir override are
injectable for tests.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default receipt file, next to alerts.log and the deploy records.
RECEIPTS_FILENAME = "deploy-control-receipts.jsonl"


def default_receipts_path() -> Path:
    """Resolve the receipt-log path.

    ``$PRISMATIC_CONTROL_RECEIPTS`` when set (tests), else
    ``$PRISMATIC_STATE_DIR/deploy-control-receipts.jsonl`` when set, else
    ``~/.prismatic/deploy-control-receipts.jsonl``.
    """
    override = os.environ.get("PRISMATIC_CONTROL_RECEIPTS")
    if override:
        return Path(override)
    state = os.environ.get("PRISMATIC_STATE_DIR")
    if state:
        return Path(state) / RECEIPTS_FILENAME
    return Path.home() / ".prismatic" / RECEIPTS_FILENAME


def build_receipt(
    action: str,
    actor: str,
    repository: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    result: str = "",
) -> dict[str, Any]:
    """Build one control receipt (who/when/what/before/after)."""
    return {
        "receipt_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
        "repository": repository,
        "summary": summary,
        "before": before or {},
        "after": after or {},
        "result": result,
    }


def write_control_receipt(
    action: str,
    actor: str,
    repository: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    result: str = "",
    *,
    log_path: Path | str | None = None,
) -> dict[str, Any]:
    """Append one receipt as JSONL. Never raises; returns the receipt."""
    receipt = build_receipt(
        action=action,
        actor=actor,
        repository=repository,
        summary=summary,
        before=before,
        after=after,
        result=result,
    )
    path = Path(log_path) if log_path is not None else default_receipts_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt) + "\n")
    except Exception as exc:  # receipting must never break the control path
        logger.error("deploy-receipts: failed to write %s: %s", path, exc)
    return receipt
