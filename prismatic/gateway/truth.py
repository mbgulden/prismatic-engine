"""Read-only truth panels for the gateway dashboard.

``get_truth_snapshot()`` consolidates six "what is actually live" probes
into one JSON document served at ``GET /api/gateway/truth``:

1. ``trust_ledger``  — folded trust-ledger state (``TrustLedger.tier_status()``).
2. ``merge_receipts`` — merge-receipt coverage from the receipts log.
3. ``t1``            — read-only ``t1_arming_status()`` consult (armed/inert).
4. ``deployed_sha``  — the running release SHA (``get_running_sha()``).
5. ``consumer_lag``  — task-admission outbox backlog counts (cursor/lag).
6. ``run_receipts``  — signed run-receipt coverage (proof-of-done).

Read-only by construction: every sub-probe only reads; none writes state,
arms anything, or triggers deploys. The T1 probe consults ONLY the
read-only ``t1_arming_status()`` — it must never import or invoke any
arming path (``cmd_arm``, ``sign_arming_document``, ``build_arming_document``).

Each sub-probe is wrapped so a single failure degrades that panel to
``{"ok": False, "error": ...}`` instead of 500ing the whole document
(fail-open display, fail-closed machinery).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _fail(reason: str, error: str | None = None) -> dict[str, Any]:
    panel: dict[str, Any] = {"ok": False, "reason": reason}
    if error:
        panel["error"] = error
    return panel


def _probe_trust_ledger() -> dict[str, Any]:
    """Folded trust-ledger state. Read-only; no data -> fail-open."""
    from prismatic.review_factory.db import default_db_path
    from prismatic.review_factory.trust import TrustLedger

    db_path = default_db_path()
    if not db_path.exists():
        return _fail("no data")
    ledger = TrustLedger(db_path=db_path)
    status = ledger.tier_status()
    recent = ledger.events(limit=5)
    return {
        "ok": True,
        "current_tier": status.get("current_tier"),
        "consecutive_clean_by_class": status.get("consecutive_clean_by_class"),
        "rollback_count_30d": status.get("rollback_count_30d"),
        "revocation_count_30d": status.get("revocation_count_30d"),
        "pauses_trailing_30": status.get("pauses_trailing_30"),
        "agreed_pauses_trailing_30": status.get("agreed_pauses_trailing_30"),
        "promotion_freeze_until": status.get("promotion_freeze_until"),
        "event_count": len(ledger.events()),
        "recent_events": [
            {
                "event_type": ev.get("event_type"),
                "ts": ev.get("ts"),
                "tier_at_event": ev.get("tier_at_event"),
                "notes": ev.get("notes"),
            }
            for ev in recent
        ],
    }


def _probe_merge_receipts() -> dict[str, Any]:
    """Merge-receipt coverage. Read-only; missing log -> fail-open."""
    from prismatic.verification.merge_receipt import (
        default_merge_receipts_path,
        find_merge_receipts,
    )

    log_path = default_merge_receipts_path()
    if not log_path.exists():
        return _fail("no data")
    receipts = find_merge_receipts(log_path=log_path, limit=100)
    if not receipts:
        return _fail("no data")
    signed = sum(1 for r in receipts if r.get("signature_or_attestation"))
    latest = receipts[-1]
    return {
        "ok": True,
        "receipt_count": len(receipts),
        "signed_count": signed,
        "unsigned_count": len(receipts) - signed,
        "log_path": str(log_path),
        "latest": {
            "merge_sha": latest.get("merge_sha"),
            "candidate_sha": latest.get("candidate_sha"),
            "emitted_at": latest.get("emitted_at"),
            "actor": latest.get("actor"),
            "change_class": latest.get("change_class"),
            "signed": bool(latest.get("signature_or_attestation")),
        },
    }


def _probe_t1() -> dict[str, Any]:
    """Read-only T1 arming consult. NEVER invokes any arming path.

    Display shape mirrors ``t1_arming_status()``: ``armed`` + ``reason``.
    Probe-level ``ok`` says the consult ran; ``armed`` says what it found.
    """
    # Intentionally narrow import: the read-only consult only. Nothing in
    # this module may reach any arming write path.
    from prismatic.review_factory.arming import t1_arming_status

    status = t1_arming_status()
    return {
        "ok": True,
        "armed": bool(status.get("armed")),
        "reason": status.get("reason"),
        "record": status.get("record"),
        "read_only": True,
    }


def _probe_deployed_sha() -> dict[str, Any]:
    """The running release SHA. Read-only; undetermined -> fail-open."""
    from prismatic.gateway.release_info import get_running_sha

    sha = get_running_sha()
    if not sha:
        return _fail("no data")
    return {"ok": True, "sha": sha}


def _probe_consumer_lag() -> dict[str, Any]:
    """Task-admission outbox backlog (cursor/lag). Read-only SQL.

    Counts rows by status and reports the oldest pending row age, so the
    panel shows whether the consumer is keeping up with admissions.
    """
    from prismatic.task_admission import _resolve_db_path

    db_path = Path(_resolve_db_path())
    if not db_path.exists():
        return _fail("no data")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "task_admission_outbox" not in tables:
            return _fail("no data")
        rows = conn.execute(
            "SELECT status, COUNT(*), MIN(created_at)"
            " FROM task_admission_outbox GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return _fail("no data")
    by_status = {row[0]: {"count": row[1], "oldest": row[2]} for row in rows}
    return {
        "ok": True,
        "db_path": str(db_path),
        "by_status": by_status,
        "pending": by_status.get("pending", {}).get("count", 0),
        "claimed": by_status.get("claimed", {}).get("count", 0),
        "launched": by_status.get("launched", {}).get("count", 0),
        "failed": by_status.get("failed", {}).get("count", 0),
        "oldest_pending": by_status.get("pending", {}).get("oldest"),
    }


def _probe_run_receipts() -> dict[str, Any]:
    """Signed run-receipt coverage (proof-of-done). Read-only; no log -> fail-open."""
    from prismatic.verification.run_receipt import (
        default_run_receipts_path,
        find_run_receipts,
    )

    log_path = default_run_receipts_path()
    if not log_path.exists():
        return _fail("no data")
    receipts = find_run_receipts(log_path=log_path, limit=100)
    if not receipts:
        return _fail("no data")
    signed = sum(
        1 for r in receipts if (r.get("signature_or_attestation") or {}).get("value")
    )
    done = sum(1 for r in receipts if r.get("done_gate_result") == "done")
    latest = receipts[-1]
    return {
        "ok": True,
        "receipt_count": len(receipts),
        "signed_count": signed,
        "unsigned_count": len(receipts) - signed,
        "done_count": done,
        "not_done_count": len(receipts) - done,
        "log_path": str(log_path),
        "latest": {
            "receipt_id": latest.get("receipt_id"),
            "run_id": latest.get("run_id"),
            "agent_name": latest.get("agent_name"),
            "verification_status": latest.get("verification_status"),
            "done_gate_result": latest.get("done_gate_result"),
            "emitted_at": latest.get("emitted_at"),
            "signed": bool((latest.get("signature_or_attestation") or {}).get("value")),
        },
    }


_PROBES: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("trust_ledger", _probe_trust_ledger),
    ("merge_receipts", _probe_merge_receipts),
    ("t1", _probe_t1),
    ("deployed_sha", _probe_deployed_sha),
    ("consumer_lag", _probe_consumer_lag),
    ("run_receipts", _probe_run_receipts),
)


def get_truth_snapshot() -> dict[str, Any]:
    """Build the six truth panels, fail-open per panel.

    A throwing probe degrades its panel to ``{"ok": False, "error": ...}``;
    the document always completes (never 500s).
    """
    panels: dict[str, dict[str, Any]] = {}
    for name, probe in _PROBES:
        try:
            panels[name] = probe()
        except Exception as exc:  # fail-open display
            logger.warning("truth panel %s failed: %s", name, exc)
            panels[name] = {
                "ok": False,
                "reason": "probe_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {"ok": True, "panels": panels}
