"""Persisted AGY completed-work ingestion.

This is the durable intake layer that stores AGY result packets, runs them
through the completed-work gate, and exposes accepted rows to the gateway and
operator scripts. It does not merge, dispatch, or mutate git state.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from prismatic.completed_work_gate import (
    AGY_COMPLETED_WORK_MARKER,
    CompletedWorkGateState,
    classify_completed_work,
    normalize_non_claims,
)

AGY_COMPLETED_WORK_INGESTION_MARKER = "AGY_COMPLETED_WORK_INGESTION_OK"
DEFAULT_DB_NAME = "agy_completed_work.db"


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def default_db_path() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_AGY_COMPLETED_WORK_DB",
            str(default_state_dir() / DEFAULT_DB_NAME),
        )
    ).expanduser()


@dataclass(frozen=True)
class CompletedWorkRow:
    id: str
    created_at: str
    updated_at: str
    agent: str | None
    source_branch: str | None
    source_path: str | None
    base_branch: str | None
    classification: str
    eligible_for_merge: bool
    requires_clean_rebuild: bool
    proof_result: str | None
    proof_marker: str | None
    gate_marker: str
    ingestion_marker: str
    packet: dict[str, Any]
    gate: dict[str, Any]
    non_claims: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "agent": self.agent,
            "source_branch": self.source_branch,
            "source_path": self.source_path,
            "base_branch": self.base_branch,
            "classification": self.classification,
            "eligible_for_merge": self.eligible_for_merge,
            "requires_clean_rebuild": self.requires_clean_rebuild,
            "proof_result": self.proof_result,
            "proof_marker": self.proof_marker,
            "gate_marker": self.gate_marker,
            "ingestion_marker": self.ingestion_marker,
            "packet": self.packet,
            "gate": self.gate,
            "non_claims": list(self.non_claims),
        }


class AgyCompletedWorkStore:
    """SQLite store for completed AGY packets and gate decisions."""

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
                CREATE TABLE IF NOT EXISTS agy_completed_work (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    agent TEXT,
                    source_branch TEXT,
                    source_path TEXT,
                    base_branch TEXT,
                    classification TEXT NOT NULL,
                    eligible_for_merge INTEGER NOT NULL,
                    requires_clean_rebuild INTEGER NOT NULL,
                    proof_result TEXT,
                    proof_marker TEXT,
                    gate_marker TEXT NOT NULL,
                    ingestion_marker TEXT NOT NULL,
                    packet_json TEXT NOT NULL,
                    gate_json TEXT NOT NULL,
                    non_claims_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agy_completed_work_created_at ON agy_completed_work(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agy_completed_work_classification ON agy_completed_work(classification)"
            )
            conn.commit()

    def ingest(
        self,
        packet: Mapping[str, Any],
        *,
        dirty_source: bool = False,
        source_is_stale: bool = False,
        conflicts: Sequence[str] | None = None,
    ) -> CompletedWorkRow:
        normalized_packet = normalize_agy_result_packet(packet)
        gate = classify_completed_work(
            normalized_packet,
            dirty_source=dirty_source,
            source_is_stale=source_is_stale,
            conflicts=conflicts or (),
        )
        raw_proof = normalized_packet.get("proof")
        proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
        non_claims = normalize_non_claims(proof)
        if not non_claims:
            # classify_completed_work will normally block this; keep persisted rows explicit.
            non_claims = tuple()
        now = datetime.now(timezone.utc).isoformat()
        row_id = completed_work_id(normalized_packet)
        gate_payload = gate.as_dict()
        values = (
            row_id,
            now,
            now,
            gate.agent,
            gate.source_branch,
            gate.source_path,
            gate.base_branch,
            gate.classification.value,
            1 if gate.eligible_for_merge else 0,
            1 if gate.requires_clean_rebuild else 0,
            gate.proof_result,
            gate.proof_marker,
            AGY_COMPLETED_WORK_MARKER,
            AGY_COMPLETED_WORK_INGESTION_MARKER,
            json.dumps(normalized_packet, sort_keys=True),
            json.dumps(gate_payload, sort_keys=True),
            json.dumps(list(non_claims), sort_keys=True),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agy_completed_work (
                    id, created_at, updated_at, agent, source_branch, source_path,
                    base_branch, classification, eligible_for_merge,
                    requires_clean_rebuild, proof_result, proof_marker,
                    gate_marker, ingestion_marker, packet_json, gate_json,
                    non_claims_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    agent = excluded.agent,
                    source_branch = excluded.source_branch,
                    source_path = excluded.source_path,
                    base_branch = excluded.base_branch,
                    classification = excluded.classification,
                    eligible_for_merge = excluded.eligible_for_merge,
                    requires_clean_rebuild = excluded.requires_clean_rebuild,
                    proof_result = excluded.proof_result,
                    proof_marker = excluded.proof_marker,
                    gate_marker = excluded.gate_marker,
                    ingestion_marker = excluded.ingestion_marker,
                    packet_json = excluded.packet_json,
                    gate_json = excluded.gate_json,
                    non_claims_json = excluded.non_claims_json
                """,
                values,
            )
            conn.commit()
        return self.get(row_id)

    def list(self, *, limit: int = 50) -> list[CompletedWorkRow]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agy_completed_work ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row_from_sqlite(row) for row in rows]

    def get(self, completed_work_id: str) -> CompletedWorkRow:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agy_completed_work WHERE id = ?",
                (completed_work_id,),
            ).fetchone()
        if row is None:
            raise KeyError(completed_work_id)
        return row_from_sqlite(row)


