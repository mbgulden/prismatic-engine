"""Durable provider-neutral verification receipt storage and read models.

The existing receipt validator is the acceptance authority. Hosted-provider signals are
retained as optional transport metadata and never participate in eligibility decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from prismatic.universal_result_manifest import find_secrets
from prismatic.verification.receipt_validator import determine_merge_eligibility

PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER = (
    "PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_OK"
)
OPTIONAL_HOSTED_SIGNAL = "OPTIONAL_HOSTED_SIGNAL"

_IDENTITY_FIELDS = (
    "schema_version",
    "policy_id",
    "policy_version",
    "task_id",
    "repository_id",
    "base_sha",
    "base_tree_sha",
    "candidate_sha",
    "tree_sha",
    "canonical_repository_root",
    "checkout_clean_state",
    "clean_checkout_id",
    "verifier_id",
    "backend_id",
    "producer_id",
)
_HOSTED_SIGNAL_FIELDS = {
    "provider",
    "status",
    "reason_code",
    "observed_at",
    "run_id",
}
_HOSTED_SIGNAL_STATUSES = {"success", "failure", "unavailable", "unknown"}
_NATIVE_REQUIRED_FIELDS = {
    "base_tree_sha",
    "canonical_repository_root",
    "checkout_clean_state",
    "changed_path_containment",
    "verifier_isolation",
    "proof_scope_status",
}
_PROOF_SCOPES = {
    "focused",
    "canonical",
    "clean_room",
    "package",
    "production",
    "browser",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _default_db_path() -> Path:
    explicit = os.environ.get("PRISMATIC_VERIFICATION_RECEIPT_DB")
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)
    state_dir = Path(
        os.environ.get(
            "PRISMATIC_STATE_DIR",
            str(Path.home() / ".prismatic" / "db"),
        )
    ).expanduser()
    return state_dir / "provider_neutral_verification_receipts.sqlite3"


def _normalize_hosted_signals(signals: Any) -> list[dict[str, str]]:
    if signals is None:
        return []
    if not isinstance(signals, list):
        raise ValueError("hosted_signals must be a list")
    normalized: list[dict[str, str]] = []
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            raise ValueError(f"hosted_signals[{index}] must be an object")
        unknown = set(signal) - _HOSTED_SIGNAL_FIELDS
        if unknown:
            raise ValueError(
                f"hosted_signals[{index}] has unsupported fields: {sorted(unknown)}"
            )
        provider = signal.get("provider")
        status = signal.get("status")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError(f"hosted_signals[{index}].provider is required")
        if status not in _HOSTED_SIGNAL_STATUSES:
            raise ValueError(f"hosted_signals[{index}].status is invalid")
        row = {
            "provider": provider.strip().lower(),
            "status": status,
            "signal_class": OPTIONAL_HOSTED_SIGNAL,
        }
        for key in ("reason_code", "observed_at", "run_id"):
            value = signal.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"hosted_signals[{index}].{key} must be text")
                row[key] = value.strip()
        normalized.append(row)
    return normalized


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError("canonical repository binding could not be verified")
    return result.stdout.strip()


def _normalized_repo_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} contains a noncanonical repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{field} contains a noncanonical repository path")
    return path


def _validate_native_bindings(receipt: dict[str, Any]) -> None:
    missing = sorted(_NATIVE_REQUIRED_FIELDS - set(receipt))
    if missing:
        raise ValueError(f"native receipt missing fields: {missing}")

    root_value = receipt["canonical_repository_root"]
    if not isinstance(root_value, str):
        raise ValueError("canonical_repository_root must be text")
    root = Path(root_value)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("canonical_repository_root must be an existing directory")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("canonical_repository_root cannot be resolved") from exc
    if resolved_root != root:
        raise ValueError("canonical_repository_root must be nonsymlink and canonical")
    if Path(_git(root, "rev-parse", "--show-toplevel")) != root:
        raise ValueError("canonical_repository_root is not the exact Git root")

    clean_state = receipt["checkout_clean_state"]
    if not isinstance(clean_state, dict) or clean_state.get("status") != "clean":
        raise ValueError("checkout_clean_state must claim clean")
    porcelain = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if porcelain:
        raise ValueError("canonical repository checkout is not clean")
    if clean_state.get("porcelain_sha256") != f"sha256:{_sha256_text(porcelain)}":
        raise ValueError("checkout_clean_state digest does not match Git status")
    observed_at = clean_state.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("checkout_clean_state observed_at must be text")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("checkout_clean_state observed_at is invalid") from exc
    if observed.tzinfo is None:
        raise ValueError("checkout_clean_state observed_at must be timezone-aware")
    clean_state_age = (datetime.now(timezone.utc) - observed).total_seconds()
    if clean_state_age < -60 or clean_state_age > 300:
        raise ValueError("checkout_clean_state observation is not fresh")

    candidate_sha = receipt.get("candidate_sha")
    base_sha = receipt.get("base_sha")
    tree_sha = receipt.get("tree_sha")
    base_tree_sha = receipt.get("base_tree_sha")
    if not all(
        isinstance(value, str)
        for value in (candidate_sha, base_sha, tree_sha, base_tree_sha)
    ):
        raise ValueError("native commit and tree bindings must be text")
    assert isinstance(candidate_sha, str)
    assert isinstance(base_sha, str)
    assert isinstance(tree_sha, str)
    assert isinstance(base_tree_sha, str)
    if _git(root, "rev-parse", "HEAD") != candidate_sha:
        raise ValueError("candidate_sha does not equal clean-checkout HEAD")
    if _git(root, "rev-parse", f"{candidate_sha}^{{tree}}") != tree_sha:
        raise ValueError("tree_sha does not match candidate tree")
    if _git(root, "rev-parse", f"{base_sha}^{{tree}}") != base_tree_sha:
        raise ValueError("base_tree_sha does not match base commit")

    changed_paths = receipt.get("changed_paths")
    if not isinstance(changed_paths, list) or not changed_paths:
        raise ValueError("changed_paths must be a nonempty list")
    normalized_changed = [
        _normalized_repo_path(value, field="changed_paths") for value in changed_paths
    ]
    actual_changed = sorted(
        line
        for line in _git(
            root, "diff", "--name-only", base_sha, candidate_sha
        ).splitlines()
        if line
    )
    if sorted(str(path) for path in normalized_changed) != actual_changed:
        raise ValueError("changed_paths do not match exact base-to-candidate diff")

    containment = receipt["changed_path_containment"]
    if not isinstance(containment, dict):
        raise ValueError("changed_path_containment must be an object")
    allowed_values = containment.get("allowed_roots")
    if not isinstance(allowed_values, list) or not allowed_values:
        raise ValueError("changed_path_containment.allowed_roots is required")
    allowed_roots = [
        _normalized_repo_path(value, field="allowed_roots") for value in allowed_values
    ]
    actually_contained = all(
        any(
            changed.parts[: len(allowed.parts)] == allowed.parts
            for allowed in allowed_roots
        )
        for changed in normalized_changed
    )
    if containment.get("contained") is not True or not actually_contained:
        raise ValueError("changed paths are outside allowed roots")
    expected_paths_digest = "sha256:" + _sha256_text(_canonical_json(actual_changed))
    if containment.get("changed_paths_sha256") != expected_paths_digest:
        raise ValueError("changed_paths_sha256 mismatch")

    isolation = receipt["verifier_isolation"]
    if not isinstance(isolation, dict):
        raise ValueError("verifier_isolation must be an object")
    if isolation.get("independent") is not True:
        raise ValueError("verifier must be independent")
    if isolation.get("filesystem_isolated") is not True:
        raise ValueError("verifier filesystem isolation is required")
    if isolation.get("clean_room_id") != receipt.get("clean_checkout_id"):
        raise ValueError("verifier isolation clean-room binding mismatch")

    scope_status = receipt["proof_scope_status"]
    if not isinstance(scope_status, dict) or set(scope_status) != _PROOF_SCOPES:
        raise ValueError("proof_scope_status must report every native proof scope")
    command_ids = {
        command.get("command_id")
        for command in receipt.get("commands_and_exit_states", [])
        if isinstance(command, dict)
    }
    for scope_name, scope in scope_status.items():
        if not isinstance(scope, dict):
            raise ValueError(f"proof scope {scope_name} must be an object")
        evidence_ids = scope.get("evidence_ids")
        if scope.get("status") == "pass" and (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not set(evidence_ids).issubset(command_ids)
        ):
            raise ValueError(
                f"passing proof scope {scope_name} must link executed command IDs"
            )
    if receipt.get("decision", {}).get("status") == "pass" and any(
        scope.get("status") == "fail" for scope in scope_status.values()
    ):
        raise ValueError("passing receipt cannot contain a failed proof scope")


def receipt_identity(receipt: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    identity = {field: receipt.get(field) for field in _IDENTITY_FIELDS}
    identity["changed_path_containment"] = receipt.get("changed_path_containment")
    identity["verifier_isolation"] = receipt.get("verifier_isolation")
    missing = [field for field, value in identity.items() if value in (None, "")]
    if missing:
        raise ValueError(f"receipt identity missing fields: {missing}")
    digest = _sha256_text(_canonical_json(identity))
    return f"pnvr-{digest}", identity


def _classification(receipt: dict[str, Any], eligible: bool, reason: str | None) -> str:
    if receipt.get("revocation_status") == "revoked":
        return "revoked"
    if eligible:
        return "accepted"
    reason_text = reason or ""
    if "revok" in reason_text:
        return "revoked"
    if (
        "freshness" in reason_text
        or "stale" in reason_text
        or "timestamp" in reason_text
    ):
        return "stale"
    if "supersed" in reason_text:
        return "superseded"
    return "blocked"


@dataclass(frozen=True)
class StoredVerificationReceipt:
    receipt_id: str
    created_at: str
    classification: str
    merge_eligible: bool
    decision_reason: str | None
    receipt_sha256: str
    policy_sha256: str
    receipt: dict[str, Any]
    policy: dict[str, Any]
    hosted_signals: list[dict[str, str]]
    superseded_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "created_at": self.created_at,
            "classification": self.classification,
            "merge_eligible": self.merge_eligible,
            "decision_reason": self.decision_reason,
            "receipt_sha256": self.receipt_sha256,
            "policy_sha256": self.policy_sha256,
            "candidate_sha": self.receipt.get("candidate_sha"),
            "tree_sha": self.receipt.get("tree_sha"),
            "base_sha": self.receipt.get("base_sha"),
            "base_tree_sha": self.receipt.get("base_tree_sha"),
            "canonical_repository_root": self.receipt.get("canonical_repository_root"),
            "checkout_clean_state": self.receipt.get("checkout_clean_state"),
            "changed_path_containment": self.receipt.get("changed_path_containment"),
            "verifier_isolation": self.receipt.get("verifier_isolation"),
            "proof_scope_status": self.receipt.get("proof_scope_status"),
            "task_id": self.receipt.get("task_id"),
            "repository_id": self.receipt.get("repository_id"),
            "source_kind": self.receipt.get("source_kind"),
            "source_provider": self.receipt.get("source_provider"),
            "source_locator": self.receipt.get("source_locator"),
            "clean_checkout_id": self.receipt.get("clean_checkout_id"),
            "source_acquisition_digest": self.receipt.get("source_acquisition_digest"),
            "environment_digest": self.receipt.get("environment_digest"),
            "changed_paths": self.receipt.get("changed_paths", []),
            "proof_classes": self.receipt.get("proof_classes", []),
            "commands_and_exit_states": self.receipt.get(
                "commands_and_exit_states", []
            ),
            "logs_and_digests": self.receipt.get("logs_and_digests", []),
            "artifacts_and_digests": self.receipt.get("artifacts_and_digests", []),
            "verifier_id": self.receipt.get("verifier_id"),
            "backend_id": self.receipt.get("backend_id"),
            "backend_class": self.receipt.get("backend_class"),
            "completed_at": self.receipt.get("completed_at"),
            "expires_at": self.receipt.get("expires_at"),
            "revocation_status": self.receipt.get("revocation_status"),
            "supersedes": self.receipt.get("supersedes"),
            "superseded_by": self.superseded_by,
            "non_claims": self.receipt.get("non_claims", []),
            "hosted_signals": self.hosted_signals,
            "hosted_signals_required": False,
            "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        }


def verification_receipt_store_path(db_path: Path | str | None = None) -> Path:
    """Resolve the durable store path without creating files."""

    return Path(db_path) if db_path is not None else _default_db_path()


def verification_revocation_store_path(
    db_path: Path | str | None = None,
    revocation_store_path: Path | str | None = None,
) -> Path:
    """Resolve the canonical fail-closed revocation source."""

    if revocation_store_path is not None:
        return Path(revocation_store_path)
    configured = os.environ.get("PRISMATIC_VERIFICATION_REVOCATION_STORE")
    if configured:
        return Path(configured)
    return verification_receipt_store_path(db_path).with_name(
        "provider_neutral_verification_revocations.json"
    )


class VerificationReceiptStore:
    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        revocation_store_path: Path | str | None = None,
    ):
        self.db_path = verification_receipt_store_path(db_path)
        db_already_exists = self.db_path.exists()
        self.revocation_store_path = verification_revocation_store_path(
            db_path, revocation_store_path
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.revocation_store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.revocation_store_path.exists() and not db_already_exists:
            try:
                fd = os.open(
                    self.revocation_store_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write("[]\n")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_neutral_verification_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    hosted_signals_json TEXT NOT NULL,
                    envelope_sha256 TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    merge_eligible INTEGER NOT NULL CHECK (merge_eligible IN (0, 1)),
                    decision_reason TEXT,
                    superseded_by TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(provider_neutral_verification_receipts)"
                )
            }
            if "superseded_by" not in columns:
                conn.execute(
                    """
                    ALTER TABLE provider_neutral_verification_receipts
                    ADD COLUMN superseded_by TEXT
                    """
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pnvr_created
                ON provider_neutral_verification_receipts(created_at DESC, receipt_id DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_neutral_receipt_lifecycle_events (
                    event_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('superseded', 'revoked')),
                    successor_receipt_id TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(receipt_id)
                        REFERENCES provider_neutral_verification_receipts(receipt_id)
                )
                """
            )
            conn.execute("DROP INDEX IF EXISTS idx_pnvr_one_terminal_event")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pnvr_lifecycle_receipt
                ON provider_neutral_receipt_lifecycle_events(receipt_id, created_at)
                """
            )

    def persist(
        self,
        receipt: dict[str, Any],
        policy: dict[str, Any],
        *,
        hosted_signals: Any = None,
    ) -> StoredVerificationReceipt:
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        _validate_native_bindings(receipt)
        receipt_id, identity = receipt_identity(receipt)
        normalized_signals = _normalize_hosted_signals(hosted_signals)
        if find_secrets(
            {
                "receipt": receipt,
                "policy": policy,
                "hosted_signals": normalized_signals,
            }
        ):
            raise ValueError("secret-like content detected; receipt was not stored")
        eligible, reason = determine_merge_eligibility(
            receipt, policy, revocation_store=self.revocation_store_path
        )
        receipt_json = _canonical_json(receipt)
        policy_json = _canonical_json(policy)
        identity_json = _canonical_json(identity)
        signals_json = _canonical_json(normalized_signals)
        receipt_sha256 = _sha256_text(receipt_json)
        policy_sha256 = _sha256_text(policy_json)
        envelope_sha256 = _sha256_text(
            _canonical_json(
                {
                    "identity": identity,
                    "receipt_sha256": receipt_sha256,
                    "policy_sha256": policy_sha256,
                    "hosted_signals": normalized_signals,
                }
            )
        )
        classification = _classification(receipt, eligible, reason)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _validate_native_bindings(receipt)
            supersedes = receipt.get("supersedes")
            superseded_row = None
            if supersedes is not None:
                superseded_row = conn.execute(
                    """
                    SELECT * FROM provider_neutral_verification_receipts
                    WHERE receipt_id = ?
                    """,
                    (supersedes,),
                ).fetchone()
                if superseded_row is None:
                    raise ValueError("supersedes receipt does not exist")
                superseded_receipt = json.loads(superseded_row["receipt_json"])
                if superseded_receipt.get("repository_id") != receipt.get(
                    "repository_id"
                ) or superseded_receipt.get("task_id") != receipt.get("task_id"):
                    raise ValueError("supersedes receipt lineage mismatch")
                prior_event = conn.execute(
                    """
                    SELECT successor_receipt_id
                    FROM provider_neutral_receipt_lifecycle_events
                    WHERE receipt_id = ?
                    """,
                    (supersedes,),
                ).fetchone()
                if (
                    prior_event is not None
                    and prior_event["successor_receipt_id"] != receipt_id
                ):
                    raise ValueError("receipt already superseded by another receipt")
            existing = conn.execute(
                """
                SELECT * FROM provider_neutral_verification_receipts
                WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if existing is not None:
                if existing["envelope_sha256"] != envelope_sha256:
                    raise ValueError(
                        "conflicting immutable verification receipt replay"
                    )
                return self._row(existing, conn=conn)
            conn.execute(
                """
                INSERT INTO provider_neutral_verification_receipts (
                    receipt_id, created_at, identity_json, receipt_json,
                    receipt_sha256, policy_json, policy_sha256,
                    hosted_signals_json, envelope_sha256, classification,
                    merge_eligible, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    created_at,
                    identity_json,
                    receipt_json,
                    receipt_sha256,
                    policy_json,
                    policy_sha256,
                    signals_json,
                    envelope_sha256,
                    classification,
                    int(eligible),
                    reason,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM provider_neutral_verification_receipts
                WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - defensive
                raise RuntimeError("verification receipt insert was not durable")
            if superseded_row is not None:
                event_id = "pnvrl-" + _sha256_text(
                    _canonical_json(
                        {
                            "event_type": "superseded",
                            "receipt_id": supersedes,
                            "successor_receipt_id": receipt_id,
                        }
                    )
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_neutral_receipt_lifecycle_events (
                        event_id, receipt_id, event_type, successor_receipt_id,
                        reason, created_at
                    ) VALUES (?, ?, 'superseded', ?, ?, ?)
                    """,
                    (
                        event_id,
                        supersedes,
                        receipt_id,
                        f"superseded_by:{receipt_id}",
                        created_at,
                    ),
                )
            return self._row(row, conn=conn)

    def revoke(
        self,
        receipt_id: str,
        *,
        reason: str,
        revoked_by: str,
    ) -> StoredVerificationReceipt:
        """Append an immutable revocation event and return effective state."""

        reason = str(reason or "").strip()
        revoked_by = str(revoked_by or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("revocation reason must be 1-500 characters")
        if not revoked_by or len(revoked_by) > 200:
            raise ValueError("revoked_by must be 1-200 characters")
        event_reason = f"revoked_by:{revoked_by}:{reason}"
        event_id = "pnvrl-" + _sha256_text(f"revoked:{receipt_id}")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM provider_neutral_verification_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            existing = conn.execute(
                """
                SELECT reason FROM provider_neutral_receipt_lifecycle_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["reason"] != event_reason:
                    raise ValueError("conflicting immutable revocation replay")
                return self._row(row, conn=conn)
            conn.execute(
                """
                INSERT INTO provider_neutral_receipt_lifecycle_events (
                    event_id, receipt_id, event_type, successor_receipt_id,
                    reason, created_at
                ) VALUES (?, ?, 'revoked', NULL, ?, ?)
                """,
                (
                    event_id,
                    receipt_id,
                    event_reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return self._row(row, conn=conn)

    def get(self, receipt_id: str) -> StoredVerificationReceipt:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM provider_neutral_verification_receipts
                WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            return self._row(row, conn=conn)

    def list(self, *, limit: int = 50) -> list[StoredVerificationReceipt]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 200
        ):
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM provider_neutral_verification_receipts
                ORDER BY created_at DESC, receipt_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row(row, conn=conn) for row in rows]

    def find_latest(
        self, *, task_id: str, candidate_sha: str
    ) -> StoredVerificationReceipt | None:
        """Return the newest effective receipt for one exact task/artifact."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM provider_neutral_verification_receipts
                ORDER BY created_at DESC, receipt_id DESC
                """
            ).fetchall()
            for row in rows:
                projected = self._row(row, conn=conn)
                if (
                    projected.receipt.get("task_id") == task_id
                    and projected.receipt.get("candidate_sha") == candidate_sha
                ):
                    return projected
        return None

    def counts(self) -> dict[str, int]:
        counts = {
            name: 0
            for name in ("accepted", "blocked", "stale", "revoked", "superseded")
        }
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_neutral_verification_receipts"
            ).fetchall()
            for row in rows:
                projected = self._row(row, conn=conn)
                counts[projected.classification] = (
                    counts.get(projected.classification, 0) + 1
                )
        counts["total"] = len(rows)
        return counts

    def _row(
        self, row: sqlite3.Row, *, conn: sqlite3.Connection | None = None
    ) -> StoredVerificationReceipt:
        receipt = json.loads(row["receipt_json"])
        policy = json.loads(row["policy_json"])
        merge_eligible, decision_reason = determine_merge_eligibility(
            receipt, policy, revocation_store=self.revocation_store_path
        )
        classification = _classification(receipt, merge_eligible, decision_reason)
        superseded_by = None
        if conn is not None:
            event = conn.execute(
                """
                SELECT event_type, successor_receipt_id, reason
                FROM provider_neutral_receipt_lifecycle_events
                WHERE receipt_id = ?
                ORDER BY CASE WHEN event_type = 'revoked' THEN 0 ELSE 1 END,
                         created_at DESC, event_id DESC
                LIMIT 1
                """,
                (row["receipt_id"],),
            ).fetchone()
            if event is not None:
                classification = event["event_type"]
                merge_eligible = False
                decision_reason = event["reason"]
                superseded_by = (
                    event["successor_receipt_id"]
                    if event["event_type"] == "superseded"
                    else None
                )
        return StoredVerificationReceipt(
            receipt_id=row["receipt_id"],
            created_at=row["created_at"],
            classification=classification,
            merge_eligible=merge_eligible,
            decision_reason=decision_reason,
            receipt_sha256=row["receipt_sha256"],
            policy_sha256=row["policy_sha256"],
            receipt=receipt,
            policy=policy,
            hosted_signals=json.loads(row["hosted_signals_json"]),
            superseded_by=superseded_by,
        )


def verification_receipt_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / ("provider-neutral-verification-receipt.schema.json")
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return {
        "status": "ok",
        "marker": PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
        "schema": schema,
        "hosted_signal_class": OPTIONAL_HOSTED_SIGNAL,
        "hosted_signals_required": False,
        "acceptance_authority": "native_provider_neutral_receipt",
        "non_claims": {
            "github_required": False,
            "github_actions_required": False,
            "auto_merge": False,
            "auto_deploy": False,
        },
    }


def persist_verification_receipt(
    receipt: dict[str, Any],
    policy: dict[str, Any],
    *,
    hosted_signals: Any = None,
    db_path: Path | str | None = None,
) -> StoredVerificationReceipt:
    return VerificationReceiptStore(db_path).persist(
        receipt, policy, hosted_signals=hosted_signals
    )


def revoke_verification_receipt(
    receipt_id: str,
    *,
    reason: str,
    revoked_by: str,
    db_path: Path | str | None = None,
) -> StoredVerificationReceipt:
    return VerificationReceiptStore(db_path).revoke(
        receipt_id, reason=reason, revoked_by=revoked_by
    )


def list_verification_receipts(
    *, limit: int = 50, db_path: Path | str | None = None
) -> list[StoredVerificationReceipt]:
    return VerificationReceiptStore(db_path).list(limit=limit)


def get_verification_receipt(
    receipt_id: str, *, db_path: Path | str | None = None
) -> StoredVerificationReceipt:
    return VerificationReceiptStore(db_path).get(receipt_id)


def verification_receipt_counts(*, db_path: Path | str | None = None) -> dict[str, int]:
    return VerificationReceiptStore(db_path).counts()
