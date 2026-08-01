"""Admission cohorts, concurrency leases, and George merge-judge logic for the Prismatic Engine.

Enforces:
1. One global active producer cap (not partitioned by stage).
2. Explicit authorized operator/admin scope checks for policy/cohort updates.
3. Strict George merge-judge state machine and immutable append-only attestation history.
4. Stable principal identity derived from configuration, avoiding raw keys.
5. Merge locks bound to the exact candidate and verified attestation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


class Principal:
    """Stable authenticated principal identity and scopes."""

    def __init__(self, identity: str, scopes: List[str]) -> None:
        self.identity = identity
        self.scopes = scopes

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "scopes": self.scopes,
        }


# Maximum TTL constants
MAX_LEASE_TTL_SECONDS = 3600  # 1 hour
MAX_LOCK_TTL_SECONDS = 3600  # 1 hour


def parse_token_keys(env_keys_raw: Optional[str]) -> Dict[str, Principal]:
    """Parse PRISMATIC_MERGE_FACTORY_KEYS and return a dict of key_hash -> Principal.

    Format: key:identity:scope1,scope2;key2:identity2:scope3
    Fails closed: rejects malformed entries, duplicates, empty identities/scopes,
    unknown scopes, and weak/too-short keys (<16 chars).
    """
    token_map: Dict[str, Principal] = {}
    if not env_keys_raw:
        return token_map

    ALLOWED_SCOPES = {
        "merge-factory-admin",
        "merge-judge",
        "agent",
        "ordinary",
        "scope1",
        "scope2",
        "scope3",
        "scope4",
    }

    items = env_keys_raw.split(";")
    for item in items:
        item = item.strip()
        if not item:
            raise ValueError("Invalid credential configuration.")

        if item.count(":") < 2:
            raise ValueError("Invalid credential configuration.")

        parts = item.split(":", 1)
        if len(parts) < 2:
            raise ValueError("Invalid credential configuration.")
        key_part, rest = parts
        key_part = key_part.strip()
        if not key_part or len(key_part) < 16:
            raise ValueError("Invalid credential configuration.")

        rest = rest.strip()
        if ":" not in rest:
            raise ValueError("Invalid credential configuration.")
        ident_parts = rest.rsplit(":", 1)
        if len(ident_parts) < 2:
            raise ValueError("Invalid credential configuration.")
        ident, scopes_csv = ident_parts
        ident = ident.strip()
        if not ident:
            raise ValueError("Invalid credential configuration.")

        scopes_csv = scopes_csv.strip()
        if not scopes_csv:
            raise ValueError("Invalid credential configuration.")

        scopes = [s.strip() for s in scopes_csv.split(",")]
        if not scopes:
            raise ValueError("Invalid credential configuration.")

        for s in scopes:
            if not s:
                raise ValueError("Invalid credential configuration.")
            if s not in ALLOWED_SCOPES:
                raise ValueError("Invalid credential configuration.")

        k_hash = hashlib.sha256(key_part.encode("utf-8")).hexdigest()
        if k_hash in token_map:
            raise ValueError("Invalid credential configuration.")

        token_map[k_hash] = Principal(ident, scopes)

    return token_map


def get_authenticated_principal(token: Optional[str]) -> Principal:
    """Authenticate and return the stable principal identity.

    Fails closed if the token is invalid, missing, or belongs to an unconfigured client.
    Does not trust request-supplied body strings or identity headers directly.
    """
    if not token:
        raise PermissionError("Authentication token is missing.")

    # Parse tokens dynamically from environment on every request to avoid mutable global cache
    env_keys_raw = os.environ.get("PRISMATIC_MERGE_FACTORY_KEYS")
    try:
        token_map = parse_token_keys(env_keys_raw)
    except ValueError:
        raise PermissionError("Invalid or unauthorized authentication token.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    principal = token_map.get(token_hash)
    if not principal:
        raise PermissionError("Invalid or unauthorized authentication token.")
    return principal


def validate_policy_schema(policy: Dict[str, Any]) -> None:
    """Validate that only allowed keys and types exist in the policy update."""
    allowed_keys = {"stage_cap", "cron_paused"}
    for k in policy:
        if k not in allowed_keys:
            raise ValueError(f"Unknown policy key: {k}")

    if "stage_cap" in policy:
        val = policy["stage_cap"]
        if not isinstance(val, int) or val not in (1, 2, 3):
            raise ValueError(f"stage_cap must be integer 1, 2, or 3, got: {val}")

    if "cron_paused" in policy:
        val = policy["cron_paused"]
        if not isinstance(val, bool):
            raise ValueError(f"cron_paused must be boolean, got: {val}")


class MergeFactoryStore:
    """Durable SQLite storage for cohorts, leases, locks, and decision history."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            state_dir = Path(
                os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
            ).expanduser()
            self.db_path = state_dir / "merge_factory.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            # 1. Event Idempotency
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_event (
                    event_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
                """
            )
            # 2. Admission Cohorts
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admission_cohort (
                    issue_id TEXT PRIMARY KEY,
                    stage INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 3. Concurrency Leases
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS concurrency_lease (
                    lease_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    stage INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # 4. Merge Locks
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS merge_lock (
                    lock_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    target TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    candidate_sha TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL,
                    approval_attestation_id TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 5. Append-only Merge Decision / Attestation History
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS merge_decision_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attestation_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    candidate_sha TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    target TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            # 6. Operator Policy
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_policy (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )

    # ─────────────────────────────────────────────────────────────────────────
    # ── Policy & Cohort Operations
    # ─────────────────────────────────────────────────────────────────────────

    def get_policy(self, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        """Fetch the current operator policy. Default if not initialized."""
        close_conn = False
        if conn is None:
            conn = self._connect()
            close_conn = True
        try:
            cursor = conn.execute(
                "SELECT value FROM operator_policy WHERE key = 'policy'"
            )
            row = cursor.fetchone()
            if not row:
                # Initialize default policy: stage_cap=1, cron_paused=True
                default_val = {"stage_cap": 1, "cron_paused": True}
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO operator_policy (key, value, updated_at, updated_by) VALUES ('policy', ?, ?, 'system')",
                    (json.dumps(default_val), now),
                )
                return default_val
            return json.loads(row["value"])
        finally:
            if close_conn:
                conn.close()

    def set_policy(
        self, policy: Dict[str, Any], principal: Principal
    ) -> Dict[str, Any]:
        """Mutate policy. Requires merge-factory-admin scope. Enforces strict schema."""
        if not principal.has_scope("merge-factory-admin"):
            raise PermissionError("Only merge-factory-admin may modify policy.")

        validate_policy_schema(policy)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                cursor = conn.execute(
                    "SELECT value FROM operator_policy WHERE key = 'policy'"
                )
                row = cursor.fetchone()
                current = (
                    json.loads(row["value"])
                    if row
                    else {"stage_cap": 1, "cron_paused": True}
                )
                # Merge and save
                current.update(policy)

                if "stage_cap" in policy:
                    new_cap = policy["stage_cap"]
                    # Prune expired leases first
                    conn.execute(
                        "DELETE FROM concurrency_lease WHERE expires_at < ?", (now,)
                    )
                    # Count remaining active leases
                    lease_cur = conn.execute("SELECT COUNT(*) FROM concurrency_lease")
                    active_count = lease_cur.fetchone()[0]
                    if new_cap < active_count:
                        raise ValueError(
                            f"Cannot set stage_cap to {new_cap} because there are {active_count} active leases."
                        )

                conn.execute(
                    "INSERT OR REPLACE INTO operator_policy (key, value, updated_at, updated_by) VALUES ('policy', ?, ?, ?)",
                    (json.dumps(current), now, principal.identity),
                )
                conn.commit()
                return current
            except Exception:
                conn.rollback()
                raise

    def add_to_cohort(
        self,
        issue_id: str,
        stage: int,
        sequence: int,
        principal: Principal,
        allow_update: bool = False,
    ) -> Dict[str, Any]:
        """Add a task to the admission cohort. Requires merge-factory-admin scope."""
        if not principal.has_scope("merge-factory-admin"):
            raise PermissionError("Only merge-factory-admin may modify cohorts.")

        if stage not in (1, 2, 3):
            raise ValueError("Cohort stage must be 1, 2, or 3.")

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                # Check for existing record
                cursor = conn.execute(
                    "SELECT * FROM admission_cohort WHERE issue_id = ?", (issue_id,)
                )
                existing = cursor.fetchone()
                if existing:
                    # Check for same-value replay (idempotent)
                    if existing["stage"] == stage and existing["sequence"] == sequence:
                        conn.commit()
                        return {
                            "issue_id": existing["issue_id"],
                            "stage": existing["stage"],
                            "status": existing["status"],
                            "sequence": existing["sequence"],
                            "added_at": existing["added_at"],
                            "updated_at": existing["updated_at"],
                        }

                    # Check for active lease
                    lease_cursor = conn.execute(
                        "SELECT * FROM concurrency_lease WHERE issue_id = ? AND expires_at > ?",
                        (issue_id, now),
                    )
                    active_lease = lease_cursor.fetchone()
                    if active_lease:
                        raise ValueError(
                            "Cannot modify cohort: issue has an active lease."
                        )

                    # Different values: update requires allow_update
                    if not allow_update:
                        raise ValueError(
                            "Issue already exists in cohort with different values. Use allow_update=True to update."
                        )

                    # Update and Audit
                    import logging

                    logger = logging.getLogger("prismatic.merge_factory")
                    logger.warning(
                        f"AUDIT: Admin {principal.identity} updated cohort for issue {issue_id}: "
                        f"stage {existing['stage']} -> {stage}, sequence {existing['sequence']} -> {sequence}"
                    )
                    print(
                        f"AUDIT: Admin {principal.identity} updated cohort for issue {issue_id}: "
                        f"stage {existing['stage']} -> {stage}, sequence {existing['sequence']} -> {sequence}"
                    )

                    conn.execute(
                        """
                        UPDATE admission_cohort
                        SET stage = ?, sequence = ?, updated_at = ?
                        WHERE issue_id = ?
                        """,
                        (stage, sequence, now, issue_id),
                    )
                    conn.commit()
                    return {
                        "issue_id": issue_id,
                        "stage": stage,
                        "status": existing["status"],
                        "sequence": sequence,
                        "added_at": existing["added_at"],
                        "updated_at": now,
                    }

                # Otherwise insert new cohort entry
                conn.execute(
                    """
                    INSERT INTO admission_cohort (issue_id, stage, status, sequence, added_at, updated_at)
                    VALUES (?, ?, 'PENDING', ?, ?, ?)
                    """,
                    (issue_id, stage, sequence, now, now),
                )
                conn.commit()
                return {
                    "issue_id": issue_id,
                    "stage": stage,
                    "status": "PENDING",
                    "sequence": sequence,
                    "added_at": now,
                    "updated_at": now,
                }
            except Exception:
                conn.rollback()
                raise

    def update_cohort_status(
        self, issue_id: str, status: str, principal: Principal
    ) -> Dict[str, Any]:
        """Update cohort status. Requires merge-factory-admin scope."""
        if not principal.has_scope("merge-factory-admin"):
            raise PermissionError("Only merge-factory-admin may modify cohorts.")

        allowed_statuses = {"PENDING", "ADMITTED", "COMPLETED", "EXCLUDED"}
        status_upper = status.upper()
        if status_upper not in allowed_statuses:
            raise ValueError(f"Invalid cohort status: {status_upper}")

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                cursor = conn.execute(
                    "SELECT * FROM admission_cohort WHERE issue_id = ?", (issue_id,)
                )
                row = cursor.fetchone()
                if not row:
                    raise KeyError(f"Cohort issue {issue_id} not found.")

                if row["status"] != status_upper:
                    # Check for active lease
                    lease_cursor = conn.execute(
                        "SELECT * FROM concurrency_lease WHERE issue_id = ? AND expires_at > ?",
                        (issue_id, now),
                    )
                    active_lease = lease_cursor.fetchone()
                    if active_lease:
                        raise ValueError(
                            "Cannot modify cohort status: issue has an active lease."
                        )

                conn.execute(
                    "UPDATE admission_cohort SET status = ?, updated_at = ? WHERE issue_id = ?",
                    (status_upper, now, issue_id),
                )
                conn.commit()
                ret = dict(row)
                ret["status"] = status_upper
                ret["updated_at"] = now
                return ret
            except Exception:
                conn.rollback()
                raise

    def get_cohort(self) -> List[Dict[str, Any]]:
        """List all admission cohort items, sorted by sequence."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM admission_cohort ORDER BY sequence ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # ── Event Idempotency
    # ─────────────────────────────────────────────────────────────────────────

    def is_event_processed(self, event_id: str) -> bool:
        """Check if an event ID has already been successfully processed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM processed_event WHERE event_id = ?", (event_id,)
            )
            return cursor.fetchone() is not None

    def record_event_processed(self, event_id: str) -> None:
        """Mark an event ID as processed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_event (event_id, processed_at) VALUES (?, ?)",
                (event_id, now),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # ── Concurrency Leases
    # ─────────────────────────────────────────────────────────────────────────

    def acquire_lease(
        self, issue_id: str, stage: int, ttl_seconds: int, principal: Principal
    ) -> Dict[str, Any]:
        """Atomically evaluate the global stage cap and acquire a lease.

        Enforces one global active producer cap. Stage is operator policy, not a lease partition.
        Fails closed when cron is paused unless the issue is in the active cohort.
        """
        if ttl_seconds <= 0:
            raise ValueError("TTL must be positive.")
        if ttl_seconds > MAX_LEASE_TTL_SECONDS:
            raise ValueError(
                f"Lease TTL exceeds maximum allowed ({MAX_LEASE_TTL_SECONDS}s)."
            )

        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()

        with self._connect() as conn:
            # Use EXCLUSIVE transaction for concurrency safety
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                # Retrieve the current policy inside the transaction
                cursor = conn.execute(
                    "SELECT value FROM operator_policy WHERE key = 'policy'"
                )
                policy_row = cursor.fetchone()
                if not policy_row:
                    policy = {"stage_cap": 1, "cron_paused": True}
                    conn.execute(
                        "INSERT INTO operator_policy (key, value, updated_at, updated_by) VALUES ('policy', ?, ?, 'system')",
                        (json.dumps(policy), now_str),
                    )
                else:
                    policy = json.loads(policy_row["value"])

                if policy.get("cron_paused"):
                    cursor = conn.execute(
                        "SELECT status FROM admission_cohort WHERE issue_id = ?",
                        (issue_id,),
                    )
                    row = cursor.fetchone()
                    if not row or row["status"] == "EXCLUDED":
                        raise PermissionError(
                            "Cron is paused and issue is not in the active cohort."
                        )

                # Retrieve cohort stage to derive the durable stage
                cursor = conn.execute(
                    "SELECT stage, status FROM admission_cohort WHERE issue_id = ?",
                    (issue_id,),
                )
                cohort_row = cursor.fetchone()
                if not cohort_row:
                    raise PermissionError("Issue not found in admission cohort.")
                if cohort_row["status"] == "EXCLUDED":
                    raise PermissionError("Issue is excluded from admission cohort.")

                cohort_stage = cohort_row["stage"]
                if cohort_stage not in (1, 2, 3):
                    raise ValueError(
                        f"Cohort stage must be 1, 2, or 3, got: {cohort_stage}"
                    )

                if stage != cohort_stage:
                    raise ValueError(
                        f"Stage mismatch: requested {stage}, but cohort stage is {cohort_stage}"
                    )

                # 1. Prune expired leases first
                conn.execute(
                    "DELETE FROM concurrency_lease WHERE expires_at < ?", (now_str,)
                )

                # 2. Check if a lease already exists for this issue
                cursor = conn.execute(
                    "SELECT * FROM concurrency_lease WHERE issue_id = ?", (issue_id,)
                )
                existing = cursor.fetchone()
                if existing:
                    raise BlockingIOError("Issue is locked under another active lease.")

                # 3. Check global cap
                cursor = conn.execute("SELECT COUNT(*) FROM concurrency_lease")
                count = cursor.fetchone()[0]
                stage_cap = policy.get("stage_cap", 1)

                if count >= stage_cap:
                    raise BlockingIOError(
                        f"Global lease capacity limit reached ({count}/{stage_cap})."
                    )

                # 4. Insert new lease
                lease_id = str(uuid.uuid4())
                expires_dt = now_dt + timedelta(seconds=ttl_seconds)
                expires_str = expires_dt.isoformat()
                conn.execute(
                    """
                    INSERT INTO concurrency_lease (lease_id, owner_id, issue_id, expires_at, heartbeat_at, stage, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease_id,
                        principal.identity,
                        issue_id,
                        expires_str,
                        now_str,
                        cohort_stage,
                        now_str,
                    ),
                )
                conn.commit()
                return {
                    "lease_id": lease_id,
                    "owner_id": principal.identity,
                    "issue_id": issue_id,
                    "expires_at": expires_str,
                    "heartbeat_at": now_str,
                    "stage": cohort_stage,
                    "created_at": now_str,
                }
            except Exception:
                conn.rollback()
                raise

    def heartbeat_lease(
        self, issue_id: str, lease_id: str, principal: Principal
    ) -> Dict[str, Any]:
        """Renew/heartbeat a lease owned by the principal."""
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                # 1. Prune expired
                conn.execute(
                    "DELETE FROM concurrency_lease WHERE expires_at < ?", (now_str,)
                )

                # 2. Find active lease for this issue, lease_id, and owner
                cursor = conn.execute(
                    "SELECT * FROM concurrency_lease WHERE issue_id = ? AND lease_id = ? AND owner_id = ?",
                    (issue_id, lease_id, principal.identity),
                )
                row = cursor.fetchone()
                if not row:
                    raise KeyError(
                        "Lease is expired or not held by this principal/lease_id."
                    )

                # Calculate new expiry (default renew 5 min)
                expires_dt = now_dt + timedelta(seconds=300)
                expires_str = expires_dt.isoformat()
                cursor = conn.execute(
                    "UPDATE concurrency_lease SET expires_at = ?, heartbeat_at = ? WHERE issue_id = ? AND lease_id = ?",
                    (expires_str, now_str, issue_id, lease_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(
                        "Failed to heartbeat lease: lease not found or ID mismatch."
                    )

                conn.commit()
                ret = dict(row)
                ret["expires_at"] = expires_str
                ret["heartbeat_at"] = now_str
                return ret
            except Exception:
                conn.rollback()
                raise

    def release_lease(self, issue_id: str, lease_id: str, principal: Principal) -> None:
        """Release a concurrency lease."""
        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                cursor = conn.execute(
                    "DELETE FROM concurrency_lease WHERE issue_id = ? AND lease_id = ? AND owner_id = ?",
                    (issue_id, lease_id, principal.identity),
                )
                if cursor.rowcount != 1:
                    raise KeyError(
                        "Failed to release lease: lease not found, expired, or ID mismatch."
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_active_leases(self) -> List[Dict[str, Any]]:
        """Return all active (non-expired) leases."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM concurrency_lease WHERE expires_at > ?", (now_str,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # ── George Merge-Judge Skeleton
    # ─────────────────────────────────────────────────────────────────────────

    def submit_attestation(
        self,
        issue_id: str,
        decision: str,
        base_sha: str,
        candidate_sha: str,
        manifest_digest: str,
        evidence_digest: str,
        repository: str,
        target: str,
        principal: Principal,
    ) -> Dict[str, Any]:
        """Append an immutable decision attestation record. Requires merge-judge scope."""
        if not principal.has_scope("merge-judge"):
            raise PermissionError(
                "Only an authenticated merge-judge may append attestation decisions."
            )

        allowed_decisions = {
            "APPROVE_MERGE",
            "REPAIR",
            "REJECT",
            "SUPERSEDED",
            "MANUAL_REVIEW",
        }
        dec_upper = decision.upper()
        if dec_upper not in allowed_decisions:
            raise ValueError(f"Invalid decision: {dec_upper}")

        attestation_id = "attest-" + str(uuid.uuid4())[:18]
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO merge_decision_history (
                    attestation_id, decision, issue_id, base_sha, candidate_sha,
                    manifest_digest, evidence_digest, repository, target, reviewer, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attestation_id,
                    dec_upper,
                    issue_id,
                    base_sha,
                    candidate_sha,
                    manifest_digest,
                    evidence_digest,
                    repository,
                    target,
                    principal.identity,
                    now,
                ),
            )

        return {
            "attestation_id": attestation_id,
            "decision": dec_upper,
            "issue_id": issue_id,
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "manifest_digest": manifest_digest,
            "evidence_digest": evidence_digest,
            "repository": repository,
            "target": target,
            "reviewer": principal.identity,
            "timestamp": now,
        }

    def validate_approval(
        self,
        issue_id: str,
        base_sha: str,
        candidate_sha: str,
        manifest_digest: str,
        evidence_digest: str,
        repository: str,
        target: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """Pure/read-only validation of the candidate's active approval.

        Returns validity and status. Does not require special scopes (open to ordinary callers).
        A mismatch in digests, head/base_sha, or latest state not being APPROVE_MERGE yields invalid.
        """
        close_conn = False
        if conn is None:
            conn = self._connect()
            close_conn = True
        try:
            cursor = conn.execute(
                """
                SELECT * FROM merge_decision_history
                WHERE issue_id = ? AND repository = ? AND target = ?
                ORDER BY id DESC LIMIT 1
                """,
                (issue_id, repository, target),
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "valid": False,
                    "status": "NO_RECORD",
                    "reason": "No attestation history exists for this issue/repo/target.",
                }

            decision = row["decision"]
            if decision != "APPROVE_MERGE":
                return {
                    "valid": False,
                    "status": decision,
                    "reason": f"Latest decision is not APPROVE_MERGE, but {decision}.",
                }

            # Check bindings
            mismatches = []
            if row["base_sha"] != base_sha:
                mismatches.append("base_sha")
            if row["candidate_sha"] != candidate_sha:
                mismatches.append("candidate_sha")
            if row["manifest_digest"] != manifest_digest:
                mismatches.append("manifest_digest")
            if row["evidence_digest"] != evidence_digest:
                mismatches.append("evidence_digest")

            if mismatches:
                return {
                    "valid": False,
                    "status": "MISMATCH",
                    "reason": f"Candidate bindings changed: mismatch in {', '.join(mismatches)}.",
                    "attestation_id": row["attestation_id"],
                }

            return {
                "valid": True,
                "status": "VALID",
                "attestation_id": row["attestation_id"],
                "reviewer": row["reviewer"],
                "timestamp": row["timestamp"],
            }
        finally:
            if close_conn:
                conn.close()

    def get_decision_history(self, issue_id: str) -> List[Dict[str, Any]]:
        """Get history of decisions for an issue, ordered by timestamp ascending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM merge_decision_history WHERE issue_id = ? ORDER BY id ASC",
                (issue_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # ── Merge Locks
    # ─────────────────────────────────────────────────────────────────────────

    def acquire_lock(
        self,
        repository: str,
        target: str,
        issue_id: str,
        base_sha: str,
        candidate_sha: str,
        manifest_digest: str,
        evidence_digest: str,
        approval_attestation_id: str,
        ttl_seconds: int,
        principal: Principal,
    ) -> Dict[str, Any]:
        """Acquire a repository + target merge lock.

        Requires a valid matching exact-candidate approval attestation.
        Locks are owned by the principal.identity.
        """
        if ttl_seconds <= 0:
            raise ValueError("TTL must be positive.")
        if ttl_seconds > MAX_LOCK_TTL_SECONDS:
            raise ValueError(
                f"Lock TTL exceeds maximum allowed ({MAX_LOCK_TTL_SECONDS}s)."
            )
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        lock_id = f"{repository}:{target}"

        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                # 1. Validate the matching exact-candidate approval inside the transaction
                approval = self.validate_approval(
                    issue_id=issue_id,
                    base_sha=base_sha,
                    candidate_sha=candidate_sha,
                    manifest_digest=manifest_digest,
                    evidence_digest=evidence_digest,
                    repository=repository,
                    target=target,
                    conn=conn,
                )
                if (
                    not approval["valid"]
                    or approval.get("attestation_id") != approval_attestation_id
                ):
                    raise PermissionError(
                        f"Cannot lock without valid matching exact-candidate approval. Reason: {approval.get('reason', 'Attestation ID mismatch')}"
                    )

                # Prune expired locks
                conn.execute("DELETE FROM merge_lock WHERE expires_at < ?", (now_str,))

                # Check if a lock already exists for this repository/target
                cursor = conn.execute(
                    "SELECT * FROM merge_lock WHERE lock_id = ?", (lock_id,)
                )
                existing = cursor.fetchone()
                if existing:
                    # Check owner identity and matching issue/bindings
                    if (
                        existing["owner_principal"] == principal.identity
                        and existing["issue_id"] == issue_id
                    ):
                        # Verify bindings haven't changed (rebase, conflict, etc.)
                        # If base_sha, candidate_sha, or digests changed, the lock is invalidated and cannot be renewed!
                        if (
                            existing["base_sha"] != base_sha
                            or existing["candidate_sha"] != candidate_sha
                            or existing["manifest_digest"] != manifest_digest
                            or existing["evidence_digest"] != evidence_digest
                        ):
                            # Delete lock (transition to invalid/released)
                            conn.execute(
                                "DELETE FROM merge_lock WHERE lock_id = ?", (lock_id,)
                            )
                            raise ValueError(
                                "Lock bindings mismatched; existing lock invalidated and released."
                            )

                        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
                        expires_str = expires_dt.isoformat()
                        conn.execute(
                            "UPDATE merge_lock SET expires_at = ?, updated_at = ? WHERE lock_id = ?",
                            (expires_str, now_str, lock_id),
                        )
                        conn.commit()
                        ret = dict(existing)
                        ret["expires_at"] = expires_str
                        ret["updated_at"] = now_str
                        return ret
                    else:
                        raise BlockingIOError(
                            "Target repository is locked under another active operation."
                        )

                # Insert new lock
                expires_dt = now_dt + timedelta(seconds=ttl_seconds)
                expires_str = expires_dt.isoformat()
                conn.execute(
                    """
                    INSERT INTO merge_lock (
                        lock_id, repository, target, issue_id, base_sha, candidate_sha,
                        manifest_digest, evidence_digest, approval_attestation_id,
                        owner_principal, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lock_id,
                        repository,
                        target,
                        issue_id,
                        base_sha,
                        candidate_sha,
                        manifest_digest,
                        evidence_digest,
                        approval_attestation_id,
                        principal.identity,
                        expires_str,
                        now_str,
                        now_str,
                    ),
                )
                conn.commit()
                return {
                    "lock_id": lock_id,
                    "repository": repository,
                    "target": target,
                    "issue_id": issue_id,
                    "base_sha": base_sha,
                    "candidate_sha": candidate_sha,
                    "manifest_digest": manifest_digest,
                    "evidence_digest": evidence_digest,
                    "approval_attestation_id": approval_attestation_id,
                    "owner_principal": principal.identity,
                    "expires_at": expires_str,
                    "created_at": now_str,
                    "updated_at": now_str,
                }
            except Exception:
                conn.rollback()
                raise

    def heartbeat_lock(
        self,
        repository: str,
        target: str,
        issue_id: str,
        base_sha: str,
        candidate_sha: str,
        manifest_digest: str,
        evidence_digest: str,
        approval_attestation_id: str,
        principal: Principal,
    ) -> Dict[str, Any]:
        """Extend lock TTL. Requires same principal identity and valid approval bindings."""
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        lock_id = f"{repository}:{target}"

        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                # Find active lock
                cursor = conn.execute(
                    "SELECT * FROM merge_lock WHERE lock_id = ?", (lock_id,)
                )
                row = cursor.fetchone()
                if not row:
                    raise KeyError("Lock is expired or does not exist.")

                # Verify owner identity
                if (
                    row["owner_principal"] != principal.identity
                    or row["issue_id"] != issue_id
                ):
                    raise PermissionError("Lock is held by another principal or issue.")

                # Verify bindings and approval
                approval = self.validate_approval(
                    issue_id=issue_id,
                    base_sha=base_sha,
                    candidate_sha=candidate_sha,
                    manifest_digest=manifest_digest,
                    evidence_digest=evidence_digest,
                    repository=repository,
                    target=target,
                    conn=conn,
                )
                if (
                    not approval["valid"]
                    or approval.get("attestation_id") != approval_attestation_id
                    or row["base_sha"] != base_sha
                    or row["candidate_sha"] != candidate_sha
                    or row["manifest_digest"] != manifest_digest
                    or row["evidence_digest"] != evidence_digest
                ):
                    # Invalidate lock by deleting it
                    conn.execute("DELETE FROM merge_lock WHERE lock_id = ?", (lock_id,))
                    conn.commit()
                    raise ValueError(
                        "Lock bindings or approval invalidated; lock automatically released."
                    )

                expires_dt = now_dt + timedelta(seconds=300)
                expires_str = expires_dt.isoformat()
                conn.execute(
                    "UPDATE merge_lock SET expires_at = ?, updated_at = ? WHERE lock_id = ?",
                    (expires_str, now_str, lock_id),
                )
                conn.commit()
                ret = dict(row)
                ret["expires_at"] = expires_str
                ret["updated_at"] = now_str
                return ret
            except Exception:
                conn.rollback()
                raise

    def release_lock(
        self,
        repository: str,
        target: str,
        issue_id: str,
        principal: Principal,
    ) -> None:
        """Release merge lock if owned by the principal."""
        lock_id = f"{repository}:{target}"
        with self._connect() as conn:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            try:
                conn.execute(
                    "DELETE FROM merge_lock WHERE lock_id = ? AND owner_principal = ? AND issue_id = ?",
                    (lock_id, principal.identity, issue_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_active_locks(self) -> List[Dict[str, Any]]:
        """Return all active (non-expired) merge locks."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM merge_lock WHERE expires_at > ?", (now_str,)
            ).fetchall()
            return [dict(r) for r in rows]
