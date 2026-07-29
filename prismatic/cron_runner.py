"""Canonical cron runner authority core and orchestration facade (GRO-4317 / CRONRUNNER-1).

This module implements:
- Normalized trigger envelope validation and canonical serialization.
- Immutable registry snapshot value object and validation.
- Bounded process adapter interface boundary (zero subprocess/Popen/fork sites).
- Transaction-safe operational APIs (submit/converge, gate, claim, pre-spawn revalidate, finalize, reconcile).
- Catch-up bucket selection semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from prismatic.cron_authority import (
    CronAuthorityError,
    connect_cron_authority,
    migrate_cron_authority,
)
from prismatic.cron_receipts.schema import CronRunReceipt

MAX_REPLAY_BUCKETS_LIMIT: int = 100

VALID_TRIGGER_KINDS: frozenset[str] = frozenset(
    {"scheduled", "manual", "retry", "hook", "recovery"}
)
VALID_TRANSPORT_KINDS: frozenset[str] = frozenset(
    {"http", "hook", "recovery", "internal"}
)
VALID_SNAPSHOT_STATES: frozenset[str] = frozenset(
    {"active", "paused", "deactivated", "deleted"}
)
VALID_CATCH_UP_POLICIES: frozenset[str] = frozenset(
    {"skip", "run_once", "bounded_replay"}
)

_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HEX40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_ROOT_PATTERN = re.compile(
    r"^/home/ubuntu/\.prismatic/releases/([0-9a-f]{40})(?:/.*)?$"
)
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


def _is_valid_utc_timestamp(val: str) -> bool:
    if not isinstance(val, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(val) is None:
        return False
    try:
        dt = datetime.fromisoformat(val[:-1] + "+00:00")
        return dt.tzinfo is not None and dt.utcoffset() == timezone.utc.utcoffset(dt)
    except ValueError:
        return False


def _get_utc_now() -> str:
    now_utc = datetime.now(timezone.utc).isoformat()
    if now_utc.endswith("+00:00"):
        now_utc = now_utc[:-6] + "Z"
    return now_utc


def compute_command_digest(argv: tuple[str, ...], cwd: str) -> str:
    """Compute canonical 64-hex SHA-256 command digest from argv and cwd."""
    data = {"argv": list(argv), "cwd": cwd}
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical_json).hexdigest()


@dataclass(frozen=True)
class CronTriggerEnvelope:
    """Normalized, frozen trigger envelope."""

    trigger_event_id: str
    trigger_kind: str
    transport_kind: str
    cron_id: str
    registry_generation: int
    schedule_bucket: str
    command_digest: str
    release_digest: str
    submitted_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.trigger_event_id, str) or not (
            1 <= len(self.trigger_event_id) <= 128
        ):
            raise CronAuthorityError(
                "trigger_event_id must be 1..128 chars", code="invalid_envelope"
            )
        if any(ord(c) < 32 or ord(c) == 127 for c in self.trigger_event_id):
            raise CronAuthorityError(
                "trigger_event_id cannot contain control characters",
                code="invalid_envelope",
            )

        if self.trigger_kind not in VALID_TRIGGER_KINDS:
            raise CronAuthorityError(
                f"Invalid trigger_kind: {self.trigger_kind!r}", code="invalid_envelope"
            )
        if self.transport_kind not in VALID_TRANSPORT_KINDS:
            raise CronAuthorityError(
                f"Invalid transport_kind: {self.transport_kind!r}",
                code="invalid_envelope",
            )

        if not isinstance(self.cron_id, str) or not (1 <= len(self.cron_id) <= 128):
            raise CronAuthorityError(
                "cron_id must be 1..128 chars", code="invalid_envelope"
            )

        if type(self.registry_generation) is not int or self.registry_generation < 1:
            raise CronAuthorityError(
                "registry_generation must be int >= 1", code="invalid_envelope"
            )

        if not _is_valid_utc_timestamp(self.schedule_bucket):
            raise CronAuthorityError(
                f"Invalid schedule_bucket: {self.schedule_bucket!r}",
                code="invalid_envelope",
            )
        if not _is_valid_utc_timestamp(self.submitted_at):
            raise CronAuthorityError(
                f"Invalid submitted_at: {self.submitted_at!r}", code="invalid_envelope"
            )

        if (
            not isinstance(self.command_digest, str)
            or _HEX64_PATTERN.fullmatch(self.command_digest) is None
        ):
            raise CronAuthorityError(
                f"Invalid command_digest: {self.command_digest!r}",
                code="invalid_envelope",
            )
        if (
            not isinstance(self.release_digest, str)
            or _HEX64_PATTERN.fullmatch(self.release_digest) is None
        ):
            raise CronAuthorityError(
                f"Invalid release_digest: {self.release_digest!r}",
                code="invalid_envelope",
            )

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "command_digest": self.command_digest,
            "cron_id": self.cron_id,
            "registry_generation": self.registry_generation,
            "release_digest": self.release_digest,
            "schedule_bucket": self.schedule_bucket,
            "submitted_at": self.submitted_at,
            "transport_kind": self.transport_kind,
            "trigger_event_id": self.trigger_event_id,
            "trigger_kind": self.trigger_kind,
        }

    def to_canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_canonical_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def trigger_digest(self) -> str:
        return hashlib.sha256(self.to_canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class CronDependency:
    """Immutable dependency specification."""

    cron_id: str
    schedule_bucket: str
    required_outcome: str = "succeeded"

    def __post_init__(self) -> None:
        if not isinstance(self.cron_id, str) or not (1 <= len(self.cron_id) <= 128):
            raise CronAuthorityError(
                "cron_id must be 1..128 chars", code="invalid_dependency"
            )
        if not _is_valid_utc_timestamp(self.schedule_bucket):
            raise CronAuthorityError(
                f"Invalid schedule_bucket: {self.schedule_bucket!r}",
                code="invalid_dependency",
            )


@dataclass(frozen=True)
class CronRegistrySnapshot:
    """Immutable registry snapshot value object."""

    cron_id: str
    registry_generation: int
    command_digest: str
    release_digest: str
    argv: tuple[str, ...]
    cwd: str
    state: str
    depends_on: tuple[CronDependency, ...] = ()
    catch_up_policy: str = "run_once"
    max_replay_buckets: int = 10
    source_id: str = "prismatic.cron-authority.sqlite/cron_registry_snapshots_v1"
    schema_id: str = "prismatic.cron.registry-snapshot"
    schema_version: int = 1
    trusted_runner_identity: str = "runner_default"
    dependency_digest: str = "0" * 64
    release_root: str = ""
    release_root_evidence: dict[str, Any] = field(default_factory=dict)
    executable_evidence: dict[str, Any] = field(default_factory=dict)
    cwd_evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.cron_id, str) or not (1 <= len(self.cron_id) <= 128):
            raise CronAuthorityError(
                "cron_id must be 1..128 chars", code="invalid_snapshot"
            )

        if type(self.registry_generation) is not int or self.registry_generation < 1:
            raise CronAuthorityError(
                "registry_generation must be int >= 1", code="invalid_snapshot"
            )

        if (
            not isinstance(self.command_digest, str)
            or _HEX64_PATTERN.fullmatch(self.command_digest) is None
        ):
            raise CronAuthorityError("Invalid command_digest", code="invalid_snapshot")
        if (
            not isinstance(self.release_digest, str)
            or _HEX64_PATTERN.fullmatch(self.release_digest) is None
        ):
            raise CronAuthorityError("Invalid release_digest", code="invalid_snapshot")

        if not isinstance(self.argv, tuple) or len(self.argv) == 0:
            raise CronAuthorityError(
                "argv must be a non-empty tuple of strings", code="invalid_snapshot"
            )
        for arg in self.argv:
            if not isinstance(arg, str) or not arg:
                raise CronAuthorityError(
                    "argv elements must be non-empty strings", code="invalid_snapshot"
                )

        m = _RELEASE_ROOT_PATTERN.fullmatch(self.cwd)
        if not m:
            raise CronAuthorityError(
                f"cwd must be under verified immutable release root /home/ubuntu/.prismatic/releases/<40-hex>/: {self.cwd!r}",
                code="invalid_snapshot",
            )
        if ".." in self.cwd or "/latest" in self.cwd or "//" in self.cwd:
            raise CronAuthorityError(
                f"cwd contains relative path or alias: {self.cwd!r}",
                code="invalid_snapshot",
            )

        if self.state not in VALID_SNAPSHOT_STATES:
            raise CronAuthorityError(
                f"Invalid snapshot state: {self.state!r}", code="invalid_snapshot"
            )

        if self.catch_up_policy not in VALID_CATCH_UP_POLICIES:
            raise CronAuthorityError(
                f"Invalid catch_up_policy: {self.catch_up_policy!r}",
                code="invalid_snapshot",
            )

        if (
            type(self.max_replay_buckets) is not int
            or isinstance(self.max_replay_buckets, bool)
            or self.max_replay_buckets < 1
            or self.max_replay_buckets > MAX_REPLAY_BUCKETS_LIMIT
        ):
            raise CronAuthorityError(
                f"max_replay_buckets must be int between 1 and {MAX_REPLAY_BUCKETS_LIMIT}",
                code="invalid_snapshot",
            )

        # Validate depends_on
        if not isinstance(self.depends_on, tuple):
            raise CronAuthorityError(
                "depends_on must be a tuple", code="invalid_snapshot"
            )
        if len(self.depends_on) > 100:
            raise CronAuthorityError(
                "Excessive dependency count (>100)", code="invalid_snapshot"
            )
        seen_deps: set[tuple[str, str]] = set()
        for dep in self.depends_on:
            if not isinstance(dep, CronDependency):
                raise CronAuthorityError(
                    "depends_on elements must be CronDependency instances",
                    code="invalid_snapshot",
                )
            if dep.cron_id == self.cron_id:
                raise CronAuthorityError(
                    f"Self-dependency rejected: {self.cron_id}", code="invalid_snapshot"
                )
            key = (dep.cron_id, dep.schedule_bucket)
            if key in seen_deps:
                raise CronAuthorityError(
                    f"Duplicate dependency rejected: {key}", code="invalid_snapshot"
                )
            seen_deps.add(key)

        # Verify command digest matches computed digest
        recomputed = compute_command_digest(self.argv, self.cwd)
        if recomputed != self.command_digest:
            raise CronAuthorityError(
                f"command_digest mismatch: expected recomputed {recomputed}, got {self.command_digest}",
                code="command_digest_mismatch",
            )


@dataclass(frozen=True)
class AdapterResult:
    """Structured result returned by a process adapter."""

    exit_code: int
    stdout: str
    stderr: str
    outcome: str = "succeeded"
    error_classification: str | None = None


class BoundedProcessAdapter(Protocol):
    """Callable protocol for process adapters."""

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: str,
        execution_id: str,
        attempt: int,
        fence_token: int,
        runner_id: str,
        runner_release_digest: str,
    ) -> AdapterResult: ...


def select_catch_up_buckets(
    schedule_buckets: list[str],
    current_bucket: str,
    catch_up_policy: str,
    max_replay_buckets: int,
    last_cursor_bucket: str | None = None,
) -> list[str]:
    """Pure bounded selection of catch-up schedule buckets."""
    if (
        type(max_replay_buckets) is not int
        or isinstance(max_replay_buckets, bool)
        or not (1 <= max_replay_buckets <= MAX_REPLAY_BUCKETS_LIMIT)
    ):
        raise CronAuthorityError(
            f"max_replay_buckets must be a non-boolean integer between 1 and {MAX_REPLAY_BUCKETS_LIMIT}",
            code="invalid_max_replay_buckets",
        )

    if catch_up_policy not in VALID_CATCH_UP_POLICIES:
        raise CronAuthorityError(
            f"Invalid catch_up_policy: {catch_up_policy!r}",
            code="invalid_catch_up_policy",
        )

    if not _is_valid_utc_timestamp(current_bucket):
        raise CronAuthorityError(
            f"Invalid current_bucket: {current_bucket!r}",
            code="invalid_schedule_bucket",
        )

    for i in range(len(schedule_buckets)):
        b = schedule_buckets[i]
        if not _is_valid_utc_timestamp(b):
            raise CronAuthorityError(
                f"Invalid bucket in schedule_buckets: {b!r}",
                code="invalid_schedule_bucket",
            )
        if i > 0 and schedule_buckets[i] <= schedule_buckets[i - 1]:
            raise CronAuthorityError(
                "schedule_buckets must be strictly ascending",
                code="invalid_schedule_bucket",
            )

    if last_cursor_bucket is not None and not _is_valid_utc_timestamp(
        last_cursor_bucket
    ):
        raise CronAuthorityError(
            f"Invalid last_cursor_bucket: {last_cursor_bucket!r}",
            code="invalid_schedule_bucket",
        )

    # Filter eligible missed buckets
    missed: list[str] = []
    for b in schedule_buckets:
        if b > current_bucket:
            raise CronAuthorityError(
                f"Future schedule_bucket rejected: {b} > {current_bucket}",
                code="future_bucket_rejected",
            )
        if last_cursor_bucket is not None and b <= last_cursor_bucket:
            continue
        missed.append(b)

    if not missed:
        return []

    if catch_up_policy == "skip":
        return []
    elif catch_up_policy == "run_once":
        return [missed[-1]]
    elif catch_up_policy == "bounded_replay":
        cap = min(max_replay_buckets, MAX_REPLAY_BUCKETS_LIMIT)
        return missed[:cap]
    return []


def _validate_runner_binding(runner_id: str, runner_release_digest: str) -> None:
    if not isinstance(runner_id, str) or not (1 <= len(runner_id) <= 128):
        raise CronAuthorityError(
            "runner_id must be 1..128 chars", code="invalid_runner_binding"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in runner_id):
        raise CronAuthorityError(
            "runner_id cannot contain control characters", code="invalid_runner_binding"
        )
    if (
        not isinstance(runner_release_digest, str)
        or _HEX64_PATTERN.fullmatch(runner_release_digest) is None
    ):
        raise CronAuthorityError(
            "runner_release_digest must be 64 lowercase hex",
            code="invalid_runner_binding",
        )


def _are_dependencies_satisfied(
    cursor: sqlite3.Cursor, snapshot: CronRegistrySnapshot
) -> tuple[bool, str | None]:
    for dep in snapshot.depends_on:
        row = cursor.execute(
            """
            SELECT receipt.outcome
            FROM main.cron_receipts AS receipt
            JOIN main.cron_execution_aggregates AS aggregate
              ON aggregate.execution_id = receipt.execution_id
            WHERE aggregate.cron_id = ?
              AND aggregate.schedule_bucket = ?
              AND receipt.outcome = ?;
            """,
            (dep.cron_id, dep.schedule_bucket, dep.required_outcome),
        ).fetchone()
        if row is None:
            return (
                False,
                f"Unsatisfied dependency on cron_id={dep.cron_id}, bucket={dep.schedule_bucket}",
            )
    return True, None


def run_once(
    *,
    envelope: CronTriggerEnvelope,
    snapshot: CronRegistrySnapshot,
    runner_id: str,
    runner_release_digest: str,
    adapter: BoundedProcessAdapter,
    db_target: Any,
    timeout: float = 30.0,
    pre_spawn_hook: Any | None = None,
) -> dict[str, Any]:
    """Execute bounded run_once workflow against the cron authority."""
    _validate_runner_binding(runner_id, runner_release_digest)

    if envelope.cron_id != snapshot.cron_id:
        raise CronAuthorityError(
            "Envelope and snapshot cron_id mismatch", code="cross_binding_mismatch"
        )
    if envelope.registry_generation != snapshot.registry_generation:
        raise CronAuthorityError(
            "Envelope and snapshot registry_generation mismatch",
            code="cross_binding_mismatch",
        )
    if envelope.command_digest != snapshot.command_digest:
        raise CronAuthorityError(
            "Envelope and snapshot command_digest mismatch",
            code="cross_binding_mismatch",
        )
    if envelope.release_digest != snapshot.release_digest:
        raise CronAuthorityError(
            "Envelope and snapshot release_digest mismatch",
            code="cross_binding_mismatch",
        )

    # Migrate target database
    migrate_cron_authority(db_target, timeout=timeout)
    conn = connect_cron_authority(db_target, timeout=timeout)
    close_conn = not hasattr(db_target, "cursor")

    try:
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.cursor()

        # Step 1: Deliver trigger envelope & resolve aggregate
        trigger_bytes = envelope.to_canonical_bytes()
        trig_digest = envelope.trigger_digest()

        # Check existing delivery
        row_del = cursor.execute(
            "SELECT disposition, reason_code, execution_id, canonical_bytes FROM main.cron_trigger_deliveries WHERE trigger_event_id = ?;",
            (envelope.trigger_event_id,),
        ).fetchone()

        if row_del is not None:
            disp, reason, existing_exec_id, existing_bytes = row_del
            if bytes(existing_bytes) != trigger_bytes:
                raise CronAuthorityError(
                    f"Same trigger_event_id collision with changed payload: {envelope.trigger_event_id}",
                    code="trigger_id_collision",
                )
            if disp == "rejected":
                conn.commit()
                return {
                    "disposition": "rejected",
                    "reason_code": reason,
                    "execution_id": None,
                    "receipt": None,
                    "adapter_called": False,
                }
            execution_id = existing_exec_id
        else:
            # Check existing aggregate by uniqueness tuple
            row_agg = cursor.execute(
                """
                SELECT execution_id, release_digest FROM main.cron_execution_aggregates
                WHERE cron_id = ? AND registry_generation = ? AND schedule_bucket = ? AND command_digest = ?;
                """,
                (
                    envelope.cron_id,
                    envelope.registry_generation,
                    envelope.schedule_bucket,
                    envelope.command_digest,
                ),
            ).fetchone()

            now_utc = _get_utc_now()
            if row_agg is not None:
                agg_exec_id, agg_rel_digest = row_agg
                if agg_rel_digest != envelope.release_digest:
                    # Record rejected delivery evidence
                    cursor.execute(
                        """
                        INSERT INTO main.cron_trigger_deliveries (
                            trigger_event_id, trigger_digest, canonical_bytes, trigger_kind, transport_kind,
                            cron_id, registry_generation, schedule_bucket, command_digest, release_digest,
                            execution_id, disposition, reason_code, submitted_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'rejected', 'conflicting_release_digest', ?, ?);
                        """,
                        (
                            envelope.trigger_event_id,
                            trig_digest,
                            trigger_bytes,
                            envelope.trigger_kind,
                            envelope.transport_kind,
                            envelope.cron_id,
                            envelope.registry_generation,
                            envelope.schedule_bucket,
                            envelope.command_digest,
                            envelope.release_digest,
                            envelope.submitted_at,
                            now_utc,
                        ),
                    )
                    conn.commit()
                    return {
                        "disposition": "rejected",
                        "reason_code": "conflicting_release_digest",
                        "execution_id": None,
                        "receipt": None,
                        "adapter_called": False,
                    }

                execution_id = agg_exec_id
                cursor.execute(
                    """
                    INSERT INTO main.cron_trigger_deliveries (
                        trigger_event_id, trigger_digest, canonical_bytes, trigger_kind, transport_kind,
                        cron_id, registry_generation, schedule_bucket, command_digest, release_digest,
                        execution_id, disposition, reason_code, submitted_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'converged', 'duplicate_tuple', ?, ?);
                    """,
                    (
                        envelope.trigger_event_id,
                        trig_digest,
                        trigger_bytes,
                        envelope.trigger_kind,
                        envelope.transport_kind,
                        envelope.cron_id,
                        envelope.registry_generation,
                        envelope.schedule_bucket,
                        envelope.command_digest,
                        envelope.release_digest,
                        execution_id,
                        envelope.submitted_at,
                        now_utc,
                    ),
                )
            else:
                # Create aggregate and attempt 1
                agg_hash = hashlib.sha256(
                    f"{envelope.cron_id}:{envelope.registry_generation}:{envelope.schedule_bucket}:{envelope.command_digest}".encode()
                ).hexdigest()
                execution_id = f"exec_{agg_hash[:32]}"

                cursor.execute(
                    """
                    INSERT INTO main.cron_execution_aggregates (
                        execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        execution_id,
                        envelope.cron_id,
                        envelope.registry_generation,
                        envelope.schedule_bucket,
                        envelope.command_digest,
                        envelope.release_digest,
                        now_utc,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO main.cron_execution_attempts (
                        execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at
                    ) VALUES (?, 1, 'admitted', NULL, NULL, NULL, ?, ?);
                    """,
                    (execution_id, now_utc, now_utc),
                )
                cursor.execute(
                    """
                    INSERT INTO main.cron_trigger_deliveries (
                        trigger_event_id, trigger_digest, canonical_bytes, trigger_kind, transport_kind,
                        cron_id, registry_generation, schedule_bucket, command_digest, release_digest,
                        execution_id, disposition, reason_code, submitted_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', 'admitted', ?, ?);
                    """,
                    (
                        envelope.trigger_event_id,
                        trig_digest,
                        trigger_bytes,
                        envelope.trigger_kind,
                        envelope.transport_kind,
                        envelope.cron_id,
                        envelope.registry_generation,
                        envelope.schedule_bucket,
                        envelope.command_digest,
                        envelope.release_digest,
                        execution_id,
                        envelope.submitted_at,
                        now_utc,
                    ),
                )

        # Step 2: Check if attempt 1 is already terminal
        row_att = cursor.execute(
            "SELECT state, runner_id, fence_token FROM main.cron_execution_attempts WHERE execution_id = ? AND attempt = 1;",
            (execution_id,),
        ).fetchone()
        if row_att is not None and row_att[0] == "terminal":
            row_rec = cursor.execute(
                "SELECT receipt_id, outcome, runner_id, runner_release_digest FROM main.cron_receipts WHERE execution_id = ? AND attempt = 1;",
                (execution_id,),
            ).fetchone()
            conn.commit()
            return {
                "disposition": "converged" if row_del is not None else "accepted",
                "reason_code": "already_terminal",
                "execution_id": execution_id,
                "receipt_id": row_rec[0] if row_rec else None,
                "outcome": row_rec[1] if row_rec else None,
                "adapter_called": False,
            }

        # Step 3: Pre-claim Gating (§7.2, §9, §10)
        deps_satisfied, dep_reason = _are_dependencies_satisfied(cursor, snapshot)
        gated = False
        gated_reason = None
        if snapshot.state != "active":
            gated = True
            gated_reason = f"cron_state_{snapshot.state}"
        elif not deps_satisfied:
            gated = True
            gated_reason = dep_reason or "unsatisfied_dependency"

        if gated:
            now_utc = _get_utc_now()
            ev_data = json.dumps(
                {"gated_reason": gated_reason, "cron_id": snapshot.cron_id}
            ).encode("utf-8")
            ev_digest = hashlib.sha256(ev_data).hexdigest()

            cursor.execute(
                "INSERT OR IGNORE INTO main.cron_evidence (evidence_digest, canonical_bytes, created_at) VALUES (?, ?, ?);",
                (ev_digest, ev_data, now_utc),
            )
            # Record trusted runner_id as terminalizer identity with NULL fence/lease
            cursor.execute(
                """
                UPDATE main.cron_execution_attempts
                SET runner_id = ?, updated_at = ?
                WHERE execution_id = ? AND attempt = 1 AND state = 'admitted';
                """,
                (runner_id, now_utc, execution_id),
            )
            receipt_id = f"rcpt_{execution_id}_1"
            rcpt = CronRunReceipt(
                receipt_id=receipt_id,
                cron_id=envelope.cron_id,
                execution_id=execution_id,
                outcome="blocked",
                attempt=1,
                runner_id=runner_id,
                runner_release_digest=runner_release_digest,
                started_at=now_utc,
                finished_at=now_utc,
                error_classification=gated_reason,
                evidence_digest=ev_digest,
                signing_key_id="unsigned",
                signature="none",
            )
            cursor.execute(
                """
                INSERT INTO main.cron_receipts (
                    receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                    started_at, finished_at, error_classification, evidence_digest, signing_key_id, signature,
                    schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
                """,
                (
                    rcpt.receipt_id,
                    rcpt.execution_id,
                    rcpt.attempt,
                    rcpt.cron_id,
                    rcpt.outcome,
                    rcpt.runner_id,
                    rcpt.runner_release_digest,
                    rcpt.started_at,
                    rcpt.finished_at,
                    rcpt.error_classification,
                    rcpt.evidence_digest,
                    rcpt.signing_key_id,
                    rcpt.signature,
                    now_utc,
                ),
            )
            conn.commit()
            return {
                "disposition": "accepted",
                "reason_code": gated_reason,
                "execution_id": execution_id,
                "receipt_id": receipt_id,
                "outcome": "blocked",
                "adapter_called": False,
            }

        # Step 4: Claim attempt 1 (§7.3)
        now_dt = datetime.now(timezone.utc)
        now_utc = _get_utc_now()
        lease_expires_dt = datetime.fromtimestamp(
            now_dt.timestamp() + 30.0, timezone.utc
        )
        lease_expires_at = lease_expires_dt.isoformat()
        if lease_expires_at.endswith("+00:00"):
            lease_expires_at = lease_expires_at[:-6] + "Z"

        fence_token = 1
        res_claim = cursor.execute(
            """
            UPDATE main.cron_execution_attempts
            SET state = 'claimed', runner_id = ?, fence_token = ?, lease_expires_at = ?, updated_at = ?
            WHERE execution_id = ? AND attempt = 1 AND state = 'admitted';
            """,
            (runner_id, fence_token, lease_expires_at, now_utc, execution_id),
        )
        if res_claim.rowcount == 0:
            # Race condition on claim
            row_rec = cursor.execute(
                "SELECT receipt_id, outcome FROM main.cron_receipts WHERE execution_id = ? AND attempt = 1;",
                (execution_id,),
            ).fetchone()
            conn.commit()
            return {
                "disposition": "converged",
                "reason_code": "claim_contention",
                "execution_id": execution_id,
                "receipt_id": row_rec[0] if row_rec else None,
                "outcome": row_rec[1] if row_rec else None,
                "adapter_called": False,
            }

        # Step 5: Pre-spawn revalidation (§7.5)
        if pre_spawn_hook is not None:
            pre_spawn_hook(conn, execution_id, 1, snapshot)

        # Revalidate state and ownership
        row_reval = cursor.execute(
            "SELECT state, runner_id, fence_token FROM main.cron_execution_attempts WHERE execution_id = ? AND attempt = 1;",
            (execution_id,),
        ).fetchone()
        if (
            row_reval is None
            or row_reval[0] != "claimed"
            or row_reval[1] != runner_id
            or row_reval[2] != fence_token
        ):
            conn.rollback()
            return {
                "disposition": "rejected",
                "reason_code": "pre_spawn_revalidation_failed",
                "execution_id": execution_id,
                "receipt": None,
                "adapter_called": False,
            }

        if snapshot.state != "active":
            conn.rollback()
            return {
                "disposition": "rejected",
                "reason_code": "pre_spawn_state_revalidation_failed",
                "execution_id": execution_id,
                "receipt": None,
                "adapter_called": False,
            }

        row_agg_reval = cursor.execute(
            "SELECT registry_generation, command_digest, release_digest FROM main.cron_execution_aggregates WHERE execution_id = ?;",
            (execution_id,),
        ).fetchone()
        if (
            row_agg_reval is None
            or row_agg_reval[0] != snapshot.registry_generation
            or row_agg_reval[1] != snapshot.command_digest
            or row_agg_reval[2] != snapshot.release_digest
        ):
            conn.rollback()
            return {
                "disposition": "rejected",
                "reason_code": "pre_spawn_aggregate_revalidation_failed",
                "execution_id": execution_id,
                "receipt": None,
                "adapter_called": False,
            }

        deps_ok, _ = _are_dependencies_satisfied(cursor, snapshot)
        if not deps_ok:
            conn.rollback()
            return {
                "disposition": "rejected",
                "reason_code": "pre_spawn_dependency_revalidation_failed",
                "execution_id": execution_id,
                "receipt": None,
                "adapter_called": False,
            }

        cursor.execute(
            """
            UPDATE main.cron_execution_attempts
            SET state = 'running', updated_at = ?
            WHERE execution_id = ? AND attempt = 1 AND runner_id = ? AND fence_token = ? AND state = 'claimed';
            """,
            (_get_utc_now(), execution_id, runner_id, fence_token),
        )

        # Commit claim & state transition before adapter invocation
        conn.commit()

        # Step 6: Process adapter invocation (§8)
        started_at = _get_utc_now()
        adapter_res = adapter(
            snapshot.argv,
            snapshot.cwd,
            execution_id,
            1,
            fence_token,
            runner_id,
            runner_release_digest,
        )
        finished_at = _get_utc_now()

        # Step 7: Finalization (§7.7, §9)
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.cursor()

        # Re-check attempt ownership before finalize
        row_fin_check = cursor.execute(
            "SELECT state, runner_id, fence_token FROM main.cron_execution_attempts WHERE execution_id = ? AND attempt = 1;",
            (execution_id,),
        ).fetchone()
        if (
            row_fin_check is None
            or row_fin_check[0] != "running"
            or row_fin_check[1] != runner_id
            or row_fin_check[2] != fence_token
        ):
            conn.rollback()
            raise CronAuthorityError(
                "Finalization rejected due to stale owner or fence mismatch",
                code="stale_owner_finalization_rejected",
            )

        ev_digest: str | None = None
        if (
            adapter_res.outcome != "succeeded"
            or adapter_res.stdout
            or adapter_res.stderr
        ):
            ev_data = json.dumps(
                {
                    "exit_code": adapter_res.exit_code,
                    "stdout": adapter_res.stdout,
                    "stderr": adapter_res.stderr,
                    "error_classification": adapter_res.error_classification,
                }
            ).encode("utf-8")
            ev_digest = hashlib.sha256(ev_data).hexdigest()
            cursor.execute(
                "INSERT OR IGNORE INTO main.cron_evidence (evidence_digest, canonical_bytes, created_at) VALUES (?, ?, ?);",
                (ev_digest, ev_data, _get_utc_now()),
            )

        receipt_id = f"rcpt_{execution_id}_1"
        rcpt = CronRunReceipt(
            receipt_id=receipt_id,
            cron_id=envelope.cron_id,
            execution_id=execution_id,
            outcome=adapter_res.outcome,
            attempt=1,
            runner_id=runner_id,
            runner_release_digest=runner_release_digest,
            started_at=started_at,
            finished_at=finished_at,
            error_classification=adapter_res.error_classification,
            evidence_digest=ev_digest,
            signing_key_id="unsigned",
            signature="none",
        )

        cursor.execute(
            """
            INSERT INTO main.cron_receipts (
                receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                started_at, finished_at, error_classification, evidence_digest, signing_key_id, signature,
                schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
            """,
            (
                rcpt.receipt_id,
                rcpt.execution_id,
                rcpt.attempt,
                rcpt.cron_id,
                rcpt.outcome,
                rcpt.runner_id,
                rcpt.runner_release_digest,
                rcpt.started_at,
                rcpt.finished_at,
                rcpt.error_classification,
                rcpt.evidence_digest,
                rcpt.signing_key_id,
                rcpt.signature,
                _get_utc_now(),
            ),
        )
        conn.commit()
        return {
            "disposition": "accepted",
            "reason_code": "executed",
            "execution_id": execution_id,
            "receipt_id": receipt_id,
            "outcome": adapter_res.outcome,
            "adapter_called": True,
        }
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if close_conn:
            conn.close()


def reconcile_expired_attempts(
    db_target: Any,
    runner_id: str,
    runner_release_digest: str,
    limit: int = 10,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Reconcile expired claimed/running execution attempts once (§7.9)."""
    _validate_runner_binding(runner_id, runner_release_digest)
    if type(limit) is not int or limit < 1:
        raise CronAuthorityError(
            "limit must be int >= 1", code="invalid_reconcile_limit"
        )

    migrate_cron_authority(db_target, timeout=timeout)
    conn = connect_cron_authority(db_target, timeout=timeout)
    close_conn = not hasattr(db_target, "cursor")

    reconciled_results: list[dict[str, Any]] = []

    try:
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.cursor()
        now_utc = _get_utc_now()

        expired_rows = cursor.execute(
            """
            SELECT attempt.execution_id, attempt.attempt, attempt.fence_token, attempt.runner_id, agg.cron_id
            FROM main.cron_execution_attempts AS attempt
            JOIN main.cron_execution_aggregates AS agg ON agg.execution_id = attempt.execution_id
            WHERE attempt.state IN ('claimed', 'running')
              AND attempt.lease_expires_at < ?
            ORDER BY attempt.lease_expires_at ASC
            LIMIT ?;
            """,
            (now_utc, limit),
        ).fetchall()

        for exec_id, attempt_num, old_fence, old_runner, cron_id in expired_rows:
            new_fence = (old_fence or 0) + 1
            new_lease_dt = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + 30.0, timezone.utc
            )
            new_lease_at = new_lease_dt.isoformat()
            if new_lease_at.endswith("+00:00"):
                new_lease_at = new_lease_at[:-6] + "Z"

            res_upd = cursor.execute(
                """
                UPDATE main.cron_execution_attempts
                SET state = 'reconciling', runner_id = ?, fence_token = ?, lease_expires_at = ?, updated_at = ?
                WHERE execution_id = ? AND attempt = ? AND fence_token = ? AND state IN ('claimed', 'running');
                """,
                (
                    runner_id,
                    new_fence,
                    new_lease_at,
                    now_utc,
                    exec_id,
                    attempt_num,
                    old_fence,
                ),
            )

            if res_upd.rowcount > 0:
                ev_data = json.dumps(
                    {
                        "reconciled_by": runner_id,
                        "previous_owner": old_runner,
                        "previous_fence": old_fence,
                        "new_fence": new_fence,
                        "reconciled_at": now_utc,
                    }
                ).encode("utf-8")
                ev_digest = hashlib.sha256(ev_data).hexdigest()
                cursor.execute(
                    "INSERT OR IGNORE INTO main.cron_evidence (evidence_digest, canonical_bytes, created_at) VALUES (?, ?, ?);",
                    (ev_digest, ev_data, now_utc),
                )

                receipt_id = f"rcpt_{exec_id}_{attempt_num}"
                rcpt = CronRunReceipt(
                    receipt_id=receipt_id,
                    cron_id=cron_id,
                    execution_id=exec_id,
                    outcome="reconciled",
                    attempt=attempt_num,
                    runner_id=runner_id,
                    runner_release_digest=runner_release_digest,
                    started_at=now_utc,
                    finished_at=now_utc,
                    error_classification="lease_expired_reconciled",
                    evidence_digest=ev_digest,
                    signing_key_id="unsigned",
                    signature="none",
                )
                cursor.execute(
                    """
                    INSERT INTO main.cron_receipts (
                        receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                        started_at, finished_at, error_classification, evidence_digest, signing_key_id, signature,
                        schema_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
                    """,
                    (
                        rcpt.receipt_id,
                        rcpt.execution_id,
                        rcpt.attempt,
                        rcpt.cron_id,
                        rcpt.outcome,
                        rcpt.runner_id,
                        rcpt.runner_release_digest,
                        rcpt.started_at,
                        rcpt.finished_at,
                        rcpt.error_classification,
                        rcpt.evidence_digest,
                        rcpt.signing_key_id,
                        rcpt.signature,
                        now_utc,
                    ),
                )
                reconciled_results.append(
                    {
                        "execution_id": exec_id,
                        "attempt": attempt_num,
                        "receipt_id": receipt_id,
                        "reconciled_fence": new_fence,
                    }
                )

        conn.commit()
        return reconciled_results
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if close_conn:
            conn.close()
