"""SQLite database layer for the Review/Merge Factory V1.

Provides table creation, migration helpers, and CRUD operations for
all five canonical record schemas.

**CRITICAL (OKF §16.8 anti-pattern #1):** The factory tables live
INSIDE ``agy_completed_work``'s existing SQLite DB — NOT a separate
file.  This reuses ``agy_completed_work.default_db_path()`` as the
single source of truth.  The ``completed_work_id`` FK in ``review_jobs``
references the ``agy_completed_work.id`` column in the SAME database.

Usage
-----
    from prismatic.review_factory.db import ReviewFactoryDB

    db = ReviewFactoryDB()  # uses agy_completed_work's DB path
    db.ensure_tables()

    # Insert a review job
    job = ReviewJob(task_id="GRO-4188", repository="mbgulden/prismatic-engine", ...)
    db.insert_review_job(job)

    # Query
    job = db.get_review_job(review_job_id)
    queued = db.list_review_jobs(state=ReviewJobState.QUEUED)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from prismatic.review_factory.models import (
    MergeAuthorization,
    RepairPacket,
    ReviewDecision,
    ReviewJob,
    ReviewJobState,
    VerificationReceipt,
)


# ─────────────────────────────────────────────────────────────────────
# Path resolution — reuses agy_completed_work's DB
# ─────────────────────────────────────────────────────────────────────


def _agy_default_state_dir() -> Path:
    """Mirror of agy_completed_work.default_state_dir().

    We replicate instead of importing to avoid circular dependencies
    during early module loading.  This MUST stay in sync with
    ``prismatic.agy_completed_work.default_state_dir()``.
    """
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def default_db_path() -> Path:
    """Resolve the shared database path.

    This returns the SAME path as ``agy_completed_work.default_db_path()``.
    The Review Factory tables are additional tables inside that DB.

    Priority:
    1. ``PRISMATIC_AGY_COMPLETED_WORK_DB`` env var
    2. ``$PRISMATIC_STATE_DIR/agy_completed_work.db``
    3. ``./prismatic_state/agy_completed_work.db`` (fallback)
    """
    return Path(
        os.environ.get(
            "PRISMATIC_AGY_COMPLETED_WORK_DB",
            str(_agy_default_state_dir() / "agy_completed_work.db"),
        )
    ).expanduser()


# ─────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

_CREATE_TABLES = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS rf_schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

-- §5.1: review_jobs
CREATE TABLE IF NOT EXISTS review_jobs (
    review_job_id TEXT PRIMARY KEY,
    completed_work_id TEXT UNIQUE NOT NULL,
    task_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    base_tree TEXT NOT NULL,
    candidate_commit TEXT NOT NULL,
    candidate_tree TEXT NOT NULL,
    result_packet_path TEXT NOT NULL DEFAULT '',
    result_packet_sha256 TEXT NOT NULL DEFAULT '',
    changed_paths_json TEXT NOT NULL DEFAULT '[]',
    risk_tier INTEGER NOT NULL DEFAULT 1,
    policy_version TEXT NOT NULL DEFAULT 'v1',
    state TEXT NOT NULL DEFAULT 'queued',
    required_witnesses INTEGER NOT NULL DEFAULT 0,
    completed_witnesses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_review_jobs_state ON review_jobs(state);
CREATE INDEX IF NOT EXISTS idx_review_jobs_task_id ON review_jobs(task_id);
CREATE INDEX IF NOT EXISTS idx_review_jobs_completed_work_id ON review_jobs(completed_work_id);
CREATE INDEX IF NOT EXISTS idx_review_jobs_candidate_tree ON review_jobs(candidate_tree);

-- §5.2: verification_receipts
CREATE TABLE IF NOT EXISTS verification_receipts (
    receipt_id TEXT PRIMARY KEY,
    review_job_id TEXT NOT NULL,
    candidate_commit TEXT NOT NULL,
    candidate_tree TEXT NOT NULL,
    immutable_archive_id TEXT NOT NULL DEFAULT '',
    commands TEXT NOT NULL DEFAULT '[]',
    exit_codes TEXT NOT NULL DEFAULT '{}',
    log_paths TEXT NOT NULL DEFAULT '{}',
    log_sha256 TEXT NOT NULL DEFAULT '{}',
    changed_path_invariance_proof TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'targeted',
    explicit_non_claims TEXT NOT NULL DEFAULT '[]',
    baseline_failures TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_job_id) REFERENCES review_jobs(review_job_id)
);

CREATE INDEX IF NOT EXISTS idx_receipts_review_job ON verification_receipts(review_job_id);

-- §5.3: review_decisions
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id TEXT PRIMARY KEY,
    review_job_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_capability_version TEXT NOT NULL DEFAULT '',
    candidate_commit TEXT NOT NULL,
    candidate_tree TEXT NOT NULL,
    receipt_id TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    findings TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_job_id) REFERENCES review_jobs(review_job_id),
    FOREIGN KEY (receipt_id) REFERENCES verification_receipts(receipt_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_idempotency ON review_decisions(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_decisions_review_job ON review_decisions(review_job_id);

-- §5.4: merge_authorizations
CREATE TABLE IF NOT EXISTS merge_authorizations (
    authorization_id TEXT PRIMARY KEY,
    review_job_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    pr_number INTEGER NOT NULL DEFAULT 0,
    pr_head_commit TEXT NOT NULL DEFAULT '',
    pr_base_commit TEXT NOT NULL DEFAULT '',
    candidate_tree TEXT NOT NULL DEFAULT '',
    expected_merge_tree TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT 'v1',
    actor TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'tier-1-auto',
    expires_at TEXT NOT NULL DEFAULT '',
    consumed_at TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (review_job_id) REFERENCES review_jobs(review_job_id)
);

CREATE INDEX IF NOT EXISTS idx_auth_review_job ON merge_authorizations(review_job_id);
CREATE INDEX IF NOT EXISTS idx_auth_consumed ON merge_authorizations(consumed_at);

-- §5.5: repair_packets
CREATE TABLE IF NOT EXISTS repair_packets (
    packet_id TEXT PRIMARY KEY,
    candidate_tree TEXT NOT NULL DEFAULT '',
    findings_json TEXT NOT NULL DEFAULT '[]',
    producer_id TEXT NOT NULL DEFAULT '',
    consumed_at TEXT NOT NULL DEFAULT '',
    resolution_attempt_n INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repair_candidate ON repair_packets(candidate_tree);

-- Enterprise Audit Log: append-only table for operator actions
CREATE TABLE IF NOT EXISTS review_factory_audit_log (
    audit_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    review_job_id TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    signature_hash TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON review_factory_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON review_factory_audit_log(timestamp);
"""