def completed_work_id(packet: Mapping[str, Any]) -> str:
    """Return a deterministic ID for a packet's source/proof identity."""

    source = {
        "agent": packet.get("agent"),
        "source_branch": packet.get("source_branch"),
        "source_path": packet.get("source_path"),
        "base_branch": packet.get("base_branch"),
        "changed_files": packet.get("changed_files"),
        "proof_marker": (packet.get("proof") or {}).get("marker") if isinstance(packet.get("proof"), Mapping) else None,
    }
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"agy-cw-{digest}"


def ingest_completed_work(
    packet: Mapping[str, Any],
    *,
    db_path: str | Path | None = None,
    dirty_source: bool = False,
    source_is_stale: bool = False,
    conflicts: Sequence[str] | None = None,
) -> CompletedWorkRow:
    return AgyCompletedWorkStore(db_path).ingest(
        packet,
        dirty_source=dirty_source,
        source_is_stale=source_is_stale,
        conflicts=conflicts,
    )


def list_completed_work(*, db_path: str | Path | None = None, limit: int = 50) -> list[CompletedWorkRow]:
    return AgyCompletedWorkStore(db_path).list(limit=limit)


def get_completed_work(completed_work_id: str, *, db_path: str | Path | None = None) -> CompletedWorkRow:
    return AgyCompletedWorkStore(db_path).get(completed_work_id)


def row_from_sqlite(row: sqlite3.Row) -> CompletedWorkRow:
    return CompletedWorkRow(
        id=str(row["id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        agent=row["agent"],
        source_branch=row["source_branch"],
        source_path=row["source_path"],
        base_branch=row["base_branch"],
        classification=str(row["classification"]),
        eligible_for_merge=bool(row["eligible_for_merge"]),
        requires_clean_rebuild=bool(row["requires_clean_rebuild"]),
        proof_result=row["proof_result"],
        proof_marker=row["proof_marker"],
        gate_marker=str(row["gate_marker"]),
        ingestion_marker=str(row["ingestion_marker"]),
        packet=json.loads(row["packet_json"]),
        gate=json.loads(row["gate_json"]),
        non_claims=tuple(json.loads(row["non_claims_json"])),
    )


def _json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    # Round-trip to ensure the persisted packet is JSON-safe and detached from callers.
    return json.loads(json.dumps(dict(value), sort_keys=True, default=str))
