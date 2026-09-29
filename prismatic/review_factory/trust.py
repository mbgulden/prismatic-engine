"""RF-6: Trust Ledger for Earned Autonomy (Phase 1).

Append-only record of merge outcomes, rollbacks, pause resolutions, tier
changes, and brake pulls.  The ledger is DATA ONLY: nothing here changes
merge behavior.  :meth:`TrustLedger.check_graduation` returns a promotion
*proposal* (or ``None``); promotion itself stays Michael's explicit
decision (``record_tier_promoted`` with a named approver).

Storage
-------
- SQLite table ``trust_ledger_events`` inside the shared Review Factory
  DB (see :mod:`prismatic.review_factory.db`).  The DB is authoritative.
- JSONL mirror at ``<audit_dir>/trust-ledger.jsonl`` (one object per
  event, append-only).  If the mirror write fails, the DB row still
  commits and the returned event carries ``"mirror_ok": False``.

Graduation counting choice (frozen, documented for override)
------------------------------------------------------------
Clean streaks are a UNION streak across the tier's qualifying classes:
``sum(consecutive_clean_by_class[c] for c in tier_classes)``.  All
classes reset at the same rollback point, so the sum is the unbroken
streak of qualifying work, not per-class streaks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from prismatic.review_factory.db import ReviewFactoryDB

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

EVENT_TYPES = {
    "merge_completed",
    "merge_receipt_missing",
    "rollback_detected",
    "human_revert",
    "pause_resolved",
    "tier_promoted",
    "tier_revoked",
    "brake_pulled",
    # T1 single arming ceremony: the signed t1_armed record is the only
    # authority that arms T1; t1_disarmed is the emergency stop.
    "t1_armed",
    "t1_disarmed",
}

CHANGE_CLASSES = {
    "docs",
    "chore",
    "dep_bump",
    "agent_standard",
    "sensitive",
    "production",
}

TIERS = (0, 1, 2, 3)

MERGED_BY_VALUES = {"auto", "michael"}

# next_tier -> (clean-streak threshold, qualifying change classes)
GRADUATION_THRESHOLDS: dict[int, tuple[int, tuple[str, ...]]] = {
    1: (20, ("docs", "chore", "dep_bump")),
    2: (30, ("agent_standard",)),
    3: (50, ("agent_standard",)),
}

# next_tier -> minimum Jev pause precision required
PAUSE_PRECISION_FLOORS: dict[int, float] = {
    2: 0.80,
    3: 0.85,
}

PAUSE_VOLUME_FLOOR_T2 = 10  # pauses (trailing 30d) required for T1 -> T2
AGREED_PAUSE_FLOOR_T3 = 2  # agreed pauses (trailing 30d) required for T2 -> T3

PROMOTION_FREEZE_DAYS = 7  # tier revocation freezes promotion for 7 days

TRUST_LEDGER_MIRROR_NAME = "trust-ledger.jsonl"


def _default_audit_dir() -> Path:
    """Resolve the audit dir: ``$PRISMATIC_AUDIT_DIR`` else ``~/.prismatic/audit``."""
    env = os.environ.get("PRISMATIC_AUDIT_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".prismatic" / "audit"


def _coerce_ts(value: Any) -> str:
    """Coerce a ``now_fn`` result to a UTC ISO-8601 string."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a stored ISO timestamp to an aware UTC datetime, else None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Trust ledger
# ─────────────────────────────────────────────────────────────────────


