"""One-shot, crash-recoverable consumer for dashboard task admissions.

The HTTP admission request remains launch-free. This module is invoked separately,
claims at most one durable outbox row, revalidates the complete immutable tuple,
enforces a singleton writer lease, and calls an idempotent launcher with the stable
event id as its launch key.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import selectors
import signal
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prismatic.task_admission import (
    TaskAdmissionError,
    _assert_database_family_private,
    _canonical_json,
    _default_git_runner,
    _hash_task_file_secure,
    _load_policy,
    _prepare_database_file,
    _read_policy_bytes,
    _reject_duplicate_pairs,
    _validate_schema,
)

_TOPIC = "dashboard.task.admitted.v1"
_MAX_LAUNCHER_OUTPUT = 64 * 1024
MAX_TERMINAL_RECONCILIATION_BODY_BYTES = 8 * 1024
_TERMINAL_RECONCILIATION_KEYS = {
    "task_id",
    "expected_event_id",
    "expected_claim_id",
    "expected_current_state",
    "evidence_type",
    "candidate_sha",
    "candidate_tree",
    "merge_sha",
    "merge_tree",
    "evidence_sha256",
    "reason_code",
}
_TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_EVENT_ID_RE = re.compile(r"^task-admission:[0-9a-f]{64}$")
_CLAIM_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECONCILIATION_EVIDENCE_TYPE = "reviewed_merge_completion"
_RECONCILIATION_REASON = "completed_via_reviewed_bounded_repair"
_RECONCILIATION_ERROR_CODE = "externally_completed_reviewed_repair"


class ConsumerError(RuntimeError):
    """Base fail-closed consumer error."""


class AdmissionRevalidationError(ConsumerError):
    """The durable admission no longer matches its exact tuple."""


class LauncherError(ConsumerError):
    """The idempotent launcher did not return an acceptable receipt."""


class LeaseHeartbeatError(ConsumerError):
    """The singleton writer lease could not be proven during launch."""


@dataclass(frozen=True)
class Claim:
    event_id: str
    task_id: str
    claim_id: str
    attempt: int
    recovered: bool


@dataclass(frozen=True)
class LaunchRequest:
    event_id: str
    task_id: str
    producer_identity: str
    base_commit: str
    base_tree: str
    task_file_sha256: str
    writer_cap: int
    worktree: str
    task_file: str
    actor: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "idempotency_key": self.event_id,
            "task_id": self.task_id,
            "producer_identity": self.producer_identity,
            "base_commit": self.base_commit,
            "base_tree": self.base_tree,
            "task_file_sha256": self.task_file_sha256,
            "writer_cap": self.writer_cap,
            "worktree": self.worktree,
            "task_file": self.task_file,
            "actor": self.actor,
        }


@dataclass(frozen=True)
class ConsumerResult:
    event_id: str
    task_id: str
    status: str
    claim_id: str
    attempt: int
    launch_id: str | None = None


@dataclass(frozen=True)
class TerminalReconciliationResult:
    replayed: bool
    record: dict[str, Any]


Launcher = Callable[[LaunchRequest], Mapping[str, Any]]


def parse_terminal_reconciliation_json(raw: bytes) -> dict[str, str]:
    """Parse one bounded terminal-reconciliation request fail closed."""

    if not raw or len(raw) > MAX_TERMINAL_RECONCILIATION_BODY_BYTES:
        raise TaskAdmissionError("invalid_body_size", 413 if raw else 422)
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except TaskAdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskAdmissionError("invalid_json", 422) from exc
    if not isinstance(payload, dict) or set(payload) != _TERMINAL_RECONCILIATION_KEYS:
        raise TaskAdmissionError("terminal_reconciliation_shape_invalid", 422)
    if any(not isinstance(value, str) for value in payload.values()):
        raise TaskAdmissionError("terminal_reconciliation_shape_invalid", 422)
    if _TASK_ID_RE.fullmatch(payload["task_id"]) is None:
        raise TaskAdmissionError("terminal_reconciliation_task_id_invalid", 422)
    if _EVENT_ID_RE.fullmatch(payload["expected_event_id"]) is None:
        raise TaskAdmissionError("terminal_reconciliation_event_id_invalid", 422)
    if _CLAIM_ID_RE.fullmatch(payload["expected_claim_id"]) is None:
        raise TaskAdmissionError("terminal_reconciliation_claim_id_invalid", 422)
    if payload["expected_current_state"] != "retryable_failed":
        raise TaskAdmissionError("terminal_reconciliation_state_invalid", 422)
    if payload["evidence_type"] != _RECONCILIATION_EVIDENCE_TYPE:
        raise TaskAdmissionError("terminal_reconciliation_evidence_type_invalid", 422)
    if payload["reason_code"] != _RECONCILIATION_REASON:
        raise TaskAdmissionError("terminal_reconciliation_reason_invalid", 422)
    for key in ("candidate_sha", "candidate_tree", "merge_sha", "merge_tree"):
        if _GIT_OBJECT_RE.fullmatch(payload[key]) is None:
            raise TaskAdmissionError("terminal_reconciliation_git_object_invalid", 422)
    if _SHA256_RE.fullmatch(payload["evidence_sha256"]) is None:
        raise TaskAdmissionError("terminal_reconciliation_evidence_digest_invalid", 422)
    return dict(payload)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class TaskAdmissionConsumer:
    """Claim and process no more than one dedicated admission outbox row."""

    def __init__(
        self,
        *,
        db_path: Path,
        policy_path: Path,
        identity: str,
        lease_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
        git_runner: Callable[[Path, str], str] = _default_git_runner,
    ) -> None:
        if not identity.strip() or len(identity) > 200:
            raise ValueError("consumer identity required")
        if lease_seconds < 30 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        self.db_path = _prepare_database_file(db_path)
        self.policy_path = policy_path
        self.identity = identity.strip()
        self.lease_seconds = lease_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.git_runner = git_runner
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_admission_consumer_claims (
                    event_id TEXT PRIMARY KEY REFERENCES task_admission_outbox(event_id),
                    task_id TEXT NOT NULL UNIQUE,
                    claim_id TEXT NOT NULL UNIQUE,
                    claimed_by TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1),
                    state TEXT NOT NULL CHECK(state IN ('claimed','validated','launch_started','completed','retryable_failed','terminal_failed')),
                    last_error_code TEXT,
                    launch_receipt_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_admission_writer_lease (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    event_id TEXT NOT NULL UNIQUE,
                    claim_id TEXT NOT NULL UNIQUE,
                    lease_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_admission_lifecycle (
                    lifecycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK(attempt >= 1),
                    consumer_identity TEXT NOT NULL,
                    detail_sha256 TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS task_admission_lifecycle_no_update
                BEFORE UPDATE ON task_admission_lifecycle BEGIN SELECT RAISE(ABORT, 'lifecycle_immutable'); END;
                CREATE TRIGGER IF NOT EXISTS task_admission_lifecycle_no_delete
                BEFORE DELETE ON task_admission_lifecycle BEGIN SELECT RAISE(ABORT, 'lifecycle_immutable'); END;
                """
            )
            connection.commit()
            _assert_database_family_private(self.db_path)
        finally:
            connection.close()

    def _append_lifecycle(
        self,
        connection: sqlite3.Connection,
        claim: Claim,
        event: str,
        *,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        digest = None
        if detail is not None:
            digest = hashlib.sha256(_canonical_json(detail).encode()).hexdigest()
        connection.execute(
            "INSERT INTO task_admission_lifecycle "
            "(event_id,task_id,claim_id,event,attempt,consumer_identity,detail_sha256,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                claim.event_id,
                claim.task_id,
                claim.claim_id,
                event,
                claim.attempt,
                self.identity,
                digest,
                _timestamp(self.now()),
            ),
        )

    def terminal_reconcile(
        self, payload: Mapping[str, Any]
    ) -> TerminalReconciliationResult:
        """Terminalize one failed admission launch without claiming producer success."""

        try:
            evidence = parse_terminal_reconciliation_json(
                _canonical_json(dict(payload)).encode()
            )
        except TaskAdmissionError:
            raise
        except (TypeError, ValueError) as exc:
            raise TaskAdmissionError(
                "terminal_reconciliation_shape_invalid", 422
            ) from exc
        detail_sha256 = hashlib.sha256(_canonical_json(evidence).encode()).hexdigest()
        task_id = evidence["task_id"]
        event_id = evidence["expected_event_id"]
        claim_id = evidence["expected_claim_id"]
        now = _utc(self.now())
        now_text = _timestamp(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            admission = connection.execute(
                "SELECT task_id,event_id FROM task_admissions WHERE task_id=?",
                (task_id,),
            ).fetchone()
            outbox = connection.execute(
                "SELECT task_id,status FROM task_admission_outbox WHERE event_id=?",
                (event_id,),
            ).fetchone()
            claim = connection.execute(
                "SELECT task_id,claim_id,state,attempt_count,lease_expires_at,"
                "last_error_code,launch_receipt_json FROM task_admission_consumer_claims "
                "WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if admission is None or outbox is None or claim is None:
                raise TaskAdmissionError("terminal_reconciliation_not_found", 404)
            if (
                admission["event_id"] != event_id
                or outbox["task_id"] != task_id
                or claim["task_id"] != task_id
                or claim["claim_id"] != claim_id
            ):
                raise TaskAdmissionError("terminal_reconciliation_tuple_mismatch", 409)

            prior = connection.execute(
                "SELECT detail_sha256 FROM task_admission_lifecycle "
                "WHERE event_id=? AND event='terminal_reconciled' "
                "ORDER BY lifecycle_id",
                (event_id,),
            ).fetchall()
            if prior:
                exact_replay = (
                    len(prior) == 1
                    and hmac.compare_digest(
                        str(prior[0]["detail_sha256"]), detail_sha256
                    )
                    and outbox["status"] == "failed"
                    and claim["state"] == "terminal_failed"
                    and claim["last_error_code"] == _RECONCILIATION_ERROR_CODE
                    and claim["launch_receipt_json"] is None
                )
                if not exact_replay:
                    raise TaskAdmissionError("terminal_reconciliation_conflict", 409)
                connection.rollback()
                return TerminalReconciliationResult(
                    replayed=True,
                    record={
                        "task_id": task_id,
                        "event_id": event_id,
                        "claim_id": claim_id,
                        "outbox_status": "failed",
                        "claim_state": "terminal_failed",
                        "error_code": _RECONCILIATION_ERROR_CODE,
                        "launch_receipt": None,
                        "detail_sha256": detail_sha256,
                    },
                )

            if outbox["status"] not in {"pending", "claimed"}:
                raise TaskAdmissionError(
                    "terminal_reconciliation_outbox_state_conflict", 409
                )
            if claim["state"] != evidence["expected_current_state"]:
                raise TaskAdmissionError(
                    "terminal_reconciliation_claim_state_conflict", 409
                )
            if claim["launch_receipt_json"] is not None:
                raise TaskAdmissionError(
                    "terminal_reconciliation_launch_receipt_present", 409
                )
            active_lease = connection.execute(
                "SELECT 1 FROM task_admission_writer_lease LIMIT 1"
            ).fetchone()
            if active_lease is not None:
                raise TaskAdmissionError(
                    "terminal_reconciliation_writer_lease_active", 409
                )
            if outbox["status"] == "claimed":
                try:
                    claim_lease_expires = _parse_timestamp(
                        str(claim["lease_expires_at"])
                    )
                except (TypeError, ValueError) as exc:
                    raise TaskAdmissionError(
                        "terminal_reconciliation_claim_lease_invalid", 409
                    ) from exc
                if claim_lease_expires > now:
                    raise TaskAdmissionError(
                        "terminal_reconciliation_claim_lease_active", 409
                    )
            completion = connection.execute(
                "SELECT 1 FROM task_admission_lifecycle "
                "WHERE event_id=? AND event='launched' LIMIT 1",
                (event_id,),
            ).fetchone()
            if completion is not None:
                raise TaskAdmissionError(
                    "terminal_reconciliation_completion_conflict", 409
                )

            outbox_update = connection.execute(
                "UPDATE task_admission_outbox SET status='failed',claimed_at=NULL,"
                "processed_at=NULL WHERE event_id=? AND status=?",
                (event_id, outbox["status"]),
            )
            claim_update = connection.execute(
                "UPDATE task_admission_consumer_claims SET state='terminal_failed',"
                "last_error_code=?,launch_receipt_json=NULL,updated_at=? "
                "WHERE event_id=? AND claim_id=? AND state='retryable_failed'",
                (_RECONCILIATION_ERROR_CODE, now_text, event_id, claim_id),
            )
            if outbox_update.rowcount != 1 or claim_update.rowcount != 1:
                raise TaskAdmissionError(
                    "terminal_reconciliation_concurrent_change", 409
                )
            claim_record = Claim(
                event_id=event_id,
                task_id=task_id,
                claim_id=claim_id,
                attempt=int(claim["attempt_count"]),
                recovered=False,
            )
            self._append_lifecycle(
                connection,
                claim_record,
                "terminal_reconciled",
                detail=evidence,
            )
            connection.commit()
            return TerminalReconciliationResult(
                replayed=False,
                record={
                    "task_id": task_id,
                    "event_id": event_id,
                    "claim_id": claim_id,
                    "outbox_status": "failed",
                    "claim_state": "terminal_failed",
                    "error_code": _RECONCILIATION_ERROR_CODE,
                    "launch_receipt": None,
                    "detail_sha256": detail_sha256,
                },
            )
        except TaskAdmissionError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise TaskAdmissionError(
                "terminal_reconciliation_storage_failed", 503
            ) from exc
        finally:
            connection.close()

    def claim_one(self) -> Claim | None:
        now = _utc(self.now())
        now_text = _timestamp(now)
        lease_expires = _timestamp(now + timedelta(seconds=self.lease_seconds))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM task_admission_writer_lease WHERE slot = 1"
            ).fetchone()
            if lease is not None and _parse_timestamp(lease["lease_expires_at"]) > now:
                connection.rollback()
                return None
            candidate = connection.execute(
                """
                SELECT o.event_id,o.task_id,o.status,c.claim_id,c.attempt_count,c.lease_expires_at
                FROM task_admission_outbox o
                LEFT JOIN task_admission_consumer_claims c ON c.event_id=o.event_id
                WHERE o.topic=? AND (
                    o.status='pending' OR
                    (o.status='claimed' AND c.lease_expires_at IS NOT NULL AND c.lease_expires_at <= ?)
                )
                ORDER BY o.created_at,o.event_id
                LIMIT 1
                """,
                (_TOPIC, now_text),
            ).fetchone()
            if candidate is None:
                if lease is not None:
                    connection.execute(
                        "DELETE FROM task_admission_writer_lease WHERE slot=1"
                    )
                    connection.commit()
                else:
                    connection.rollback()
                return None
            recovered = candidate["status"] == "claimed"
            attempt = int(candidate["attempt_count"] or 0) + 1
            claim_id = uuid.uuid4().hex
            claim = Claim(
                event_id=candidate["event_id"],
                task_id=candidate["task_id"],
                claim_id=claim_id,
                attempt=attempt,
                recovered=recovered,
            )
            connection.execute(
                "UPDATE task_admission_outbox SET status='claimed',claimed_at=?,processed_at=NULL "
                "WHERE event_id=?",
                (now_text, claim.event_id),
            )
            connection.execute(
                """
                INSERT INTO task_admission_consumer_claims
                    (event_id,task_id,claim_id,claimed_by,lease_expires_at,attempt_count,state,last_error_code,launch_receipt_json,updated_at)
                VALUES (?,?,?,?,?,?,'claimed',NULL,NULL,?)
                ON CONFLICT(event_id) DO UPDATE SET
                    claim_id=excluded.claim_id,
                    claimed_by=excluded.claimed_by,
                    lease_expires_at=excluded.lease_expires_at,
                    attempt_count=excluded.attempt_count,
                    state='claimed',
                    last_error_code=NULL,
                    launch_receipt_json=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    claim.event_id,
                    claim.task_id,
                    claim.claim_id,
                    self.identity,
                    lease_expires,
                    claim.attempt,
                    now_text,
                ),
            )
            connection.execute(
                """
                INSERT INTO task_admission_writer_lease(slot,event_id,claim_id,lease_expires_at,updated_at)
                VALUES (1,?,?,?,?)
                ON CONFLICT(slot) DO UPDATE SET
                    event_id=excluded.event_id,
                    claim_id=excluded.claim_id,
                    lease_expires_at=excluded.lease_expires_at,
                    updated_at=excluded.updated_at
                """,
                (claim.event_id, claim.claim_id, lease_expires, now_text),
            )
            self._append_lifecycle(
                connection, claim, "recovered" if recovered else "claimed"
            )
            connection.commit()
            return claim
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _claim_rows(self, claim: Claim) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        connection = self._connect()
        try:
            outbox = connection.execute(
                "SELECT * FROM task_admission_outbox WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            admission = connection.execute(
                "SELECT * FROM task_admissions WHERE task_id=?", (claim.task_id,)
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM task_admission_consumer_claims WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            if outbox is None or admission is None or current is None:
                raise AdmissionRevalidationError("durable_rows_missing")
            if current["claim_id"] != claim.claim_id or outbox["status"] != "claimed":
                raise AdmissionRevalidationError("claim_superseded")
            return outbox, admission, current
        finally:
            connection.close()

    def revalidate(self, claim: Claim) -> LaunchRequest:
        outbox, admission, _ = self._claim_rows(claim)
        try:
            payload = json.loads(admission["payload_json"])
            envelope = json.loads(outbox["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise AdmissionRevalidationError("durable_json_invalid") from exc
        if not isinstance(payload, dict) or not isinstance(envelope, dict):
            raise AdmissionRevalidationError("durable_json_not_object")
        try:
            _validate_schema(payload)
        except Exception as exc:
            raise AdmissionRevalidationError("durable_schema_invalid") from exc
        payload_digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
        if not hmac.compare_digest(payload_digest, admission["payload_sha256"]):
            raise AdmissionRevalidationError("admission_payload_digest_mismatch")
        expected_envelope = {
            **payload,
            "actor": admission["actor"],
            "payload_sha256": admission["payload_sha256"],
            "event_id": admission["event_id"],
            "received_at": admission["received_at"],
        }
        if not hmac.compare_digest(
            _canonical_json(expected_envelope), _canonical_json(envelope)
        ):
            raise AdmissionRevalidationError("outbox_envelope_mismatch")
        if (
            outbox["topic"] != _TOPIC
            or outbox["task_id"] != admission["task_id"]
            or outbox["event_id"] != admission["event_id"]
            or payload["writer_cap"] != 1
        ):
            raise AdmissionRevalidationError("durable_binding_mismatch")
        worktrees, producers, _ = _load_policy(self.policy_path)
        if payload["producer_identity"] not in producers:
            raise AdmissionRevalidationError("producer_not_allowed")
        worktree = Path(payload["worktree"])
        if not worktree.is_absolute():
            raise AdmissionRevalidationError("worktree_not_absolute")
        try:
            canonical_worktree = worktree.resolve(strict=True)
        except OSError as exc:
            raise AdmissionRevalidationError("worktree_unavailable") from exc
        if canonical_worktree != worktree or canonical_worktree not in worktrees:
            raise AdmissionRevalidationError("worktree_not_allowed")
        before = (
            self.git_runner(worktree, "HEAD"),
            self.git_runner(worktree, "HEAD^{tree}"),
            self.git_runner(worktree, "STATUS"),
        )
        if before[0] != payload["base_commit"] or before[1] != payload["base_tree"]:
            raise AdmissionRevalidationError("git_tuple_mismatch")
        if before[2]:
            raise AdmissionRevalidationError("worktree_dirty")
        digest = _hash_task_file_secure(worktree, payload["task_file"])
        if not hmac.compare_digest(digest, payload["task_file_sha256"]):
            raise AdmissionRevalidationError("task_file_digest_mismatch")
        after = (
            self.git_runner(worktree, "HEAD"),
            self.git_runner(worktree, "HEAD^{tree}"),
            self.git_runner(worktree, "STATUS"),
        )
        if after != before:
            raise AdmissionRevalidationError("worktree_changed_during_revalidation")
        return LaunchRequest(
            event_id=claim.event_id,
            task_id=claim.task_id,
            producer_identity=payload["producer_identity"],
            base_commit=payload["base_commit"],
            base_tree=payload["base_tree"],
            task_file_sha256=payload["task_file_sha256"],
            writer_cap=1,
            worktree=str(worktree),
            task_file=payload["task_file"],
            actor=admission["actor"],
        )

    def _renew(self, claim: Claim) -> None:
        now = _utc(self.now())
        lease_expires = _timestamp(now + timedelta(seconds=self.lease_seconds))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT claim_id FROM task_admission_consumer_claims WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            lease = connection.execute(
                "SELECT claim_id FROM task_admission_writer_lease WHERE slot=1"
            ).fetchone()
            if (
                current is None
                or lease is None
                or current["claim_id"] != claim.claim_id
                or lease["claim_id"] != claim.claim_id
            ):
                raise LeaseHeartbeatError("lease_lost")
            connection.execute(
                "UPDATE task_admission_consumer_claims SET lease_expires_at=?,updated_at=? "
                "WHERE event_id=? AND claim_id=?",
                (
                    lease_expires,
                    _timestamp(now),
                    claim.event_id,
                    claim.claim_id,
                ),
            )
            connection.execute(
                "UPDATE task_admission_writer_lease SET lease_expires_at=?,updated_at=? "
                "WHERE slot=1 AND claim_id=?",
                (lease_expires, _timestamp(now), claim.claim_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _transition(self, claim: Claim, state: str, lifecycle_event: str) -> None:
        now = _utc(self.now())
        lease_expires = _timestamp(now + timedelta(seconds=self.lease_seconds))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT claim_id FROM task_admission_consumer_claims WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            lease = connection.execute(
                "SELECT claim_id FROM task_admission_writer_lease WHERE slot=1"
            ).fetchone()
            if (
                current is None
                or lease is None
                or current["claim_id"] != claim.claim_id
                or lease["claim_id"] != claim.claim_id
            ):
                raise AdmissionRevalidationError("lease_lost")
            connection.execute(
                "UPDATE task_admission_consumer_claims SET state=?,lease_expires_at=?,updated_at=? "
                "WHERE event_id=? AND claim_id=?",
                (state, lease_expires, _timestamp(now), claim.event_id, claim.claim_id),
            )
            connection.execute(
                "UPDATE task_admission_writer_lease SET lease_expires_at=?,updated_at=? "
                "WHERE slot=1 AND claim_id=?",
                (lease_expires, _timestamp(now), claim.claim_id),
            )
            self._append_lifecycle(connection, claim, lifecycle_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_receipt(receipt: Mapping[str, Any], event_id: str) -> dict[str, Any]:
        if set(receipt) != {"accepted", "idempotency_key", "launch_id"}:
            raise LauncherError("launcher_receipt_shape_invalid")
        if receipt["accepted"] is not True:
            raise LauncherError("launcher_rejected")
        if not isinstance(receipt["idempotency_key"], str) or not hmac.compare_digest(
            receipt["idempotency_key"], event_id
        ):
            raise LauncherError("launcher_idempotency_mismatch")
        if (
            not isinstance(receipt["launch_id"], str)
            or not receipt["launch_id"].strip()
        ):
            raise LauncherError("launcher_id_missing")
        return dict(receipt)

    def _complete(self, claim: Claim, receipt: Mapping[str, Any]) -> ConsumerResult:
        canonical_receipt = _canonical_json(receipt)
        now = _timestamp(self.now())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT claim_id FROM task_admission_consumer_claims WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            if current is None or current["claim_id"] != claim.claim_id:
                raise AdmissionRevalidationError("claim_superseded_before_complete")
            connection.execute(
                "UPDATE task_admission_outbox SET status='processed',processed_at=? WHERE event_id=? AND status='claimed'",
                (now, claim.event_id),
            )
            if connection.total_changes < 1:
                raise AdmissionRevalidationError("outbox_not_claimed")
            connection.execute(
                "UPDATE task_admission_consumer_claims SET state='completed',launch_receipt_json=?,updated_at=? "
                "WHERE event_id=? AND claim_id=?",
                (canonical_receipt, now, claim.event_id, claim.claim_id),
            )
            self._append_lifecycle(connection, claim, "launched", detail=receipt)
            connection.execute(
                "DELETE FROM task_admission_writer_lease WHERE slot=1 AND claim_id=?",
                (claim.claim_id,),
            )
            connection.commit()
            return ConsumerResult(
                event_id=claim.event_id,
                task_id=claim.task_id,
                status="processed",
                claim_id=claim.claim_id,
                attempt=claim.attempt,
                launch_id=str(receipt["launch_id"]),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _fail(self, claim: Claim, exc: Exception, *, terminal: bool) -> None:
        now = _timestamp(self.now())
        code = type(exc).__name__
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT claim_id FROM task_admission_consumer_claims WHERE event_id=?",
                (claim.event_id,),
            ).fetchone()
            if current is None or current["claim_id"] != claim.claim_id:
                connection.rollback()
                return
            connection.execute(
                "UPDATE task_admission_outbox SET status=?,claimed_at=NULL WHERE event_id=?",
                ("failed" if terminal else "pending", claim.event_id),
            )
            connection.execute(
                "UPDATE task_admission_consumer_claims SET state=?,last_error_code=?,updated_at=? "
                "WHERE event_id=? AND claim_id=?",
                (
                    "terminal_failed" if terminal else "retryable_failed",
                    code,
                    now,
                    claim.event_id,
                    claim.claim_id,
                ),
            )
            self._append_lifecycle(
                connection,
                claim,
                "validation_failed" if terminal else "launch_failed",
                detail={"error_code": code},
            )
            connection.execute(
                "DELETE FROM task_admission_writer_lease WHERE slot=1 AND claim_id=?",
                (claim.claim_id,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def run_once(self, launcher: Launcher) -> ConsumerResult | None:
        claim = self.claim_one()
        if claim is None:
            return None
        try:
            request = self.revalidate(claim)
            self._transition(claim, "validated", "validated")
            final_request = self.revalidate(claim)
            if not hmac.compare_digest(
                _canonical_json(request.as_dict()),
                _canonical_json(final_request.as_dict()),
            ):
                raise AdmissionRevalidationError("tuple_changed_before_launch")
            self._transition(claim, "launch_started", "launch_started")
            stop = threading.Event()
            heartbeat_errors: list[Exception] = []

            def heartbeat() -> None:
                interval = max(1.0, self.lease_seconds / 3)
                while not stop.wait(interval):
                    try:
                        self._renew(claim)
                    except Exception as exc:  # pragma: no cover - rare runtime path
                        heartbeat_errors.append(exc)
                        stop.set()

            thread = threading.Thread(
                target=heartbeat,
                name=f"task-admission-lease-{claim.claim_id[:8]}",
                daemon=True,
            )
            thread.start()
            try:
                raw_receipt = launcher(final_request)
            finally:
                stop.set()
                thread.join(timeout=5)
            if heartbeat_errors:
                raise LeaseHeartbeatError(
                    "lease_heartbeat_failed"
                ) from heartbeat_errors[0]
            self._renew(claim)
            receipt = self._validate_receipt(raw_receipt, claim.event_id)
            return self._complete(claim, receipt)
        except AdmissionRevalidationError as exc:
            self._fail(claim, exc, terminal=True)
            raise
        except Exception as exc:
            self._fail(claim, exc, terminal=False)
            raise


def _load_launcher_config(path: Path, producer: str) -> tuple[list[str], int]:
    try:
        document = json.loads(_read_policy_bytes(path))
    except Exception as exc:
        raise LauncherError("launcher_config_unavailable") from exc
    if not isinstance(document, dict) or set(document) != {"version", "producers"}:
        raise LauncherError("launcher_config_invalid")
    producers = document["producers"]
    if (
        document["version"] != 1
        or not isinstance(producers, dict)
        or producer not in producers
    ):
        raise LauncherError("launcher_config_invalid")
    item = producers[producer]
    if not isinstance(item, dict) or set(item) != {"command", "timeout_seconds"}:
        raise LauncherError("launcher_config_invalid")
    command = item["command"]
    timeout = item["timeout_seconds"]
    if (
        not isinstance(command, list)
        or len(command) != 1
        or not isinstance(command[0], str)
        or not command[0]
        or isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or timeout > 3600
    ):
        raise LauncherError("launcher_config_invalid")
    executable = Path(command[0])
    try:
        metadata = executable.lstat()
        canonical_executable = executable.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("launcher_executable_invalid") from exc
    if (
        not executable.is_absolute()
        or executable != canonical_executable
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(executable, os.X_OK)
    ):
        raise LauncherError("launcher_executable_invalid")
    for parent in executable.parents:
        try:
            parent_metadata = parent.lstat()
        except OSError as exc:
            raise LauncherError("launcher_executable_invalid") from exc
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        ):
            raise LauncherError("launcher_executable_invalid")
    return [str(canonical_executable), *command[1:]], timeout


def command_launcher(config_path: Path) -> Launcher:
    def launch(request: LaunchRequest) -> Mapping[str, Any]:
        command, timeout = _load_launcher_config(config_path, request.producer_identity)
        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                shell=False,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None:
                raise LauncherError("launcher_pipe_unavailable")
            request_bytes = _canonical_json(request.as_dict()).encode()
            if len(request_bytes) > 32 * 1024:
                raise LauncherError("launcher_request_too_large")
            process.stdin.write(request_bytes)
            process.stdin.close()
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            output = bytearray()
            deadline = time.monotonic() + timeout
            eof = False
            while not eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LauncherError("launcher_command_timeout")
                events = selector.select(remaining)
                if not events:
                    raise LauncherError("launcher_command_timeout")
                for key, _ in events:
                    chunk = os.read(
                        key.fd,
                        min(64 * 1024, _MAX_LAUNCHER_OUTPUT + 1 - len(output)),
                    )
                    if not chunk:
                        eof = True
                        break
                    output.extend(chunk)
                    if len(output) > _MAX_LAUNCHER_OUTPUT:
                        raise LauncherError("launcher_output_too_large")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LauncherError("launcher_command_timeout")
            process.wait(timeout=remaining)
            if process.returncode != 0:
                raise LauncherError("launcher_command_failed")
            raw_receipt = bytes(output)
        except LauncherError:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise LauncherError("launcher_command_failed") from exc
        finally:
            if selector is not None:
                selector.close()
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
        try:
            receipt = json.loads(raw_receipt)
        except json.JSONDecodeError as exc:
            raise LauncherError("launcher_receipt_invalid_json") from exc
        if not isinstance(receipt, dict):
            raise LauncherError("launcher_receipt_not_object")
        return receipt

    return launch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consume at most one task admission")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--launcher-config", required=True, type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--lease-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    consumer = TaskAdmissionConsumer(
        db_path=args.db,
        policy_path=args.policy,
        identity=args.identity,
        lease_seconds=args.lease_seconds,
    )
    result = consumer.run_once(command_launcher(args.launcher_config))
    if result is None:
        print(json.dumps({"status": "idle"}, sort_keys=True))
    else:
        print(json.dumps(result.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
