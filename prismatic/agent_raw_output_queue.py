"""Persistence queue for raw agent output captured before normalization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prismatic.agent_packet_normalizer import (
    NormalizationResult,
    NormalizationStatus,
    normalize_agent_output,
    repair_preview as preview_raw_repair,
)


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def default_db_path() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_AGENT_RAW_OUTPUT_DB",
            default_state_dir() / "agent_raw_output_queue.sqlite3",
        )
    ).expanduser()


def max_raw_payload_bytes() -> int:
    return int(os.environ.get("PRISMATIC_AGENT_RAW_OUTPUT_MAX_BYTES", "131072"))


def retention_max_rows() -> int:
    return int(os.environ.get("PRISMATIC_AGENT_RAW_OUTPUT_RETENTION_ROWS", "500"))


_SECRET_LOCATOR_RE = re.compile(r"(?i)(token|key|secret|password)=([^&#]+)")
_URI_USERINFO_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/@]+@", re.IGNORECASE)
_QUEUE_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?:^|[\s{,;])"
    r"[\"']?"
    r"[A-Za-z0-9_.-]*"
    r"(?:password|passwd|secret|token|api_key|apikey|access_key|private_key|credential)"
    r"[A-Za-z0-9_.-]*"
    r"[\"']?"
    r"\s*(?:=|:)\s*"
    r"[\"']?"
    r"(?:\[REDACTED\]|[^\s,;&\"'}\]]+)"
)
_QUEUE_AUTHORIZATION_RE = re.compile(
    r"(?ix)"
    r"\bauthorization\b\s*(?:=|:)\s*"
    r"(?:\[REDACTED\]\s*)?"
    r"(?:bearer|basic)\s+[^\s,;&\"'}\]]+"
)
_QUEUE_URI_USERINFO_ANYWHERE_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@"
)
_QUEUE_PROVIDER_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|LINEAR_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\b\s*[:=]"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class RawAgentOutputRow:
    raw_output_id: str
    agent: str | None
    task_id: str | None
    source_event_id: str | None
    raw_text_or_artifact_path: str
    received_at: str
    normalization_status: str
    canonical_packet_id: str | None
    rejection_reason: str | None
    repair_hint: str | None
    rerun_allowed: bool
    rerun_requested: bool
    rerun_requested_at: str | None
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_output_id": self.raw_output_id,
            "agent": self.agent,
            "task_id": self.task_id,
            "source_event_id": self.source_event_id,
            "artifact_path": self.raw_text_or_artifact_path,
            "received_at": self.received_at,
            "normalization_status": self.normalization_status,
            "canonical_packet_id": self.canonical_packet_id,
            "rejection_reason": self.rejection_reason,
            "repair_hint": self.repair_hint,
            "rerun_allowed": self.rerun_allowed,
            "rerun_requested": self.rerun_requested,
            "rerun_requested_at": self.rerun_requested_at,
            "warnings": list(self.warnings),
        }


DELIVERY_STATUSES = frozenset(
    {"pending", "in_progress", "retry_wait", "succeeded", "terminal_failed"}
)
DELIVERY_ERROR_CODES = frozenset(
    {
        "agent_ineligible",
        "task_identity_invalid",
        "source_provenance_invalid",
        "source_artifact_invalid",
        "digest_mismatch",
        "raw_json_invalid",
        "raw_dialect_invalid",
        "packet_schema_invalid",
        "issue_identity_mismatch",
        "completed_work_storage_failed",
        "completed_work_result_invalid",
        "completed_work_marker_invalid",
        "retry_exhausted",
    }
)
TERMINAL_DISPOSITIONS = frozenset(
    {"ineligible", "malformed", "provenance_invalid", "retry_exhausted"}
)


@dataclass(frozen=True)
class RawOutputDelivery:
    raw_output_id: str
    status: str
    retry_count: int
    attempted_at: str | None
    succeeded_at: str | None
    failed_at: str | None
    next_attempt_at: str | None
    completed_work_id: str | None
    terminal_disposition: str | None
    last_error_code: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RawOutputDeliveryClaim:
    raw_output_id: str
    lease_token: str
    lease_owner: str
    lease_expires_at: str
    retry_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_output_id": self.raw_output_id,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "retry_count": self.retry_count,
        }


class RawAgentOutputStore:
    """SQLite-backed raw output queue.

    Stored rows are source-of-truth state. Repair preview and rerun request are
    explicit operator actions; neither mutates success state or dispatches agents.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_private(self.db_path.parent, 0o700)
        self._ensure_schema()
        _chmod_private(self.db_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_raw_output_queue (
                    raw_output_id TEXT PRIMARY KEY,
                    agent TEXT,
                    task_id TEXT,
                    source_event_id TEXT,
                    raw_text_or_artifact_path TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    normalization_status TEXT NOT NULL,
                    canonical_packet_id TEXT,
                    rejection_reason TEXT,
                    repair_hint TEXT,
                    rerun_allowed INTEGER NOT NULL,
                    rerun_requested INTEGER NOT NULL DEFAULT 0,
                    rerun_requested_at TEXT,
                    warnings_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_raw_output_received_at ON agent_raw_output_queue(received_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_raw_output_status ON agent_raw_output_queue(normalization_status)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_raw_output_delivery (
                    raw_output_id TEXT PRIMARY KEY REFERENCES agent_raw_output_queue(raw_output_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('pending','in_progress','retry_wait','succeeded','terminal_failed')),
                    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
                    attempted_at TEXT, succeeded_at TEXT, failed_at TEXT,
                    next_attempt_at TEXT, completed_work_id TEXT,
                    terminal_disposition TEXT, last_error_code TEXT,
                    lease_token TEXT, lease_owner TEXT, lease_expires_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_agent_raw_delivery_pending
                ON agent_raw_output_delivery(status, next_attempt_at, lease_expires_at, raw_output_id)"""
            )
            now = _iso(None)
            conn.execute(
                """INSERT OR IGNORE INTO agent_raw_output_delivery
                (raw_output_id, status, retry_count, created_at, updated_at)
                SELECT raw_output_id, 'pending', 0, received_at, ? FROM agent_raw_output_queue""",
                (now,),
            )
            conn.commit()

    def persist(
        self,
        *,
        raw_text: str,
        agent: str | None = None,
        task_id: str | None = None,
        source_event_id: str | None = None,
        raw_text_or_artifact_path: str | None = None,
        expected_agent: str | None = None,
    ) -> RawAgentOutputRow:
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("raw_text is required")
        received_at = datetime.now(timezone.utc).isoformat()
        storage_text, result = _safe_storage_text_and_result(
            raw_text, expected_agent=expected_agent or agent
        )
        raw_output_id = _raw_output_id(
            raw_text, agent=agent, task_id=task_id, source_event_id=source_event_id
        )
        locator = (
            _safe_artifact_locator(raw_text_or_artifact_path)
            if raw_text_or_artifact_path
            else f"inline:{raw_output_id}"
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_raw_output_queue (
                    raw_output_id, agent, task_id, source_event_id,
                    raw_text_or_artifact_path, raw_text, received_at,
                    normalization_status, canonical_packet_id, rejection_reason,
                    repair_hint, rerun_allowed, rerun_requested,
                    rerun_requested_at, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                ON CONFLICT(raw_output_id) DO NOTHING
                """,
                (
                    raw_output_id,
                    agent,
                    task_id,
                    source_event_id,
                    locator,
                    storage_text,
                    received_at,
                    result.status.value,
                    result.canonical_packet_id,
                    result.rejection_reason,
                    result.repair_hint,
                    1 if result.rerun_allowed else 0,
                    json.dumps(list(result.warnings), sort_keys=True),
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO agent_raw_output_delivery
                (raw_output_id, status, retry_count, created_at, updated_at)
                VALUES (?, 'pending', 0, ?, ?)""",
                (raw_output_id, received_at, received_at),
            )
            conn.commit()
        self._prune_retention()
        return self.get(raw_output_id)

    def list(self, *, limit: int = 50) -> list[RawAgentOutputRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_raw_output_queue ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, raw_output_id: str) -> RawAgentOutputRow:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_raw_output_queue WHERE raw_output_id = ?",
                (raw_output_id,),
            ).fetchone()
        if row is None:
            raise KeyError(raw_output_id)
        return self._row(row)

    def repair_preview(self, raw_output_id: str) -> dict[str, Any]:
        raw_text, row = self._raw_text_and_row(raw_output_id)
        preview = preview_raw_repair(raw_text, expected_agent=row.agent)
        preview["raw_output"] = row.as_dict()
        return preview

    def mark_rerun_requested(self, raw_output_id: str) -> RawAgentOutputRow:
        row = self.get(raw_output_id)
        if not row.rerun_allowed:
            raise ValueError("rerun is not allowed for this raw output classification")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_raw_output_queue SET rerun_requested = 1, rerun_requested_at = ? WHERE raw_output_id = ?",
                (now, raw_output_id),
            )
            conn.commit()
        return self.get(raw_output_id)

    def _prune_retention(self) -> None:
        limit = retention_max_rows()
        if limit <= 0:
            return
        with self._connect() as conn:
            conn.execute(
                """DELETE FROM agent_raw_output_queue WHERE raw_output_id IN (
                    SELECT q.raw_output_id FROM agent_raw_output_queue q
                    JOIN agent_raw_output_delivery d USING(raw_output_id)
                    WHERE d.status IN ('succeeded','terminal_failed')
                    ORDER BY q.received_at DESC, q.raw_output_id DESC
                    LIMIT -1 OFFSET ?
                )""",
                (limit,),
            )
            conn.commit()

    def get_delivery(self, raw_output_id: str) -> RawOutputDelivery:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_raw_output_delivery WHERE raw_output_id=?",
                (raw_output_id,),
            ).fetchone()
        if row is None:
            raise KeyError(raw_output_id)
        return self._delivery(row)

    def list_pending_deliveries(
        self, *, limit: int = 50, now: datetime | str | None = None
    ) -> list[RawOutputDelivery]:
        limit = _bounded_limit(limit)
        instant = _iso(now)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT d.* FROM agent_raw_output_delivery d
                JOIN agent_raw_output_queue q USING(raw_output_id)
                WHERE d.status='pending'
                   OR (d.status='retry_wait' AND d.next_attempt_at<=?)
                   OR (d.status='in_progress' AND d.lease_expires_at<=?)
                ORDER BY q.received_at ASC, q.raw_output_id ASC LIMIT ?""",
                (instant, instant, limit),
            ).fetchall()
        return [self._delivery(row) for row in rows]

    def claim_pending_deliveries(
        self,
        *,
        limit: int = 10,
        lease_owner: str,
        lease_seconds: int = 60,
        now: datetime | str | None = None,
    ) -> list[RawOutputDeliveryClaim]:
        limit = _bounded_limit(limit)
        owner, seconds = _validate_lease(lease_owner, lease_seconds)
        instant = _datetime(now)
        instant_s = instant.isoformat()
        expires = (instant + timedelta(seconds=seconds)).isoformat()
        claims: list[RawOutputDeliveryClaim] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT d.raw_output_id, d.retry_count FROM agent_raw_output_delivery d
                JOIN agent_raw_output_queue q USING(raw_output_id)
                WHERE d.status='pending'
                   OR (d.status='retry_wait' AND d.next_attempt_at<=?)
                   OR (d.status='in_progress' AND d.lease_expires_at<=?)
                ORDER BY q.received_at ASC, q.raw_output_id ASC LIMIT ?""",
                (instant_s, instant_s, limit),
            ).fetchall()
            for row in rows:
                token = secrets.token_urlsafe(32)
                changed = conn.execute(
                    """UPDATE agent_raw_output_delivery SET status='in_progress', attempted_at=?,
                    next_attempt_at=NULL, lease_token=?, lease_owner=?, lease_expires_at=?, updated_at=?
                    WHERE raw_output_id=? AND (status='pending'
                    OR (status='retry_wait' AND next_attempt_at<=?)
                    OR (status='in_progress' AND lease_expires_at<=?))""",
                    (
                        instant_s,
                        token,
                        owner,
                        expires,
                        instant_s,
                        row["raw_output_id"],
                        instant_s,
                        instant_s,
                    ),
                ).rowcount
                if changed == 1:
                    claims.append(
                        RawOutputDeliveryClaim(
                            row["raw_output_id"],
                            token,
                            owner,
                            expires,
                            row["retry_count"],
                        )
                    )
            conn.commit()
        return claims

    def claim_delivery(
        self,
        raw_output_id: str,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        now: datetime | str | None = None,
    ) -> RawOutputDeliveryClaim | None:
        owner, seconds = _validate_lease(lease_owner, lease_seconds)
        instant = _datetime(now)
        instant_s = instant.isoformat()
        expires = (instant + timedelta(seconds=seconds)).isoformat()
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT retry_count FROM agent_raw_output_delivery WHERE raw_output_id=?",
                (raw_output_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError(raw_output_id)
            changed = conn.execute(
                """UPDATE agent_raw_output_delivery SET status='in_progress', attempted_at=?,
                next_attempt_at=NULL, lease_token=?, lease_owner=?, lease_expires_at=?, updated_at=?
                WHERE raw_output_id=? AND (status='pending'
                OR (status='retry_wait' AND next_attempt_at<=?)
                OR (status='in_progress' AND lease_expires_at<=?))""",
                (
                    instant_s,
                    token,
                    owner,
                    expires,
                    instant_s,
                    raw_output_id,
                    instant_s,
                    instant_s,
                ),
            ).rowcount
            conn.commit()
        if changed != 1:
            return None
        return RawOutputDeliveryClaim(
            raw_output_id, token, owner, expires, row["retry_count"]
        )

    def mark_delivery_succeeded(
        self,
        claim: RawOutputDeliveryClaim,
        *,
        completed_work_id: str,
        now: datetime | str | None = None,
    ) -> bool:
        if type(completed_work_id) is not str or not completed_work_id.strip():
            raise ValueError("completed_work_id is required")
        instant = _iso(now)
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE agent_raw_output_delivery SET status='succeeded', succeeded_at=?,
                completed_work_id=?, terminal_disposition=NULL, last_error_code=NULL,
                lease_token=NULL, lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE raw_output_id=? AND status='in_progress' AND lease_token=?""",
                (
                    instant,
                    completed_work_id,
                    instant,
                    claim.raw_output_id,
                    claim.lease_token,
                ),
            ).rowcount
            conn.commit()
        self._prune_retention()
        return changed == 1

    def mark_delivery_failed(
        self,
        claim: RawOutputDeliveryClaim,
        *,
        error_code: str,
        terminal_disposition: str | None = None,
        retry_at: datetime | str | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        if error_code not in DELIVERY_ERROR_CODES:
            raise ValueError("unsafe delivery error code")
        if (
            terminal_disposition is not None
            and terminal_disposition not in TERMINAL_DISPOSITIONS
        ):
            raise ValueError("unsafe terminal disposition")
        if (terminal_disposition is None) == (retry_at is None):
            raise ValueError("exactly one terminal_disposition or retry_at is required")
        instant = _iso(now)
        retry_count = claim.retry_count + (1 if retry_at is not None else 0)
        status = (
            "retry_wait"
            if retry_at is not None and retry_count < 5
            else "terminal_failed"
        )
        disposition, code = terminal_disposition, error_code
        next_attempt = _iso(retry_at) if status == "retry_wait" else None
        if retry_at is not None and retry_count >= 5:
            disposition, code = "retry_exhausted", "retry_exhausted"
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE agent_raw_output_delivery SET status=?, retry_count=?, failed_at=?,
                next_attempt_at=?, terminal_disposition=?, last_error_code=?, lease_token=NULL,
                lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE raw_output_id=? AND status='in_progress' AND lease_token=?""",
                (
                    status,
                    retry_count,
                    instant,
                    next_attempt,
                    disposition,
                    code,
                    instant,
                    claim.raw_output_id,
                    claim.lease_token,
                ),
            ).rowcount
            conn.commit()
        if status == "terminal_failed":
            self._prune_retention()
        return changed == 1

    def _raw_text_for_delivery_claim(
        self, claim: RawOutputDeliveryClaim
    ) -> tuple[str, RawAgentOutputRow]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT q.* FROM agent_raw_output_queue q
                JOIN agent_raw_output_delivery d USING(raw_output_id)
                WHERE q.raw_output_id=? AND d.status='in_progress' AND d.lease_token=?""",
                (claim.raw_output_id, claim.lease_token),
            ).fetchone()
        if row is None:
            raise PermissionError("delivery claim is stale")
        return str(row["raw_text"]), self._row(row)

    @staticmethod
    def _delivery(row: sqlite3.Row) -> RawOutputDelivery:
        return RawOutputDelivery(
            **{name: row[name] for name in RawOutputDelivery.__dataclass_fields__}
        )

    def counts(self) -> dict[str, int]:
        rows = self.list(limit=500)
        counts = {
            "accepted": 0,
            "normalized": 0,
            "rejected": 0,
            "repairable": 0,
            "rerun_required": 0,
            "policy_violation": 0,
        }
        for row in rows:
            status = row.normalization_status
            if status == NormalizationStatus.ACCEPTED.value:
                counts["accepted"] += 1
            if status == NormalizationStatus.NORMALIZED_WITH_WARNINGS.value:
                counts["normalized"] += 1
            if status.startswith("rejected_"):
                counts["rejected"] += 1
            if status == NormalizationStatus.REJECTED_REPAIRABLE.value:
                counts["repairable"] += 1
            if status == NormalizationStatus.REJECTED_RERUN_REQUIRED.value:
                counts["rerun_required"] += 1
            if status == NormalizationStatus.REJECTED_POLICY_VIOLATION.value:
                counts["policy_violation"] += 1
        return counts

    def _raw_text_and_row(self, raw_output_id: str) -> tuple[str, RawAgentOutputRow]:
        with self._connect() as conn:
            db_row = conn.execute(
                "SELECT * FROM agent_raw_output_queue WHERE raw_output_id = ?",
                (raw_output_id,),
            ).fetchone()
        if db_row is None:
            raise KeyError(raw_output_id)
        return str(db_row["raw_text"]), self._row(db_row)

    @staticmethod
    def _row(row: sqlite3.Row) -> RawAgentOutputRow:
        warnings = tuple(json.loads(row["warnings_json"] or "[]"))
        return RawAgentOutputRow(
            raw_output_id=row["raw_output_id"],
            agent=row["agent"],
            task_id=row["task_id"],
            source_event_id=row["source_event_id"],
            raw_text_or_artifact_path=row["raw_text_or_artifact_path"],
            received_at=row["received_at"],
            normalization_status=row["normalization_status"],
            canonical_packet_id=row["canonical_packet_id"],
            rejection_reason=row["rejection_reason"],
            repair_hint=row["repair_hint"],
            rerun_allowed=bool(row["rerun_allowed"]),
            rerun_requested=bool(row["rerun_requested"]),
            rerun_requested_at=row["rerun_requested_at"],
            warnings=warnings,
        )


def persist_raw_output(**kwargs: Any) -> RawAgentOutputRow:
    return RawAgentOutputStore().persist(**kwargs)


def list_raw_outputs(
    *, limit: int = 50, db_path: str | Path | None = None
) -> list[RawAgentOutputRow]:
    return RawAgentOutputStore(db_path).list(limit=limit)


def get_raw_output(
    raw_output_id: str, *, db_path: str | Path | None = None
) -> RawAgentOutputRow:
    return RawAgentOutputStore(db_path).get(raw_output_id)


def repair_preview(
    raw_output_id: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    return RawAgentOutputStore(db_path).repair_preview(raw_output_id)


def mark_rerun_requested(
    raw_output_id: str, *, db_path: str | Path | None = None
) -> RawAgentOutputRow:
    return RawAgentOutputStore(db_path).mark_rerun_requested(raw_output_id)


def queue_counts(*, db_path: str | Path | None = None) -> dict[str, int]:
    return RawAgentOutputStore(db_path).counts()


def list_pending_deliveries(
    *,
    limit: int = 50,
    now: datetime | str | None = None,
    db_path: str | Path | None = None,
) -> list[RawOutputDelivery]:
    return RawAgentOutputStore(db_path).list_pending_deliveries(limit=limit, now=now)


def claim_pending_deliveries(
    *,
    limit: int = 10,
    lease_owner: str,
    lease_seconds: int = 60,
    now: datetime | str | None = None,
    db_path: str | Path | None = None,
) -> list[RawOutputDeliveryClaim]:
    return RawAgentOutputStore(db_path).claim_pending_deliveries(
        limit=limit, lease_owner=lease_owner, lease_seconds=lease_seconds, now=now
    )


def claim_delivery(
    raw_output_id: str,
    *,
    lease_owner: str,
    lease_seconds: int = 60,
    now: datetime | str | None = None,
    db_path: str | Path | None = None,
) -> RawOutputDeliveryClaim | None:
    return RawAgentOutputStore(db_path).claim_delivery(
        raw_output_id,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        now=now,
    )


def mark_delivery_succeeded(
    claim: RawOutputDeliveryClaim,
    *,
    completed_work_id: str,
    now: datetime | str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    return RawAgentOutputStore(db_path).mark_delivery_succeeded(
        claim, completed_work_id=completed_work_id, now=now
    )


def mark_delivery_failed(
    claim: RawOutputDeliveryClaim,
    *,
    error_code: str,
    terminal_disposition: str | None = None,
    retry_at: datetime | str | None = None,
    now: datetime | str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    return RawAgentOutputStore(db_path).mark_delivery_failed(
        claim,
        error_code=error_code,
        terminal_disposition=terminal_disposition,
        retry_at=retry_at,
        now=now,
    )


def _raw_output_id(
    raw_text: str,
    *,
    agent: str | None,
    task_id: str | None,
    source_event_id: str | None,
) -> str:
    if source_event_id:
        payload_obj = {
            "agent": agent,
            "task_id": task_id,
            "source_event_id": source_event_id,
        }
    else:
        payload_obj = {
            "agent": agent,
            "task_id": task_id,
            "source_event_id": source_event_id,
            "raw_text": raw_text,
        }
    payload = json.dumps(payload_obj, sort_keys=True, separators=(",", ":"))
    return "raw_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _bounded_limit(limit: int) -> int:
    if type(limit) is not int:
        raise TypeError("limit must be an exact integer")
    return max(1, min(100, limit))


def _datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if type(value) is not datetime:
        raise TypeError("time must be a datetime or ISO string")
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | str | None) -> str:
    return _datetime(value).isoformat()


def _validate_lease(owner: str, seconds: int) -> tuple[str, int]:
    if (
        type(owner) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", owner.strip()) is None
    ):
        raise ValueError("safe lease_owner is required")
    if type(seconds) is not int or seconds < 1 or seconds > 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    return owner.strip(), seconds


def _safe_storage_text_and_result(
    raw_text: str, *, expected_agent: str | None = None
) -> tuple[str, NormalizationResult]:
    raw_bytes = raw_text.encode("utf-8", errors="replace")
    if len(raw_bytes) > max_raw_payload_bytes():
        return (
            json.dumps(
                {
                    "raw_output_storage": "rejected",
                    "reason": "oversized_payload",
                    "payload_bytes": len(raw_bytes),
                    "max_bytes": max_raw_payload_bytes(),
                },
                sort_keys=True,
            ),
            NormalizationResult(
                status=NormalizationStatus.REJECTED_POLICY_VIOLATION,
                normalized_packet=None,
                canonical_packet_id=None,
                rejection_reason=f"raw output exceeds max bytes ({len(raw_bytes)} > {max_raw_payload_bytes()})",
                repair_hint="oversized_payload",
                rerun_allowed=False,
            ),
        )
    if _queue_contains_secret_like_content(raw_text):
        return (
            json.dumps(
                {
                    "raw_output_storage": "rejected",
                    "reason": "secret_like_content_detected",
                    "payload_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "payload_bytes": len(raw_bytes),
                },
                sort_keys=True,
            ),
            NormalizationResult(
                status=NormalizationStatus.REJECTED_POLICY_VIOLATION,
                normalized_packet=None,
                canonical_packet_id=None,
                rejection_reason="secret-like content detected in raw output",
                repair_hint="secret_like_content_detected",
                rerun_allowed=False,
            ),
        )
    result = normalize_agent_output(raw_text, expected_agent=expected_agent)
    if result.repair_hint == "secret_like_content_detected":
        return (
            json.dumps(
                {
                    "raw_output_storage": "rejected",
                    "reason": "secret_like_content_detected",
                    "payload_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "payload_bytes": len(raw_bytes),
                },
                sort_keys=True,
            ),
            result,
        )
    return raw_text, result


def _queue_contains_secret_like_content(value: str) -> bool:
    text = value or ""
    return (
        bool(_QUEUE_SECRET_ASSIGNMENT_RE.search(text))
        or bool(_QUEUE_AUTHORIZATION_RE.search(text))
        or bool(_QUEUE_URI_USERINFO_ANYWHERE_RE.search(text))
        or any(pattern.search(text) for pattern in _QUEUE_PROVIDER_SECRET_PATTERNS)
    )


def _safe_artifact_locator(locator: str | None) -> str:
    value = str(locator or "").strip()
    if not value:
        return "artifact:missing"
    value = _URI_USERINFO_RE.sub(r"\1[redacted]@", value)
    value = _SECRET_LOCATOR_RE.sub(lambda m: f"{m.group(1)}=[redacted]", value)
    if "://" in value:
        value = value.split("#", 1)[0]
    return value[:500]


def _chmod_private(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:
        raise PermissionError(
            f"failed to set private mode {oct(mode)} on {path}"
        ) from exc
    actual = path.stat().st_mode & 0o777
    if actual != mode:
        raise PermissionError(
            f"private mode verification failed for {path}: got {oct(actual)}, expected {oct(mode)}"
        )