class TrustLedger:
    """Append-only trust ledger backing earned-autonomy tier decisions.

    The ledger never changes tiers by itself: ``check_graduation`` only
    proposes.  Tiers move via explicit ``record_tier_promoted`` (Michael)
    or mechanical ``revoke`` (rollback/human-revert/brake).
    """

    def __init__(
        self,
        db: Optional[ReviewFactoryDB] = None,
        db_path: Optional[Path] = None,
        audit_dir: Optional[Path] = None,
        now_fn: Optional[Callable[[], Any]] = None,
    ):
        self._db = db if db is not None else ReviewFactoryDB(db_path=db_path)
        self._db.ensure_tables()
        self._audit_dir = (
            Path(audit_dir) if audit_dir is not None else _default_audit_dir()
        )
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._mirror_path = self._audit_dir / TRUST_LEDGER_MIRROR_NAME
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc).isoformat())

    # ── core write path ──────────────────────────────────────────────

    def record_event(
        self,
        event_type: str,
        *,
        artifact_id: Optional[str] = None,
        change_class: Optional[str] = None,
        tier_at_event: Optional[int] = None,
        merged_by: Optional[str] = None,
        deterministic_verdict: Optional[str] = None,
        judgment: Optional[Mapping[str, Any]] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Append one event to the ledger.

        Validates ``event_type``/``change_class``/``tier_at_event``/
        ``merged_by``; raises :class:`ValueError` on invalid input (no row
        written, no mirror line appended).
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown trust-ledger event_type: {event_type!r}")
        if change_class is not None and change_class not in CHANGE_CLASSES:
            raise ValueError(f"unknown change_class: {change_class!r}")
        if tier_at_event is not None and tier_at_event not in TIERS:
            raise ValueError(
                f"tier_at_event must be one of {TIERS}, got {tier_at_event!r}"
            )
        if merged_by is not None and merged_by not in MERGED_BY_VALUES:
            raise ValueError(
                f"merged_by must be one of {sorted(MERGED_BY_VALUES)}, got {merged_by!r}"
            )
        if judgment is not None and not isinstance(judgment, Mapping):
            raise ValueError("judgment must be a mapping or None")

        judgment_json = json.dumps(dict(judgment)) if judgment is not None else None
        event: dict[str, Any] = {
            "event_id": uuid.uuid4().hex,
            "ts": _coerce_ts(self._now_fn()),
            "event_type": event_type,
            "artifact_id": artifact_id,
            "change_class": change_class,
            "tier_at_event": tier_at_event,
            "merged_by": merged_by,
            "deterministic_verdict": deterministic_verdict,
            "judgment": dict(judgment) if judgment is not None else None,
            "notes": notes or "",
        }

        with self._db.transaction() as cur:
            cur.execute(
                """INSERT INTO trust_ledger_events (
                    event_id, ts, event_type, artifact_id, change_class,
                    tier_at_event, merged_by, deterministic_verdict,
                    judgment, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"],
                    event["ts"],
                    event["event_type"],
                    event["artifact_id"],
                    event["change_class"],
                    event["tier_at_event"],
                    event["merged_by"],
                    event["deterministic_verdict"],
                    judgment_json,
                    event["notes"],
                ),
            )

        event["mirror_ok"] = self._append_mirror(event)
        return event

    def _append_mirror(self, event: dict[str, Any]) -> bool:
        """Append the event to the JSONL mirror.  Returns False on failure."""
        try:
            with open(self._mirror_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            return True
        except OSError as exc:
            logger.warning("trust-ledger mirror write failed: %s", exc)
            return False

    # ── convenience recorders ────────────────────────────────────────

    def _find_outcome_event(
        self, event_type: str, artifact_id: str
    ) -> "Optional[dict[str, Any]]":
        """Return the existing event for (event_type, artifact_id), if any.

        Outcome events are idempotent on the logical artifact: recording the
        same merge/rollback twice (retried callers, ad-hoc runs against the
        production DB, a future bus subscriber) must not double-count in
        the graduation streaks.
        """
        cur = self._db.conn.execute(
            "SELECT event_id, ts, event_type, artifact_id, change_class,"
            " tier_at_event, merged_by, deterministic_verdict, judgment, notes"
            " FROM trust_ledger_events"
            " WHERE event_type = ? AND artifact_id = ?"
            " ORDER BY ts ASC, rowid ASC LIMIT 1",
            (event_type, artifact_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "event_id": row["event_id"],
            "ts": row["ts"],
            "event_type": row["event_type"],
            "artifact_id": row["artifact_id"],
            "change_class": row["change_class"],
            "tier_at_event": row["tier_at_event"],
            "merged_by": row["merged_by"],
            "deterministic_verdict": row["deterministic_verdict"],
            "judgment": json.loads(row["judgment"]) if row["judgment"] else None,
            "notes": row["notes"],
            "duplicate_skipped": True,
        }

    def record_merge_outcome(
        self,
        *,
        artifact_id: str,
        change_class: str,
        merged_by: str,
        deterministic_verdict: str = "CLEAN",
        judgment: Optional[Mapping[str, Any]] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a completed merge.

        Idempotent on ``artifact_id``: a second record for the same merge
        returns the existing event instead of double-counting.
        """
        existing = self._find_outcome_event("merge_completed", artifact_id)
        if existing is not None:
            logger.info(
                "trust ledger: merge_completed already recorded for %s; "
                "skipping duplicate",
                artifact_id,
            )
            return existing
        return self.record_event(
            "merge_completed",
            artifact_id=artifact_id,
            change_class=change_class,
            merged_by=merged_by,
            deterministic_verdict=deterministic_verdict,
            judgment=judgment,
            notes=notes,
        )

    def record_merge_receipt_missing(
        self,
        *,
        artifact_id: str,
        change_class: str,
        merge_sha: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a merge commit that is live without a signed receipt.

        Used when receipt emission failed and the CAS rollback was refused
        or failed, so the commit stayed on the target. The attempt is
        visible in the ledger but never counts as a clean merge
        (``merge_outcomes()`` only counts ``merge_completed``). Idempotent
        on ``artifact_id`` like the other outcome recorders.
        """
        existing = self._find_outcome_event("merge_receipt_missing", artifact_id)
        if existing is not None:
            logger.info(
                "trust ledger: merge_receipt_missing already recorded for %s; "
                "skipping duplicate",
                artifact_id,
            )
            return existing
        note = f"merge_sha={merge_sha}" if merge_sha else ""
        if notes:
            note = f"{note} {notes}".strip() if note else notes
        return self.record_event(
            "merge_receipt_missing",
            artifact_id=artifact_id,
            change_class=change_class,
            merged_by="auto",
            notes=note,
        )

    def record_rollback(
        self,
        *,
        artifact_id: str,
        change_class: str,
        auto_merged: bool = True,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a rollback.  If the rolled-back work was auto-merged,
        revoke a tier immediately (mechanical — no proposal, no waiting)."""
        existing = self._find_outcome_event("rollback_detected", artifact_id)
        if existing is not None:
            logger.info(
                "trust ledger: rollback_detected already recorded for %s; "
                "skipping duplicate",
                artifact_id,
            )
            return existing
        event = self.record_event(
            "rollback_detected",
            artifact_id=artifact_id,
            change_class=change_class,
            notes=notes,
        )
        if auto_merged:
            self.revoke(reason=notes or "rollback of auto-merged work")
        return event

    def record_human_revert(
        self,
        *,
        artifact_id: str,
        change_class: str,
        auto_merged: bool = True,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a human revert.  Same mechanical revocation as a rollback."""
        existing = self._find_outcome_event("human_revert", artifact_id)
        if existing is not None:
            logger.info(
                "trust ledger: human_revert already recorded for %s; "
                "skipping duplicate",
                artifact_id,
            )
            return existing
        event = self.record_event(
            "human_revert",
            artifact_id=artifact_id,
            change_class=change_class,
            notes=notes,
        )
        if auto_merged:
            self.revoke(reason=notes or "human revert of auto-merged work")
        return event

    def record_pause_resolution(
        self,
        *,
        artifact_id: str,
        agreed: bool,
        judge: str = "jev",
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a Jev judge pause resolution (agreed = Michael agreed)."""
        return self.record_event(
            "pause_resolved",
            artifact_id=artifact_id,
            judgment={"judge": judge, "decision": "PAUSE", "agreed": bool(agreed)},
            notes=notes,
        )

    def record_tier_promoted(
        self,
        *,
        to_tier: int,
        approver: str,
        rationale: str = "",
        override_freeze: bool = False,
    ) -> dict[str, Any]:
        """Record an explicit tier promotion.  Approvals need a named
        human — empty approver raises ValueError.

        A revocation-triggered promotion freeze blocks promotion for
        ``PROMOTION_FREEZE_DAYS``; promoting during a freeze raises
        ValueError unless ``override_freeze=True`` is passed explicitly
        (the override is recorded in the event notes).
        """
        if not approver or not str(approver).strip():
            raise ValueError("tier promotion requires a named approver")
        if to_tier not in TIERS:
            raise ValueError(f"to_tier must be one of {TIERS}, got {to_tier!r}")
        freeze_until = self.tier_status().get("promotion_freeze_until")
        if freeze_until is not None and not override_freeze:
            raise ValueError(
                "tier promotion blocked by active promotion freeze until "
                f"{freeze_until}; pass override_freeze=True for an explicit "
                "override"
            )
        notes = rationale
        if freeze_until is not None and override_freeze:
            notes = f"{rationale} [freeze overridden; was frozen until {freeze_until}]".strip()
        return self.record_event(
            "tier_promoted",
            tier_at_event=to_tier,
            judgment={
                "to_tier": to_tier,
                "approver": approver,
                "override_freeze": bool(override_freeze),
            },
            notes=notes,
        )

    def record_brake_pulled(self, *, notes: str = "") -> dict[str, Any]:
        """Record a manual brake pull (promotion freeze + drop to T0 is
        the operator's call; use revoke(to_tier=0) for that)."""
        return self.record_event("brake_pulled", notes=notes)

    def record_t1_armed(self, *, document: dict[str, Any]) -> dict[str, Any]:
        """Record the signed T1 arming document (the arming ceremony).

        The document MUST carry a complete ``signature_or_attestation``
        envelope — unsigned documents are refused with ValueError, so a
        forged raw ``record_event("t1_armed", ...)`` can never arm T1.
        """
        from prismatic.review_factory import arming

        ok, reason = arming._validate_schema(document)
        if not ok:
            raise ValueError(f"refusing to record unsigned t1_armed document: {reason}")
        return self.record_event(
            "t1_armed",
            tier_at_event=1,
            judgment=document,
            notes=f"T1 armed by {document.get('approver')}",
        )

    def record_t1_disarmed(
        self, *, approver: str, rationale: str = ""
    ) -> dict[str, Any]:
        """Record the T1 disarm (emergency stop). Latest of t1_armed /
        t1_disarmed wins in ``arming.t1_arming_status()``."""
        if not approver or not str(approver).strip():
            raise ValueError("t1 disarm requires a named approver")
        return self.record_event(
            "t1_disarmed",
            tier_at_event=0,
            judgment={"approver": approver, "rationale": rationale},
            notes=f"T1 disarmed by {approver}: {rationale}".strip(),
        )

    def revoke(self, *, reason: str, to_tier: Optional[int] = None) -> dict[str, Any]:
        """Mechanically drop the tier: one step down by default, or to an
        explicit target.  Never raises the tier."""
        current = self.tier_status()["current_tier"]
        target = to_tier if to_tier is not None else max(TIERS[0], current - 1)
        if target not in TIERS:
            raise ValueError(f"to_tier must be one of {TIERS}, got {target!r}")
        return self.record_event(
            "tier_revoked",
            tier_at_event=target,
            judgment={"from_tier": current, "to_tier": target},
            notes=reason,
        )

    # ── reads ────────────────────────────────────────────────────────

    def events(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """All ledger events, oldest first (ordered by ts, then rowid)."""
        sql = (
            "SELECT rowid, event_id, ts, event_type, artifact_id, change_class,"
            " tier_at_event, merged_by, deterministic_verdict, judgment, notes"
            " FROM trust_ledger_events ORDER BY ts ASC, rowid ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cur = self._db.conn.execute(sql, params)
        out = []
        for row in cur.fetchall():
            out.append(
                {
                    "event_id": row["event_id"],
                    "ts": row["ts"],
                    "event_type": row["event_type"],
                    "artifact_id": row["artifact_id"],
                    "change_class": row["change_class"],
                    "tier_at_event": row["tier_at_event"],
                    "merged_by": row["merged_by"],
                    "deterministic_verdict": row["deterministic_verdict"],
                    "judgment": json.loads(row["judgment"])
                    if row["judgment"]
                    else None,
                    "notes": row["notes"],
                }
            )
        return out

    def tier_status(self) -> dict[str, Any]:
        """Fold the ledger into the current trust picture.

        - ``current_tier``: last tier_promoted/tier_revoked wins, else 0.
        - ``consecutive_clean_by_class``: merge_completed count per class
          since the last rollback_detected/human_revert of ANY class.
        - ``promotion_freeze_until``: ISO ts (or None) — max(ts + 7d) over
          tier_revoked events, only when still in the future.
        """
        now = _parse_ts(_coerce_ts(self._now_fn())) or datetime.now(timezone.utc)
        window_start = now - timedelta(days=30)

        current_tier = TIERS[0]
        streaks = {c: 0 for c in CHANGE_CLASSES}
        rollback_count_30d = 0
        revocation_count_30d = 0
        pauses_trailing_30 = 0
        agreed_pauses_trailing_30 = 0
        recent_pauses: list[bool] = []  # most recent 30 pause_resolved, oldest first
        freeze_until: Optional[datetime] = None

        for ev in self.events():
            etype = ev["event_type"]
            ts = _parse_ts(ev["ts"])
            in_window = ts is not None and ts >= window_start
            judgment = ev["judgment"] or {}

            if etype == "tier_promoted":
                to_tier = judgment.get("to_tier", ev["tier_at_event"])
                if to_tier in TIERS:
                    current_tier = to_tier
            elif etype == "tier_revoked":
                to_tier = judgment.get("to_tier", ev["tier_at_event"])
                if to_tier in TIERS:
                    current_tier = to_tier
                if ts is not None:
                    candidate = ts + timedelta(days=PROMOTION_FREEZE_DAYS)
                    if freeze_until is None or candidate > freeze_until:
                        freeze_until = candidate
                if in_window:
                    revocation_count_30d += 1
            elif etype == "merge_completed":
                cls = ev["change_class"]
                if cls in streaks:
                    streaks[cls] += 1
            elif etype in ("rollback_detected", "human_revert"):
                streaks = {c: 0 for c in CHANGE_CLASSES}
                if in_window:
                    rollback_count_30d += 1
            elif etype == "pause_resolved":
                agreed = bool(judgment.get("agreed"))
                recent_pauses.append(agreed)
                if in_window:
                    pauses_trailing_30 += 1
                    if agreed:
                        agreed_pauses_trailing_30 += 1

        trailing_30_pauses = recent_pauses[-30:]
        pause_precision: Optional[float] = None
        if trailing_30_pauses:
            pause_precision = sum(trailing_30_pauses) / len(trailing_30_pauses)

        return {
            "current_tier": current_tier,
            "consecutive_clean_by_class": streaks,
            "rollback_count_30d": rollback_count_30d,
            "pause_precision": pause_precision,
            "pauses_trailing_30": pauses_trailing_30,
            "agreed_pauses_trailing_30": agreed_pauses_trailing_30,
            "revocation_count_30d": revocation_count_30d,
            "promotion_freeze_until": (
                freeze_until.isoformat()
                if freeze_until is not None and freeze_until > now
                else None
            ),
        }

    def check_graduation(self) -> Optional[dict[str, Any]]:
        """Propose a tier promotion if the ledger meets the thresholds.

        NEVER changes the tier — returns a data-only proposal dict (or
        None when no promotion is warranted).  ``requires_michael`` is
        always True: the promotion itself is his call.
        """
        status = self.tier_status()
        if status["promotion_freeze_until"] is not None:
            return None
        current = status["current_tier"]
        next_tier = current + 1
        if next_tier > TIERS[-1] or next_tier not in GRADUATION_THRESHOLDS:
            return None

        threshold, classes = GRADUATION_THRESHOLDS[next_tier]
        clean = sum(status["consecutive_clean_by_class"].get(c, 0) for c in classes)
        if clean < threshold or status["rollback_count_30d"] != 0:
            return None

        pause_floor = PAUSE_PRECISION_FLOORS.get(next_tier)
        precision = status["pause_precision"]
        if next_tier == 2:
            if (
                pause_floor is None
                or precision is None
                or precision < pause_floor
                or status["pauses_trailing_30"] < PAUSE_VOLUME_FLOOR_T2
            ):
                return None
        if next_tier == 3:
            if (
                pause_floor is None
                or precision is None
                or precision < pause_floor
                or status["agreed_pauses_trailing_30"] < AGREED_PAUSE_FLOOR_T3
            ):
                return None

        evidence = {
            "threshold": threshold,
            "classes": list(classes),
            "clean_streak": clean,
            "rollback_count_30d": status["rollback_count_30d"],
            "pause_precision": precision,
            "pauses_trailing_30": status["pauses_trailing_30"],
            "agreed_pauses_trailing_30": status["agreed_pauses_trailing_30"],
            "revocation_count_30d": status["revocation_count_30d"],
        }
        rationale = (
            f"T{current}->T{next_tier}: {clean} clean {'/'.join(classes)} merges"
            f" (threshold {threshold}), {status['rollback_count_30d']} rollbacks"
            f" in 30d"
        )
        if pause_floor is not None:
            rationale += f", pause precision {precision:.2f} (floor {pause_floor:.2f})"
        return {
            "proposal": "tier_promotion",
            "from_tier": current,
            "to_tier": next_tier,
            "evidence": evidence,
            "requires_michael": True,
            "rationale": rationale,
        }

    # ── event-bus wiring ─────────────────────────────────────────────

    def subscribe(self, bus: Any = None) -> bool:
        """Subscribe to ``review_factory.merge_completed`` on the Gateway
        event bus and record each completed merge.

        The live merge event is sparse (review_job_id, task_id, merge_sha,
        actor, target) — required ``record_merge_outcome`` fields the
        event does not carry (change_class, merged_by) are taken from the
        payload only when present; events missing them are skipped rather
        than recorded with invented values.

        Returns True on success, False when the gateway is unavailable
        (no-op — nothing is wired).
        """
        try:
            from prismatic.gateway.event_bus import get_event_bus
        except ImportError:
            return False
        try:
            if bus is None:
                bus = get_event_bus()

            async def _handler(event: Any) -> None:
                if getattr(event, "type", None) != "review_factory.merge_completed":
                    return
                payload = getattr(event, "payload", None) or {}
                change_class = payload.get("change_class")
                merged_by = payload.get("merged_by")
                if not change_class or not merged_by:
                    logger.debug(
                        "trust-ledger: skipping merge_completed without "
                        "change_class/merged_by (review_job_id=%r)",
                        payload.get("review_job_id"),
                    )
                    return
                try:
                    self.record_merge_outcome(
                        artifact_id=payload.get("review_job_id")
                        or payload.get("merge_sha", ""),
                        change_class=change_class,
                        merged_by=merged_by,
                        deterministic_verdict=payload.get(
                            "deterministic_verdict", "CLEAN"
                        ),
                        judgment=payload.get("judgment"),
                        notes=payload.get("notes", ""),
                    )
                except ValueError as exc:
                    logger.debug("trust-ledger: invalid merge event: %s", exc)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bus.subscribe(_handler))
            except RuntimeError:
                asyncio.run(bus.subscribe(_handler))
            return True
        except Exception as exc:  # gateway present but unusable — stay a no-op
            logger.debug("trust-ledger subscribe failed: %s", exc)
            return False
