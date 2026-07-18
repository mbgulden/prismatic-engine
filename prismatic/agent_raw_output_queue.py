"""Persistence queue for raw agent output captured before normalization."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prismatic.agent_packet_normalizer import (
    RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
    NormalizationStatus,
    normalize_agent_output,
    repair_preview as preview_raw_repair,
)


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def default_db_path() -> Path:
    return Path(os.environ.get("PRISMATIC_AGENT_RAW_OUTPUT_DB", default_state_dir() / "agent_raw_output_queue.sqlite3")).expanduser()


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
            "raw_text_or_artifact_path": self.raw_text_or_artifact_path,
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


class RawAgentOutputStore:
    """SQLite-backed raw output queue.

    Stored rows are source-of-truth state. Repair preview and rerun request are
    explicit operator actions; neither mutates success state or dispatches agents.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_raw_output_received_at ON agent_raw_output_queue(received_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_raw_output_status ON agent_raw_output_queue(normalization_status)")
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
        result = normalize_agent_output(raw_text, expected_agent=expected_agent or agent)
        raw_output_id = _raw_output_id(raw_text, agent=agent, task_id=task_id, source_event_id=source_event_id)
        locator = raw_text_or_artifact_path or f"inline:{raw_output_id}"
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
                ON CONFLICT(raw_output_id) DO UPDATE SET
                    agent = excluded.agent,
                    task_id = excluded.task_id,
                    source_event_id = excluded.source_event_id,
                    raw_text_or_artifact_path = excluded.raw_text_or_artifact_path,
                    raw_text = excluded.raw_text,
                    normalization_status = excluded.normalization_status,
                    canonical_packet_id = excluded.canonical_packet_id,
                    rejection_reason = excluded.rejection_reason,
                    repair_hint = excluded.repair_hint,
                    rerun_allowed = excluded.rerun_allowed,
                    warnings_json = excluded.warnings_json
                """,
                (
                    raw_output_id,
                    agent,
                    task_id,
                    source_event_id,
                    locator,
                    raw_text,
                    received_at,
                    result.status.value,
                    result.canonical_packet_id,
                    result.rejection_reason,
                    result.repair_hint,
                    1 if result.rerun_allowed else 0,
                    json.dumps(list(result.warnings), sort_keys=True),
                ),
            )
            conn.commit()
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
            row = conn.execute("SELECT * FROM agent_raw_output_queue WHERE raw_output_id = ?", (raw_output_id,)).fetchone()
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
            db_row = conn.execute("SELECT * FROM agent_raw_output_queue WHERE raw_output_id = ?", (raw_output_id,)).fetchone()
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


def list_raw_outputs(*, limit: int = 50, db_path: str | Path | None = None) -> list[RawAgentOutputRow]:
    return RawAgentOutputStore(db_path).list(limit=limit)


def get_raw_output(raw_output_id: str, *, db_path: str | Path | None = None) -> RawAgentOutputRow:
    return RawAgentOutputStore(db_path).get(raw_output_id)


def repair_preview(raw_output_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return RawAgentOutputStore(db_path).repair_preview(raw_output_id)


def mark_rerun_requested(raw_output_id: str, *, db_path: str | Path | None = None) -> RawAgentOutputRow:
    return RawAgentOutputStore(db_path).mark_rerun_requested(raw_output_id)


def queue_counts(*, db_path: str | Path | None = None) -> dict[str, int]:
    return RawAgentOutputStore(db_path).counts()


def _raw_output_id(raw_text: str, *, agent: str | None, task_id: str | None, source_event_id: str | None) -> str:
    payload = json.dumps(
        {"agent": agent, "task_id": task_id, "source_event_id": source_event_id, "raw_text": raw_text},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "raw_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