# ─────────────────────────────────────────────────────────────────────
# Database class
# ─────────────────────────────────────────────────────────────────────


class ReviewFactoryDB:
    """SQLite persistence for the Review Factory.

    Thread-safe via ``check_same_thread=False`` and WAL mode.
    All writes are atomic (single transaction per method).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage transactions
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for an atomic transaction."""
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema management ────────────────────────────────────────────

    def ensure_tables(self) -> None:
        """Create all tables if they don't exist.  Idempotent."""
        self.conn.executescript(_CREATE_TABLES)
        # Record schema version if not already present
        cur = self.conn.execute(
            "SELECT version FROM rf_schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None or row["version"] < SCHEMA_VERSION:
            self.conn.execute(
                "INSERT INTO rf_schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )

    # ── review_jobs CRUD ─────────────────────────────────────────────

    def insert_review_job(self, job: ReviewJob) -> str:
        """Insert a new review job.  Returns the review_job_id."""
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO review_jobs (
                    review_job_id, completed_work_id, task_id,
                    repository, base_commit, base_tree,
                    candidate_commit, candidate_tree,
                    result_packet_path, result_packet_sha256,
                    changed_paths_json, risk_tier, policy_version,
                    state, required_witnesses, completed_witnesses,
                    created_at, lease_owner, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.review_job_id,
                    job.completed_work_id,
                    job.task_id,
                    job.repository,
                    job.base_commit,
                    job.base_tree,
                    job.candidate_commit,
                    job.candidate_tree,
                    job.result_packet_path,
                    job.result_packet_sha256,
                    job.changed_paths_json,
                    job.risk_tier,
                    job.policy_version,
                    job.state,
                    job.required_witnesses,
                    job.completed_witnesses,
                    job.created_at,
                    job.lease_owner,
                    job.lease_expires_at,
                ),
            )
        return job.review_job_id

    def get_review_job(self, review_job_id: str) -> Optional[ReviewJob]:
        """Fetch a single review job by ID."""
        cur = self.conn.execute(
            "SELECT * FROM review_jobs WHERE review_job_id = ?",
            (review_job_id,),
        )
        row = cur.fetchone()
        return self._row_to_review_job(row) if row else None

    def list_review_jobs(
        self,
        state: Optional[ReviewJobState] = None,
        limit: int = 100,
    ) -> list[ReviewJob]:
        """List review jobs, optionally filtered by state."""
        if state is not None:
            cur = self.conn.execute(
                "SELECT * FROM review_jobs WHERE state = ? ORDER BY created_at ASC LIMIT ?",
                (state.value, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM review_jobs ORDER BY created_at ASC LIMIT ?",
                (limit,),
            )
        return [self._row_to_review_job(r) for r in cur.fetchall()]

    def get_job_by_completed_work_id(
        self, completed_work_id: str
    ) -> Optional[ReviewJob]:
        """Fetch job by completed_work_id via SQL index query."""
        cur = self.conn.execute(
            "SELECT * FROM review_jobs WHERE completed_work_id = ? LIMIT 1",
            (completed_work_id,),
        )
        row = cur.fetchone()
        return self._row_to_review_job(row) if row else None

    def update_review_job_state(
        self,
        review_job_id: str,
        new_state: ReviewJobState,
        lease_owner: str = "",
        lease_expires_at: str = "",
    ) -> bool:
        """Atomically transition a review job's state.

        Returns True if the update succeeded, False if the job
        was not found or the transition was invalid.
        """
        job = self.get_review_job(review_job_id)
        if job is None:
            return False

        current = ReviewJobState(job.state)
        if current == new_state:
            return True  # idempotent

        if not current.can_transition_to(new_state):
            return False

        with self.transaction() as cur:
            cur.execute(
                """UPDATE review_jobs
                   SET state = ?, lease_owner = ?, lease_expires_at = ?
                   WHERE review_job_id = ? AND state = ?""",
                (
                    new_state.value,
                    lease_owner,
                    lease_expires_at,
                    review_job_id,
                    current.value,
                ),
            )
            return cur.rowcount > 0

    def update_completed_witnesses(self, review_job_id: str) -> int:
        """Recalculate completed_witnesses from distinct clean decision reviewers."""
        with self.transaction() as cur:
            cur.execute(
                """SELECT COUNT(DISTINCT reviewer_id) as cnt
                   FROM review_decisions
                   WHERE review_job_id = ? AND verdict = 'clean'""",
                (review_job_id,),
            )
            row = cur.fetchone()
            cnt = row["cnt"] if row else 0
            cur.execute(
                """UPDATE review_jobs
                   SET completed_witnesses = ?
                   WHERE review_job_id = ?""",
                (cnt, review_job_id),
            )
            return cnt

    def increment_witnesses(self, review_job_id: str) -> int:
        """Alias for update_completed_witnesses to preserve backward compatibility."""
        return self.update_completed_witnesses(review_job_id)

    # ── verification_receipts CRUD ───────────────────────────────────

    def insert_receipt(self, receipt: VerificationReceipt) -> str:
        """Insert a verification receipt.  Returns receipt_id."""
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO verification_receipts (
                    receipt_id, review_job_id,
                    candidate_commit, candidate_tree,
                    immutable_archive_id,
                    commands, exit_codes, log_paths, log_sha256,
                    changed_path_invariance_proof,
                    classification, explicit_non_claims, baseline_failures,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.receipt_id,
                    receipt.review_job_id,
                    receipt.candidate_commit,
                    receipt.candidate_tree,
                    receipt.immutable_archive_id,
                    receipt.commands,
                    receipt.exit_codes,
                    receipt.log_paths,
                    receipt.log_sha256,
                    receipt.changed_path_invariance_proof,
                    receipt.classification,
                    receipt.explicit_non_claims,
                    receipt.baseline_failures,
                    receipt.created_at,
                ),
            )
        return receipt.receipt_id

    def get_receipts_for_job(self, review_job_id: str) -> list[VerificationReceipt]:
        """Fetch all receipts for a review job."""
        cur = self.conn.execute(
            "SELECT * FROM verification_receipts WHERE review_job_id = ? ORDER BY created_at",
            (review_job_id,),
        )
        return [self._row_to_receipt(r) for r in cur.fetchall()]

    # ── review_decisions CRUD ────────────────────────────────────────

    def insert_decision(self, decision: ReviewDecision) -> str:
        """Insert a review decision.  Idempotent via idempotency_key.

        Returns decision_id.  If a decision with the same idempotency_key
        already exists, the existing decision_id is returned (no-op).
        """
        with self.transaction() as cur:
            # Check for existing idempotent decision
            cur.execute(
                "SELECT decision_id FROM review_decisions WHERE idempotency_key = ?",
                (decision.idempotency_key,),
            )
            existing = cur.fetchone()
            if existing:
                return existing["decision_id"]

            cur.execute(
                """INSERT INTO review_decisions (
                    decision_id, review_job_id,
                    reviewer_id, reviewer_capability_version,
                    candidate_commit, candidate_tree,
                    receipt_id, verdict, findings,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.decision_id,
                    decision.review_job_id,
                    decision.reviewer_id,
                    decision.reviewer_capability_version,
                    decision.candidate_commit,
                    decision.candidate_tree,
                    decision.receipt_id,
                    decision.verdict,
                    decision.findings,
                    decision.idempotency_key,
                    decision.created_at,
                ),
            )
        return decision.decision_id

    def get_decisions_for_job(self, review_job_id: str) -> list[ReviewDecision]:
        """Fetch all decisions for a review job."""
        cur = self.conn.execute(
            "SELECT * FROM review_decisions WHERE review_job_id = ? ORDER BY created_at",
            (review_job_id,),
        )
        return [self._row_to_decision(r) for r in cur.fetchall()]

    # ── merge_authorizations CRUD ────────────────────────────────────

    def insert_authorization(self, auth: MergeAuthorization) -> str:
        """Insert a merge authorization.  Returns authorization_id."""
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO merge_authorizations (
                    authorization_id, review_job_id,
                    repository, pr_number,
                    pr_head_commit, pr_base_commit,
                    candidate_tree, expected_merge_tree,
                    policy_version, actor, scope,
                    expires_at, consumed_at, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth.authorization_id,
                    auth.review_job_id,
                    auth.repository,
                    auth.pr_number,
                    auth.pr_head_commit,
                    auth.pr_base_commit,
                    auth.candidate_tree,
                    auth.expected_merge_tree,
                    auth.policy_version,
                    auth.actor,
                    auth.scope,
                    auth.expires_at,
                    auth.consumed_at,
                    auth.idempotency_key,
                ),
            )
        return auth.authorization_id

    def get_authorization_for_job(
        self, review_job_id: str
    ) -> Optional[MergeAuthorization]:
        """Fetch the latest unconsumed authorization for a job."""
        cur = self.conn.execute(
            """SELECT * FROM merge_authorizations
               WHERE review_job_id = ? AND consumed_at = ''
               ORDER BY expires_at DESC LIMIT 1""",
            (review_job_id,),
        )
        row = cur.fetchone()
        return self._row_to_authorization(row) if row else None

    def consume_authorization(self, authorization_id: str) -> bool:
        """Mark an authorization as consumed.  Idempotent."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """UPDATE merge_authorizations
                   SET consumed_at = ?
                   WHERE authorization_id = ? AND consumed_at = ''""",
                (now, authorization_id),
            )
            return cur.rowcount > 0

    # ── repair_packets CRUD ──────────────────────────────────────────

    def insert_repair_packet(self, packet: RepairPacket) -> str:
        """Insert a repair packet.  Returns packet_id."""
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO repair_packets (
                    packet_id, candidate_tree, findings_json,
                    producer_id, consumed_at, resolution_attempt_n,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    packet.packet_id,
                    packet.candidate_tree,
                    packet.findings_json,
                    packet.producer_id,
                    packet.consumed_at,
                    packet.resolution_attempt_n,
                    packet.created_at,
                ),
            )
        return packet.packet_id

    def get_unconsumed_repairs(self, candidate_tree: str) -> list[RepairPacket]:
        """Fetch unconsumed repair packets for a candidate."""
        cur = self.conn.execute(
            """SELECT * FROM repair_packets
               WHERE candidate_tree = ? AND consumed_at = ''
               ORDER BY created_at""",
            (candidate_tree,),
        )
        return [self._row_to_repair_packet(r) for r in cur.fetchall()]

    # ── Janitor / recovery ───────────────────────────────────────────

    def reset_stale_leases(self, now_iso: Optional[str] = None) -> int:
        """Reset jobs with expired leases back to their re-queue state.

        - ``verifying`` with expired lease → ``queued``
        - ``reviewing`` with expired lease → ``review_ready``

        Returns the number of jobs reset.
        """
        now = now_iso or datetime.now(timezone.utc).isoformat()
        count = 0
        with self.transaction() as cur:
            # Verifying → Queued
            cur.execute(
                """UPDATE review_jobs
                   SET state = 'queued', lease_owner = '', lease_expires_at = ''
                   WHERE state = 'verifying'
                     AND lease_expires_at != ''
                     AND lease_expires_at < ?""",
                (now,),
            )
            count += cur.rowcount

            # Reviewing → Review Ready
            cur.execute(
                """UPDATE review_jobs
                   SET state = 'review_ready', lease_owner = '', lease_expires_at = ''
                   WHERE state = 'reviewing'
                     AND lease_expires_at != ''
                     AND lease_expires_at < ?""",
                (now,),
            )
            count += cur.rowcount
        return count

    # ── Statistics ───────────────────────────────────────────────────

    def queue_stats(self) -> dict[str, int]:
        """Return counts of jobs by state."""
        cur = self.conn.execute(
            "SELECT state, COUNT(*) as cnt FROM review_jobs GROUP BY state"
        )
        return {row["state"]: row["cnt"] for row in cur.fetchall()}

    def total_jobs(self) -> int:
        """Total number of review jobs."""
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM review_jobs")
        return cur.fetchone()["cnt"]

    # ── Row mapping helpers ──────────────────────────────────────────

    @staticmethod
    def _row_to_review_job(row: sqlite3.Row) -> ReviewJob:
        return ReviewJob(
            review_job_id=row["review_job_id"],
            completed_work_id=row["completed_work_id"],
            task_id=row["task_id"],
            repository=row["repository"],
            base_commit=row["base_commit"],
            base_tree=row["base_tree"],
            candidate_commit=row["candidate_commit"],
            candidate_tree=row["candidate_tree"],
            result_packet_path=row["result_packet_path"],
            result_packet_sha256=row["result_packet_sha256"],
            changed_paths_json=row["changed_paths_json"],
            risk_tier=row["risk_tier"],
            policy_version=row["policy_version"],
            state=row["state"],
            required_witnesses=row["required_witnesses"],
            completed_witnesses=row["completed_witnesses"],
            created_at=row["created_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
        )

    @staticmethod
    def _row_to_receipt(row: sqlite3.Row) -> VerificationReceipt:
        return VerificationReceipt(
            receipt_id=row["receipt_id"],
            review_job_id=row["review_job_id"],
            candidate_commit=row["candidate_commit"],
            candidate_tree=row["candidate_tree"],
            immutable_archive_id=row["immutable_archive_id"],
            commands=row["commands"],
            exit_codes=row["exit_codes"],
            log_paths=row["log_paths"],
            log_sha256=row["log_sha256"],
            changed_path_invariance_proof=row["changed_path_invariance_proof"],
            classification=row["classification"],
            explicit_non_claims=row["explicit_non_claims"],
            baseline_failures=row["baseline_failures"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> ReviewDecision:
        return ReviewDecision(
            decision_id=row["decision_id"],
            review_job_id=row["review_job_id"],
            reviewer_id=row["reviewer_id"],
            reviewer_capability_version=row["reviewer_capability_version"],
            candidate_commit=row["candidate_commit"],
            candidate_tree=row["candidate_tree"],
            receipt_id=row["receipt_id"],
            verdict=row["verdict"],
            findings=row["findings"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_authorization(row: sqlite3.Row) -> MergeAuthorization:
        return MergeAuthorization(
            authorization_id=row["authorization_id"],
            review_job_id=row["review_job_id"],
            repository=row["repository"],
            pr_number=row["pr_number"],
            pr_head_commit=row["pr_head_commit"],
            pr_base_commit=row["pr_base_commit"],
            candidate_tree=row["candidate_tree"],
            expected_merge_tree=row["expected_merge_tree"],
            policy_version=row["policy_version"],
            actor=row["actor"],
            scope=row["scope"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            idempotency_key=row["idempotency_key"],
        )

    @staticmethod
    def _row_to_repair_packet(row: sqlite3.Row) -> RepairPacket:
        return RepairPacket(
            packet_id=row["packet_id"],
            candidate_tree=row["candidate_tree"],
            findings_json=row["findings_json"],
            producer_id=row["producer_id"],
            consumed_at=row["consumed_at"],
            resolution_attempt_n=row["resolution_attempt_n"],
            created_at=row["created_at"],
        )

    def insert_audit_entry(
        self,
        actor: str,
        action: str,
        review_job_id: str = "",
        client_ip: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> str:
        """Record an immutable audit log entry."""
        audit_id = f"audit-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details or {})
        raw_sig = f"{audit_id}:{now_iso}:{actor}:{action}:{review_job_id}:{client_ip}:{details_json}"
        sig_hash = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO review_factory_audit_log (
                    audit_id, timestamp, actor, action, review_job_id, client_ip, details_json, signature_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    now_iso,
                    actor,
                    action,
                    review_job_id,
                    client_ip,
                    details_json,
                    sig_hash,
                ),
            )
        return audit_id

    def list_audit_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        """List audit log entries ordered by timestamp descending."""
        with self.transaction() as conn:
            cur = conn.execute(
                "SELECT * FROM review_factory_audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
