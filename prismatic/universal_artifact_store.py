"""Durable, content-addressed artifact store, quarantine, and retention engine.

Implements durable content-addressed artifact ingestion for binary and text outputs.
Verifies SHA-256, byte size, and media type; deduplicates safely; enforces private/public
access classes; quarantines unsafe or mismatched artifacts; retains source plus derivatives;
prevents traversal, symlink, userinfo, and control-character hazards; and issues immutable
artifact receipts and proof logs.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from prismatic.universal_result_manifest import (
    MANIFEST_FORMAT_CHECKER,
    SECRET_VALUE_RE,
    find_secrets,
    is_safe_path,
    sanitize_error_message,
)


class CrossProcessLock:
    """Reentrant thread and cross-process OS flock lock."""

    def __init__(self, lock_file: Path):
        self.lock_file = Path(lock_file).resolve()
        self._thread_lock = threading.RLock()
        self._fd: int | None = None
        self._ref_count = 0

    def __enter__(self):
        self._thread_lock.acquire()
        if self._ref_count == 0:
            try:
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_RDWR | os.O_CREAT
                self._fd = os.open(str(self.lock_file), flags, 0o600)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except Exception:
                self._thread_lock.release()
                raise
        self._ref_count += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._ref_count -= 1
            if self._ref_count == 0 and self._fd is not None:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
        finally:
            self._thread_lock.release()


UNIVERSAL_ARTIFACT_STORE_MARKER = "UNIVERSAL_ARTIFACT_STORE_OK"


def compute_event_integrity_digest(event: dict[str, Any]) -> str:
    canonical_dict = {k: v for k, v in event.items() if k != "integrity_digest"}
    canonical_str = json.dumps(canonical_dict, sort_keys=True)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def compute_receipt_integrity_digest(receipt_dict: dict[str, Any]) -> str:
    canonical_dict = {k: v for k, v in receipt_dict.items() if k != "evidence_digest"}
    canonical_str = json.dumps(canonical_dict, sort_keys=True)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def compute_deterministic_receipt_id(
    content_digest: str,
    source_commit: str,
    command_env: dict[str, Any],
    media_type: str,
    access_class: AccessClass | str,
    parent_artifact_id: str | None = None,
    merge_evidence_refs: Sequence[str] = (),
) -> str:
    env_str = json.dumps(command_env, sort_keys=True)
    refs_str = ",".join(sorted(merge_evidence_refs))
    parent_str = parent_artifact_id or ""
    access_str = (
        access_class.value if isinstance(access_class, Enum) else str(access_class)
    )

    canonical_identity = "|".join(
        [
            content_digest,
            source_commit,
            env_str,
            media_type,
            access_str,
            parent_str,
            refs_str,
        ]
    )
    h = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return f"art_{h[:16]}"


def compute_deterministic_quarantine_id(
    content_digest: str,
    source_commit: str,
    command_env: dict[str, Any],
    media_type: str,
    quarantine_reason: str,
    parent_artifact_id: str | None = None,
) -> str:
    env_str = json.dumps(command_env, sort_keys=True)
    parent_str = parent_artifact_id or ""
    canonical_identity = "|".join(
        [
            content_digest,
            source_commit,
            env_str,
            media_type,
            quarantine_reason,
            parent_str,
        ]
    )
    h = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return f"quar_{h[:16]}"


def validate_receipt_id_format(receipt_id: str) -> None:
    if not isinstance(receipt_id, str):
        raise ArtifactIngestionError("Receipt ID must be a string")
    if receipt_id.startswith("art_"):
        if not re.match(r"^art_[a-f0-9]{16}$", receipt_id):
            raise ArtifactIngestionError(f"Malformed receipt ID: '{receipt_id}'")
    elif receipt_id.startswith("quar_"):
        if not re.match(r"^quar_[a-f0-9]{16}$", receipt_id):
            raise ArtifactIngestionError(f"Malformed quarantine ID: '{receipt_id}'")
    else:
        raise ArtifactIngestionError(f"Malformed receipt ID: '{receipt_id}'")


def safe_verify_path(base_dir: Path, target_path: Path) -> None:
    resolved_base = base_dir.resolve()
    target_str = str(target_path)
    if any(ord(c) < 32 or ord(c) == 127 for c in target_str):
        raise ArtifactIngestionError("Path contains control characters")
    if ".." in target_path.parts:
        raise ArtifactIngestionError("Path contains traversal segments")

    try:
        resolved_target = target_path.resolve(strict=False)
    except Exception:
        resolved_target = target_path.absolute()

    try:
        resolved_target.relative_to(resolved_base)
    except ValueError:
        raise ArtifactIngestionError(
            f"Path fencing violation: '{target_path}' is not within '{base_dir}'"
        )


def safe_write_text_exclusive(path: Path, content: str) -> None:
    safe_verify_path(path.parent.parent, path.parent)
    safe_verify_path(path.parent, path)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise


def safe_write_bytes_exclusive(path: Path, content: bytes) -> None:
    safe_verify_path(path.parent.parent, path.parent)
    safe_verify_path(path.parent, path)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise


def safe_read_text_nofollow(path: Path) -> str:
    safe_verify_path(path.parent, path)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        return f.read()


def safe_read_bytes_nofollow(path: Path) -> bytes:
    safe_verify_path(path.parent, path)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def safe_atomic_write_bytes(final_path: Path, content: bytes) -> None:
    temp_path = final_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
    safe_write_bytes_exclusive(temp_path, content)
    os.chmod(temp_path, 0o600)
    assert (temp_path.stat().st_mode & 0o777) == 0o600
    os.replace(temp_path, final_path)
    os.chmod(final_path, 0o600)
    assert (final_path.stat().st_mode & 0o777) == 0o600


def safe_atomic_write_text(final_path: Path, content: str) -> None:
    temp_path = final_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
    safe_write_text_exclusive(temp_path, content)
    os.chmod(temp_path, 0o600)
    assert (temp_path.stat().st_mode & 0o777) == 0o600
    os.replace(temp_path, final_path)
    os.chmod(final_path, 0o600)
    assert (final_path.stat().st_mode & 0o777) == 0o600


class AccessClass(str, Enum):
    """Artifact access classification."""

    PRIVATE = "private"
    PUBLIC = "public"


class ArtifactStatus(str, Enum):
    """Artifact lifecycle status."""

    INGESTED = "ingested"
    QUARANTINED = "quarantined"
    RETAINED = "retained"
    ARCHIVED = "archived"


class ArtifactIngestionError(ValueError):
    """Raised when artifact ingestion fails closed due to safety or integrity violations."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        sanitized = sanitize_error_message(message)
        super().__init__(sanitized)
        self.details = details or {}


class ArtifactAccessDeniedError(PermissionError):
    """Raised when an attempt is made to access a private or quarantined artifact without permission."""

    def __init__(self, message: str):
        super().__init__(sanitize_error_message(message))


class ArtifactNotFoundError(KeyError):
    """Raised when a requested artifact or receipt is not found."""

    def __init__(self, key: str):
        super().__init__(sanitize_error_message(f"Artifact not found: {key}"))


@dataclass(frozen=True)
class ArtifactReceipt:
    receipt_id: str
    content_digest: str
    storage_id: str
    source_commit: str
    command_env: dict[str, Any]
    evidence_digest: str
    byte_size: int
    media_type: str
    access_class: AccessClass
    status: ArtifactStatus
    is_quarantined: bool
    created_at: str
    quarantine_reason: str | None = None
    parent_artifact_id: str | None = None
    derivative_ids: tuple[str, ...] = ()
    merge_evidence_refs: tuple[str, ...] = ()
    marker: str = UNIVERSAL_ARTIFACT_STORE_MARKER

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "receipt_id": self.receipt_id,
            "content_digest": self.content_digest,
            "storage_id": self.storage_id,
            "source_commit": self.source_commit,
            "command_env": self.command_env,
            "evidence_digest": self.evidence_digest,
            "byte_size": self.byte_size,
            "media_type": self.media_type,
            "access_class": self.access_class.value
            if isinstance(self.access_class, Enum)
            else str(self.access_class),
            "status": self.status.value
            if isinstance(self.status, Enum)
            else str(self.status),
            "is_quarantined": self.is_quarantined,
            "quarantine_reason": self.quarantine_reason,
            "parent_artifact_id": self.parent_artifact_id,
            "derivative_ids": list(self.derivative_ids),
            "merge_evidence_refs": list(self.merge_evidence_refs),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactReceipt:
        return cls(
            receipt_id=str(data["receipt_id"]),
            content_digest=str(data["content_digest"]),
            storage_id=str(data["storage_id"]),
            source_commit=str(data["source_commit"]),
            command_env=dict(data.get("command_env", {})),
            evidence_digest=str(data["evidence_digest"]),
            byte_size=int(data["byte_size"]),
            media_type=str(data["media_type"]),
            access_class=AccessClass(
                data.get("access_class", AccessClass.PRIVATE.value)
            ),
            status=ArtifactStatus(data.get("status", ArtifactStatus.INGESTED.value)),
            is_quarantined=bool(data.get("is_quarantined", False)),
            quarantine_reason=data.get("quarantine_reason"),
            parent_artifact_id=data.get("parent_artifact_id"),
            derivative_ids=tuple(data.get("derivative_ids", [])),
            merge_evidence_refs=tuple(data.get("merge_evidence_refs", [])),
            created_at=str(data["created_at"]),
            marker=str(data.get("marker", UNIVERSAL_ARTIFACT_STORE_MARKER)),
        )


def load_universal_artifact_store_schema() -> dict[str, Any]:
    """Load the JSON Schema definition for Universal Artifact Store Receipts."""
    try:
        from importlib import resources

        schema_text = (
            resources.files("prismatic")
            .joinpath("schemas/universal-artifact-store.schema.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(schema_text)
    except Exception:
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "schemas" / "universal-artifact-store.schema.json"
        if not schema_path.is_file():
            schema_path = (
                Path(__file__).resolve().parent
                / "schemas"
                / "universal-artifact-store.schema.json"
            )
        with schema_path.open("r", encoding="utf-8") as f:
            return json.load(f)


def validate_artifact_receipt(receipt: Mapping[str, Any]) -> list[str]:
    """Validate an artifact receipt against the JSON schema and semantic rules."""
    errors: list[str] = []
    try:
        schema = load_universal_artifact_store_schema()
        validator = Draft202012Validator(schema, format_checker=MANIFEST_FORMAT_CHECKER)
        schema_errors = sorted(
            validator.iter_errors(receipt), key=lambda e: list(e.path)
        )
        for err in schema_errors:
            path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
            errors.append(sanitize_error_message(f"[{path_str}] {err.message}"))
    except Exception as exc:
        errors.append(sanitize_error_message(f"Schema load/validation failure: {exc}"))

    if not errors:
        if receipt.get("marker") != UNIVERSAL_ARTIFACT_STORE_MARKER:
            errors.append(f"marker must be '{UNIVERSAL_ARTIFACT_STORE_MARKER}'")
        secret_errors = find_secrets(dict(receipt))
        if secret_errors:
            errors.extend(secret_errors)

    return errors


class ArtifactStorageAdapter(ABC):
    """Abstract interface for durable content blob storage."""

    @abstractmethod
    def store_bytes(self, content_digest: str, data: bytes) -> str:
        """Store content bytes under content_digest. Returns storage_id."""

    @abstractmethod
    def read_bytes(self, content_digest: str) -> bytes:
        """Retrieve content bytes for content_digest. Raises ArtifactNotFoundError if missing."""

    @abstractmethod
    def delete_bytes(self, content_digest: str, force: bool = False) -> bool:
        """Delete content bytes for content_digest. Returns True if deleted."""

    @abstractmethod
    def exists(self, content_digest: str) -> bool:
        """Check if content_digest exists in storage."""


class LocalArtifactStorageAdapter(ArtifactStorageAdapter):
    """Thread-safe, durable local filesystem storage adapter."""

    def __init__(self, storage_dir: str | Path):
        self.storage_dir = Path(storage_dir).resolve()
        self.blobs_dir = self.storage_dir / "blobs"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

        # Enforce owner-only permissions on directories
        os.chmod(self.storage_dir, 0o700)
        os.chmod(self.blobs_dir, 0o700)
        assert (self.storage_dir.stat().st_mode & 0o777) == 0o700
        assert (self.blobs_dir.stat().st_mode & 0o777) == 0o700

        self._lock = CrossProcessLock(self.storage_dir / ".adapter.lock")

    def _blob_path(self, content_digest: str) -> Path:
        # Validate that digest matches pattern strictly
        if not re.match(r"^[a-f0-9]{64}$", content_digest):
            raise ArtifactIngestionError(
                f"Malformed content digest: '{content_digest}'"
            )
        prefix = content_digest[:2] if len(content_digest) >= 2 else "xx"
        path = self.blobs_dir / prefix / content_digest
        # Exact-ID path fencing check
        safe_verify_path(self.blobs_dir, path)
        return path

    def store_bytes(self, content_digest: str, data: bytes) -> str:
        blob_path = self._blob_path(content_digest)
        if blob_path.is_file():
            assert (blob_path.stat().st_mode & 0o777) == 0o600
            return f"local://{content_digest}"

        with self._lock:
            if blob_path.is_file():
                assert (blob_path.stat().st_mode & 0o777) == 0o600
                return f"local://{content_digest}"

            prefix_dir = blob_path.parent
            prefix_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(prefix_dir, 0o700)
            assert (prefix_dir.stat().st_mode & 0o777) == 0o700

            safe_atomic_write_bytes(blob_path, data)
            assert (blob_path.stat().st_mode & 0o777) == 0o600

        return f"local://{content_digest}"

    def read_bytes(self, content_digest: str) -> bytes:
        blob_path = self._blob_path(content_digest)
        if not blob_path.is_file():
            raise ArtifactNotFoundError(content_digest)
        assert (blob_path.stat().st_mode & 0o777) == 0o600
        return safe_read_bytes_nofollow(blob_path)

    def delete_bytes(self, content_digest: str, force: bool = False) -> bool:
        blob_path = self._blob_path(content_digest)
        with self._lock:
            if blob_path.is_file():
                blob_path.unlink()
                return True
            return False

    def exists(self, content_digest: str) -> bool:
        try:
            return self._blob_path(content_digest).is_file()
        except Exception:
            return False


def _validate_and_parse_lease_file(
    lease_file: Path, leases_dir: Path, expected_holder_id: str | None = None
) -> dict[str, Any]:
    safe_verify_path(leases_dir, lease_file)
    try:
        text = safe_read_text_nofollow(lease_file)
        data = json.loads(text)
    except Exception as exc:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}' cannot be read or parsed: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': root is not a JSON object"
        )

    expected_keys = {
        "holder_id",
        "lease_token",
        "generation",
        "created_at",
        "updated_at",
        "expires_at",
        "pid",
        "active_digests",
        "active_receipts",
    }
    if set(data.keys()) != expected_keys:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': schema mismatch (keys: {set(data.keys())})"
        )

    holder_id = data.get("holder_id")
    if not isinstance(holder_id, str) or not holder_id.strip():
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': missing or invalid holder_id"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in holder_id):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id contains control characters"
        )
    if ".." in holder_id or "/" in holder_id or "\\" in holder_id:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id contains path hazards"
        )
    if "://" in holder_id or re.search(r"[a-zA-Z0-9_.-]+(?::[^@]*)?@", holder_id):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id contains URL or userinfo"
        )
    if not re.match(r"^[a-zA-Z0-9_.-]{1,128}$", holder_id):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id format invalid"
        )

    if lease_file.name != f"{holder_id}.json":
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id '{holder_id}' does not match file stem '{lease_file.stem}'"
        )
    if expected_holder_id is not None and holder_id != expected_holder_id:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': holder_id '{holder_id}' does not match expected holder_id '{expected_holder_id}'"
        )

    token = data.get("lease_token")
    if not isinstance(token, str) or not re.match(r"^[a-zA-Z0-9_.-]{8,128}$", token):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': missing or invalid lease_token"
        )

    gen = data.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': missing or invalid generation"
        )

    for ts_field in ("created_at", "updated_at", "expires_at"):
        ts_val = data.get(ts_field)
        if not isinstance(ts_val, str) or not ts_val.strip():
            raise ArtifactIngestionError(
                f"Malformed durable lease file '{lease_file.name}': missing or invalid {ts_field}"
            )
        try:
            parsed_dt = datetime.datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                raise ValueError("timestamp must be timezone-aware")
        except Exception as exc:
            raise ArtifactIngestionError(
                f"Malformed durable lease file '{lease_file.name}': unparseable or naive {ts_field} timestamp: {exc}"
            ) from exc

    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': missing or invalid pid"
        )

    digests = data.get("active_digests")
    if not isinstance(digests, list):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': active_digests must be a list"
        )
    for d_digest in digests:
        if not isinstance(d_digest, str) or not re.match(r"^[a-f0-9]{64}$", d_digest):
            raise ArtifactIngestionError(
                f"Malformed durable lease file '{lease_file.name}': malformed active_digests entry '{d_digest}'"
            )

    receipts = data.get("active_receipts")
    if not isinstance(receipts, list):
        raise ArtifactIngestionError(
            f"Malformed durable lease file '{lease_file.name}': active_receipts must be a list"
        )
    for r_id in receipts:
        if not isinstance(r_id, str):
            raise ArtifactIngestionError(
                f"Malformed durable lease file '{lease_file.name}': malformed active_receipts entry '{r_id}'"
            )
        try:
            validate_receipt_id_format(r_id)
        except Exception as exc:
            raise ArtifactIngestionError(
                f"Malformed durable lease file '{lease_file.name}': malformed active_receipts entry '{r_id}': {exc}"
            ) from exc

    return data


class UniversalArtifactStore:
    """Content-addressed artifact store, quarantine, and retention manager."""

    def __init__(
        self,
        root_dir: str | Path,
        storage_adapter: ArtifactStorageAdapter | None = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.receipts_dir = self.root_dir / "receipts"
        self.quarantine_dir = self.root_dir / "quarantine"
        self.proof_logs_dir = self.root_dir / "proof_logs"
        self.leases_dir = self.root_dir / "leases"

        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.proof_logs_dir.mkdir(parents=True, exist_ok=True)
        self.leases_dir.mkdir(parents=True, exist_ok=True)

        # Enforce 0700 permissions on all directories
        for d in (
            self.root_dir,
            self.receipts_dir,
            self.quarantine_dir,
            self.proof_logs_dir,
            self.leases_dir,
        ):
            os.chmod(d, 0o700)
            assert (d.stat().st_mode & 0o777) == 0o700

        self.storage_adapter = storage_adapter or LocalArtifactStorageAdapter(
            self.root_dir
        )
        self._lock = CrossProcessLock(self.root_dir / ".store.lock")

    def acquire_lease(
        self,
        holder_id: str | None = None,
        active_digests: Sequence[str] = (),
        active_receipts: Sequence[str] = (),
        ttl_seconds: float = 30.0,
        lease_token: str | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        """Acquire or renew a cross-process lease protecting active digests and receipts from GC."""
        # Validate input parameters before lock
        if holder_id is not None:
            if not isinstance(holder_id, str) or not holder_id.strip():
                raise ArtifactIngestionError("holder_id must be a non-empty string")
            if any(ord(c) < 32 or ord(c) == 127 for c in holder_id):
                raise ArtifactIngestionError("holder_id contains control characters")
            if ".." in holder_id or "/" in holder_id or "\\" in holder_id:
                raise ArtifactIngestionError(
                    "holder_id contains directory traversal hazards"
                )
            if "://" in holder_id or re.search(
                r"[a-zA-Z0-9_.-]+(?::[^@]*)?@", holder_id
            ):
                raise ArtifactIngestionError(
                    "holder_id contains URL or userinfo structure"
                )
            if not re.match(r"^[a-zA-Z0-9_.-]{1,128}$", holder_id):
                raise ArtifactIngestionError(f"Malformed holder_id: '{holder_id}'")

        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ArtifactIngestionError("ttl_seconds must be a finite positive number")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ArtifactIngestionError("ttl_seconds must be a finite positive number")

        for r_id in active_receipts:
            if not isinstance(r_id, str):
                raise ArtifactIngestionError(f"Malformed active receipt ID: '{r_id}'")
            validate_receipt_id_format(r_id)

        for d_digest in active_digests:
            if not isinstance(d_digest, str) or not re.match(
                r"^[a-f0-9]{64}$", d_digest
            ):
                raise ArtifactIngestionError(
                    f"Malformed active content digest: '{d_digest}'"
                )

        if lease_token is not None:
            if not isinstance(lease_token, str) or not re.match(
                r"^[a-zA-Z0-9_.-]{8,128}$", lease_token
            ):
                raise ArtifactIngestionError("Malformed lease_token")

        if expected_generation is not None:
            if (
                isinstance(expected_generation, bool)
                or not isinstance(expected_generation, int)
                or expected_generation < 1
            ):
                raise ArtifactIngestionError(
                    "expected_generation must be a positive integer"
                )

        with self._lock:
            holder = holder_id or f"holder_{os.getpid()}_{uuid.uuid4().hex}"
            lease_file = self.leases_dir / f"{holder}.json"
            now = datetime.datetime.now(datetime.timezone.utc)
            now_iso = now.isoformat()
            expires_at = (now + datetime.timedelta(seconds=ttl_seconds)).isoformat()

            existing_lease: dict[str, Any] | None = None
            if lease_file.is_file():
                existing_lease = _validate_and_parse_lease_file(
                    lease_file, self.leases_dir, expected_holder_id=holder
                )

            if existing_lease is not None:
                exp_str = existing_lease["expires_at"]
                exp_dt = datetime.datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                is_expired = exp_dt <= now

                if not is_expired:
                    # Existing active lease! CAS check required.
                    existing_token = existing_lease["lease_token"]
                    existing_gen = existing_lease["generation"]

                    if not lease_token or lease_token != existing_token:
                        raise ArtifactIngestionError(
                            "Lease acquisition/renewal rejected: missing or stale lease_token on existing active lease"
                        )
                    if expected_generation is None:
                        raise ArtifactIngestionError(
                            "Lease renewal rejected: missing expected_generation on existing active lease"
                        )
                    if expected_generation != existing_gen:
                        raise ArtifactIngestionError(
                            f"Lease renewal rejected: expected generation {expected_generation} does not match current generation {existing_gen}"
                        )

                    # Renewal / mutation under compare-and-swap
                    new_gen = existing_gen + 1
                    lease_data = {
                        "holder_id": holder,
                        "lease_token": existing_token,
                        "generation": new_gen,
                        "created_at": existing_lease.get("created_at", now_iso),
                        "updated_at": now_iso,
                        "expires_at": expires_at,
                        "pid": os.getpid(),
                        "active_digests": list(active_digests),
                        "active_receipts": list(active_receipts),
                    }
                    safe_atomic_write_text(lease_file, json.dumps(lease_data, indent=2))
                    return lease_data
                else:
                    # Target lease is expired. Expired-holder mutation fails closed!
                    if lease_token is not None:
                        raise ArtifactIngestionError(
                            "Lease mutation rejected: target lease is expired"
                        )
                    if expected_generation is not None:
                        raise ArtifactIngestionError(
                            "Lease acquisition rejected: target lease is expired"
                        )
                    # Unlink expired lease file and allow fresh acquisition
                    lease_file.unlink(missing_ok=True)

            # First acquisition (minted initial identity)
            if lease_token is not None:
                raise ArtifactIngestionError(
                    "Lease acquisition rejected: caller-supplied lease_token not allowed on initial acquisition"
                )
            if expected_generation is not None:
                raise ArtifactIngestionError(
                    "Lease acquisition rejected: caller-supplied expected_generation not allowed on initial acquisition"
                )

            token = uuid.uuid4().hex
            gen = 1
            lease_data = {
                "holder_id": holder,
                "lease_token": token,
                "generation": gen,
                "created_at": now_iso,
                "updated_at": now_iso,
                "expires_at": expires_at,
                "pid": os.getpid(),
                "active_digests": list(active_digests),
                "active_receipts": list(active_receipts),
            }
            safe_atomic_write_text(lease_file, json.dumps(lease_data, indent=2))
            return lease_data

    def renew_lease(
        self,
        holder_id: str,
        lease_token: str,
        expected_generation: int | None = None,
        active_digests: Sequence[str] = (),
        active_receipts: Sequence[str] = (),
        ttl_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Renew/mutate an existing active lease under exact-token compare-and-swap semantics."""
        return self.acquire_lease(
            holder_id=holder_id,
            active_digests=active_digests,
            active_receipts=active_receipts,
            ttl_seconds=ttl_seconds,
            lease_token=lease_token,
            expected_generation=expected_generation,
        )

    def release_lease(
        self,
        holder_id: str,
        lease_token: str,
        expected_generation: int | None = None,
    ) -> bool:
        """Release a cross-process lease if token and generation match. Replay-safe."""
        if not isinstance(holder_id, str) or not holder_id.strip():
            raise ArtifactIngestionError("holder_id must be a non-empty string")
        if any(ord(c) < 32 or ord(c) == 127 for c in holder_id):
            raise ArtifactIngestionError("holder_id contains control characters")
        if ".." in holder_id or "/" in holder_id or "\\" in holder_id:
            raise ArtifactIngestionError(
                "holder_id contains directory traversal hazards"
            )
        if "://" in holder_id or re.search(r"[a-zA-Z0-9_.-]+(?::[^@]*)?@", holder_id):
            raise ArtifactIngestionError("holder_id contains URL or userinfo structure")
        if not re.match(r"^[a-zA-Z0-9_.-]{1,128}$", holder_id):
            raise ArtifactIngestionError(f"Malformed holder_id: '{holder_id}'")

        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ArtifactIngestionError("lease_token must be a non-empty string")
        if not re.match(r"^[a-zA-Z0-9_.-]{8,128}$", lease_token):
            raise ArtifactIngestionError("Malformed lease_token")

        if (
            expected_generation is None
            or isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 1
        ):
            raise ArtifactIngestionError(
                "expected_generation must be a positive integer"
            )

        with self._lock:
            lease_file = self.leases_dir / f"{holder_id}.json"
            if not lease_file.is_file():
                # Replay-safe: already released or expired
                return True

            ldata = _validate_and_parse_lease_file(
                lease_file, self.leases_dir, expected_holder_id=holder_id
            )

            existing_token = ldata["lease_token"]
            existing_gen = ldata["generation"]

            if existing_token != lease_token:
                raise ArtifactIngestionError(
                    "Lease release rejected: lease_token mismatch (stale release attempt)"
                )

            if existing_gen != expected_generation:
                raise ArtifactIngestionError(
                    f"Lease release rejected: expected generation {expected_generation} does not match current generation {existing_gen}"
                )

            lease_file.unlink(missing_ok=True)
            return True

    def _compute_evidence_digest(
        self,
        content_digest: str,
        storage_id: str,
        source_commit: str,
        command_env: dict[str, Any],
    ) -> str:
        # Retain old method signature for compatibility if needed, but we use receipt integrity digest primarily
        canonical_str = json.dumps(
            {
                "command_env": command_env,
                "content_digest": content_digest,
                "source_commit": source_commit,
                "storage_id": storage_id,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def _validate_ingestion_input(
        self,
        content: bytes,
        metadata: dict[str, Any],
    ) -> list[str]:
        errors: list[str] = []

        if not isinstance(metadata, dict):
            errors.append("Metadata must be a dictionary")
            return errors

        # Strict closed-property contract on metadata properties
        allowed_keys = {
            "command_env",
            "source_commit",
            "claimed_digest",
            "sha256",
            "claimed_size",
            "byte_size",
            "media_type",
            "relative_path",
            "filename",
            "path",
            "task_index",
        }
        for k in metadata:
            if k not in allowed_keys:
                errors.append(f"Unknown metadata property: '{k}'")

        # Check required command_env
        if "command_env" not in metadata:
            errors.append("command_env must be present in metadata")
        else:
            command_env = metadata.get("command_env")
            if not isinstance(command_env, dict):
                errors.append(
                    "command_env must be a dict containing 'command' and 'environment'"
                )
            else:
                for k in command_env:
                    if k not in {"command", "environment"}:
                        errors.append(f"Unknown nested command_env property: '{k}'")

                if "command" not in command_env:
                    errors.append("command_env must contain 'command'")
                elif not isinstance(command_env["command"], str):
                    errors.append("command_env['command'] must be a string")

                if "environment" not in command_env:
                    errors.append("command_env must contain 'environment'")
                elif not isinstance(command_env["environment"], dict):
                    errors.append("command_env['environment'] must be a dictionary")
                else:
                    for env_k, env_v in command_env["environment"].items():
                        if not isinstance(env_k, str):
                            errors.append(
                                f"command_env['environment'] key '{env_k}' must be a string"
                            )
                        if not isinstance(env_v, str):
                            errors.append(
                                f"command_env['environment'] value for key '{env_k}' must be a string"
                            )

        # Check source_commit
        if "source_commit" not in metadata:
            errors.append("source_commit must be present in metadata")
        else:
            source_commit = metadata.get("source_commit")
            if not isinstance(source_commit, str) or not source_commit.strip():
                errors.append("source_commit must be a non-empty string")
            elif not re.match(r"^[a-zA-Z0-9_-]{5,40}$", source_commit):
                errors.append(f"Malformed source commit: '{source_commit}'")

        # media_type
        if "media_type" in metadata:
            media = metadata["media_type"]
            if not isinstance(media, str) or not media.strip():
                errors.append("media_type must be a non-empty string")

        # claimed_digest and sha256
        for key in ("claimed_digest", "sha256"):
            if key in metadata:
                val = metadata[key]
                if not isinstance(val, str) or not re.match(r"^[a-f0-9]{64}$", val):
                    errors.append(f"{key} must be a 64-character hex string")

        # claimed_size and byte_size
        for key in ("claimed_size", "byte_size"):
            if key in metadata:
                val = metadata[key]
                if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                    errors.append(f"{key} must be a non-negative integer")

        # Path safety checks on metadata path properties
        for key in ("relative_path", "filename", "path"):
            if key in metadata:
                val = metadata[key]
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"{key} must be a non-empty string")
                else:
                    safe, reason = is_safe_path(val)
                    if not safe:
                        errors.append(
                            f"Unsafe path hazard in metadata '{key}': {reason}"
                        )

        # task_index
        if "task_index" in metadata:
            val = metadata["task_index"]
            if not isinstance(val, int) or isinstance(val, bool):
                errors.append("task_index must be an integer")

        return errors

    def ingest_bytes(
        self,
        content: bytes,
        metadata: dict[str, Any],
        access_class: AccessClass | str = AccessClass.PRIVATE,
        parent_artifact_id: str | None = None,
        merge_evidence_refs: Sequence[str] = (),
    ) -> ArtifactReceipt:
        """Ingest raw content bytes into the content-addressed store.

        Calculates actual content SHA-256 digest and byte size from content bytes.
        Fails closed or quarantines on integrity mismatch, secret exposure, or path hazards.
        """
        if not isinstance(content, (bytes, bytearray)):
            raise ArtifactIngestionError("Content must be bytes or bytearray")

        actual_content_bytes = bytes(content)
        actual_digest = hashlib.sha256(actual_content_bytes).hexdigest()
        actual_size = len(actual_content_bytes)

        if access_class not in (
            AccessClass.PRIVATE,
            AccessClass.PUBLIC,
            "private",
            "public",
        ):
            raise ArtifactIngestionError(f"Invalid access class: '{access_class}'")

        access = (
            AccessClass(access_class) if isinstance(access_class, str) else access_class
        )

        # 1. Reject credential-bearing metadata/content immediately before any raw persistence
        secret_errors = []
        try:
            content_str = actual_content_bytes.decode("utf-8", errors="ignore")
            if SECRET_VALUE_RE.search(content_str):
                secret_errors.append(
                    "Content contains secret-like content [REDACTED_SECRET]"
                )
        except Exception:
            pass

        meta_secret_errors = find_secrets(metadata)
        if meta_secret_errors:
            secret_errors.extend(meta_secret_errors)

        if secret_errors:
            raise ArtifactIngestionError(
                f"Ingestion rejected: {'; '.join(secret_errors)}"
            )

        # Strict validation of optional parent_artifact_id and merge_evidence_refs
        if parent_artifact_id is not None:
            validate_receipt_id_format(parent_artifact_id)
        for ref in merge_evidence_refs:
            if not isinstance(ref, str) or not re.match(r"^[a-zA-Z0-9_.-]{3,64}$", ref):
                raise ArtifactIngestionError(f"Malformed merge evidence ref: '{ref}'")

        # Validate metadata safety
        validation_errors = self._validate_ingestion_input(
            actual_content_bytes, metadata
        )

        # Check producer claim mismatches
        claimed_digest = metadata.get("claimed_digest") or metadata.get("sha256")
        if claimed_digest and str(claimed_digest).lower() != actual_digest:
            validation_errors.append(
                f"Content digest mismatch: claimed '{claimed_digest}', actual computed '{actual_digest}'"
            )

        claimed_size = metadata.get("claimed_size") or metadata.get("byte_size")
        if claimed_size is not None:
            try:
                claimed_size_int = int(claimed_size)
            except (ValueError, TypeError):
                validation_errors.append(f"Malformed claimed size: '{claimed_size}'")
                claimed_size_int = None
            if claimed_size_int is not None and claimed_size_int != actual_size:
                validation_errors.append(
                    f"Content size mismatch: claimed {claimed_size}, actual {actual_size}"
                )

        media_type = metadata.get("media_type") or "application/octet-stream"
        if not isinstance(media_type, str) or not media_type.strip():
            validation_errors.append("media_type must be a non-empty string")

        source_commit = str(metadata.get("source_commit", "unknown"))
        command_env = metadata.get(
            "command_env", {"command": "unknown", "environment": {}}
        )
        if not isinstance(command_env, dict):
            command_env = {"command": str(command_env), "environment": {}}

        # If validation errors occurred -> Quarantine & Fail closed
        if validation_errors:
            quarantine_reason = "; ".join(
                sanitize_error_message(e) for e in validation_errors
            )
            quarantine_receipt = self._quarantine_artifact(
                content=actual_content_bytes,
                content_digest=actual_digest,
                byte_size=actual_size,
                media_type=str(media_type),
                source_commit=source_commit,
                command_env=command_env,
                quarantine_reason=quarantine_reason,
                parent_artifact_id=parent_artifact_id,
            )
            raise ArtifactIngestionError(
                f"Ingestion quarantined: {quarantine_reason}",
                details=quarantine_receipt.to_dict(),
            )

        with self._lock:
            # Handle parent artifact lookup
            clean_parent_id = None
            if parent_artifact_id:
                parent_receipt = self._get_receipt_unlocked(parent_artifact_id)
                if parent_receipt:
                    clean_parent_id = parent_receipt.receipt_id

            # Compute deterministic receipt identity
            receipt_id = compute_deterministic_receipt_id(
                content_digest=actual_digest,
                source_commit=source_commit,
                command_env=command_env,
                media_type=str(media_type),
                access_class=access,
                parent_artifact_id=clean_parent_id,
                merge_evidence_refs=merge_evidence_refs,
            )

            # Converge concurrent identical inputs
            existing_receipt = self._get_receipt_unlocked(receipt_id)
            if existing_receipt:
                return existing_receipt

            # Store bytes content-addressed (deduplicates only the blob)
            storage_id = self.storage_adapter.store_bytes(
                actual_digest, actual_content_bytes
            )

            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # For derivatives: do not mutate parent receipt. Instead write append-only lineage event.
            if clean_parent_id:
                self._write_lineage_event_unlocked(clean_parent_id, receipt_id)

            # Create receipt with placeholder evidence_digest
            draft_receipt = ArtifactReceipt(
                receipt_id=receipt_id,
                content_digest=actual_digest,
                storage_id=storage_id,
                source_commit=source_commit,
                command_env=command_env,
                evidence_digest="",
                byte_size=actual_size,
                media_type=str(media_type),
                access_class=access,
                status=ArtifactStatus.INGESTED,
                is_quarantined=False,
                quarantine_reason=None,
                parent_artifact_id=clean_parent_id,
                derivative_ids=(),
                merge_evidence_refs=tuple(merge_evidence_refs),
                created_at=created_at,
            )

            # Compute integrity digest binding all fields
            evidence_digest = compute_receipt_integrity_digest(draft_receipt.to_dict())

            receipt = ArtifactReceipt(
                receipt_id=receipt_id,
                content_digest=actual_digest,
                storage_id=storage_id,
                source_commit=source_commit,
                command_env=command_env,
                evidence_digest=evidence_digest,
                byte_size=actual_size,
                media_type=str(media_type),
                access_class=access,
                status=ArtifactStatus.INGESTED,
                is_quarantined=False,
                quarantine_reason=None,
                parent_artifact_id=clean_parent_id,
                derivative_ids=(),
                merge_evidence_refs=tuple(merge_evidence_refs),
                created_at=created_at,
            )

            # Validate generated receipt against JSON Schema
            schema_errors = validate_artifact_receipt(receipt.to_dict())
            if schema_errors:
                err_msg = "; ".join(schema_errors)
                raise ArtifactIngestionError(
                    f"Generated receipt violated schema: {err_msg}"
                )

            self._save_receipt_unlocked(receipt)
            self._write_proof_log_unlocked("INGEST", receipt)
            return receipt

    def ingest_file(
        self,
        file_path: str | Path,
        metadata: dict[str, Any],
        access_class: AccessClass | str = AccessClass.PRIVATE,
        parent_artifact_id: str | None = None,
        merge_evidence_refs: Sequence[str] = (),
    ) -> ArtifactReceipt:
        """Ingest a file from the local filesystem safely."""
        path_str = str(file_path)
        if any(ord(c) < 32 or ord(c) == 127 for c in path_str):
            raise ArtifactIngestionError("Source path contains control characters")
        if "://" in path_str or re.search(r"[a-zA-Z0-9_.-]+(?::[^@]*)?@", path_str):
            raise ArtifactIngestionError(
                "Source path contains URL or userinfo structure"
            )

        path = Path(file_path)
        if ".." in path.parts:
            raise ArtifactIngestionError(
                "Source path contains '..' directory traversal segment"
            )

        if not path.exists():
            raise ArtifactIngestionError(f"Source file does not exist: {file_path}")

        if path.is_symlink():
            raise ArtifactIngestionError(f"Symlink input hazard detected: {file_path}")

        try:
            content = safe_read_bytes_nofollow(path)
        except Exception as exc:
            raise ArtifactIngestionError(f"Failed to read source file: {exc}")

        # Auto-populate filename if missing
        file_meta = dict(metadata)
        if "filename" not in file_meta:
            file_meta["filename"] = path.name

        return self.ingest_bytes(
            content=content,
            metadata=file_meta,
            access_class=access_class,
            parent_artifact_id=parent_artifact_id,
            merge_evidence_refs=merge_evidence_refs,
        )

    def _quarantine_artifact(
        self,
        content: bytes,
        content_digest: str,
        byte_size: int,
        media_type: str,
        source_commit: str,
        command_env: dict[str, Any],
        quarantine_reason: str,
        parent_artifact_id: str | None,
    ) -> ArtifactReceipt:
        """Quarantine an artifact into isolated storage without making bytes publicly accessible."""
        with self._lock:
            # Deterministic quarantine identity
            receipt_id = compute_deterministic_quarantine_id(
                content_digest=content_digest,
                source_commit=source_commit,
                command_env=command_env,
                media_type=media_type,
                quarantine_reason=quarantine_reason,
                parent_artifact_id=parent_artifact_id,
            )

            existing = self._get_receipt_unlocked(receipt_id)
            if existing:
                return existing

            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            quarantine_blob_path = self.quarantine_dir / f"{receipt_id}.bin"
            safe_verify_path(self.quarantine_dir, quarantine_blob_path)
            safe_atomic_write_bytes(quarantine_blob_path, content)

            storage_id = f"quarantine://{receipt_id}"
            safe_reason = sanitize_error_message(quarantine_reason)

            draft_receipt = ArtifactReceipt(
                receipt_id=receipt_id,
                content_digest=content_digest,
                storage_id=storage_id,
                source_commit=source_commit,
                command_env=command_env,
                evidence_digest="",
                byte_size=byte_size,
                media_type=media_type,
                access_class=AccessClass.PRIVATE,  # NEVER public
                status=ArtifactStatus.QUARANTINED,
                is_quarantined=True,
                quarantine_reason=safe_reason,
                parent_artifact_id=parent_artifact_id,
                derivative_ids=(),
                merge_evidence_refs=(),
                created_at=created_at,
            )

            evidence_digest = compute_receipt_integrity_digest(draft_receipt.to_dict())

            receipt = ArtifactReceipt(
                receipt_id=receipt_id,
                content_digest=content_digest,
                storage_id=storage_id,
                source_commit=source_commit,
                command_env=command_env,
                evidence_digest=evidence_digest,
                byte_size=byte_size,
                media_type=media_type,
                access_class=AccessClass.PRIVATE,
                status=ArtifactStatus.QUARANTINED,
                is_quarantined=True,
                quarantine_reason=safe_reason,
                parent_artifact_id=parent_artifact_id,
                derivative_ids=(),
                merge_evidence_refs=(),
                created_at=created_at,
            )

            self._save_receipt_unlocked(receipt)
            self._write_proof_log_unlocked("QUARANTINE", receipt)
            return receipt

    def get_receipt(self, receipt_id: str) -> ArtifactReceipt:
        """Get an artifact receipt by ID."""
        validate_receipt_id_format(receipt_id)
        with self._lock:
            receipt = self._get_receipt_unlocked(receipt_id)
            if not receipt:
                raise ArtifactNotFoundError(receipt_id)
            return receipt

    def get_content(
        self,
        receipt_id: str,
        requester_access: AccessClass | str = AccessClass.PUBLIC,
    ) -> bytes:
        """Retrieve stored artifact bytes. Fails closed if quarantined or private without access."""
        req_access = (
            AccessClass(requester_access)
            if isinstance(requester_access, str)
            else requester_access
        )
        receipt = self.get_receipt(receipt_id)

        if receipt.is_quarantined:
            raise ArtifactAccessDeniedError(
                f"Artifact '{receipt_id}' is quarantined and cannot be retrieved."
            )

        if (
            receipt.access_class == AccessClass.PRIVATE
            and req_access != AccessClass.PRIVATE
        ):
            raise ArtifactAccessDeniedError(
                f"Artifact '{receipt_id}' is private and requires private access level."
            )

        return self.storage_adapter.read_bytes(receipt.content_digest)

    def _save_receipt_unlocked(self, receipt: ArtifactReceipt) -> None:
        receipt_path = self.receipts_dir / f"{receipt.receipt_id}.json"
        safe_verify_path(self.receipts_dir, receipt_path)
        safe_atomic_write_text(receipt_path, receipt.to_json())
        assert (receipt_path.stat().st_mode & 0o777) == 0o600

    def _get_receipt_unlocked(self, receipt_id: str) -> ArtifactReceipt | None:
        validate_receipt_id_format(receipt_id)
        receipt_path = self.receipts_dir / f"{receipt_id}.json"
        if not receipt_path.is_file():
            return None
        safe_verify_path(self.receipts_dir, receipt_path)
        try:
            text = safe_read_text_nofollow(receipt_path)
            data = json.loads(text)

            # Strict validation of receipt properties before load/parse
            errors = validate_artifact_receipt(data)
            if errors:
                raise ArtifactIngestionError(
                    f"Receipt validation failed: {'; '.join(errors)}"
                )

            # Assert types of critical fields explicitly
            for field in (
                "receipt_id",
                "content_digest",
                "storage_id",
                "source_commit",
                "media_type",
                "created_at",
            ):
                if not isinstance(data.get(field), str):
                    raise ArtifactIngestionError(
                        f"Malformed receipt field '{field}' type"
                    )
            if not isinstance(data.get("command_env"), dict):
                raise ArtifactIngestionError(
                    "Malformed receipt field 'command_env' type"
                )

            receipt = ArtifactReceipt.from_dict(data)

            # 1. Verify evidence digest integrity
            expected_digest = compute_receipt_integrity_digest(receipt.to_dict())
            if receipt.evidence_digest != expected_digest:
                raise ArtifactIngestionError(
                    f"Receipt integrity verification failed: digest mismatch for {receipt_id}"
                )

            # 2. Re-bind receipt identity to compute_deterministic_receipt_id / quarantine_id
            if receipt.receipt_id.startswith("art_"):
                expected_id = compute_deterministic_receipt_id(
                    content_digest=receipt.content_digest,
                    source_commit=receipt.source_commit,
                    command_env=receipt.command_env,
                    media_type=receipt.media_type,
                    access_class=receipt.access_class,
                    parent_artifact_id=receipt.parent_artifact_id,
                    merge_evidence_refs=receipt.merge_evidence_refs,
                )
                if (
                    receipt.receipt_id != expected_id
                    or receipt.receipt_id != receipt_id
                ):
                    raise ArtifactIngestionError(
                        f"Receipt tamper detected: deterministic receipt ID mismatch for '{receipt_id}'"
                    )
            elif receipt.receipt_id.startswith("quar_"):
                expected_id = compute_deterministic_quarantine_id(
                    content_digest=receipt.content_digest,
                    source_commit=receipt.source_commit,
                    command_env=receipt.command_env,
                    media_type=receipt.media_type,
                    quarantine_reason=receipt.quarantine_reason or "",
                    parent_artifact_id=receipt.parent_artifact_id,
                )
                if (
                    receipt.receipt_id != expected_id
                    or receipt.receipt_id != receipt_id
                ):
                    raise ArtifactIngestionError(
                        f"Receipt tamper detected: quarantine ID identity mismatch for '{receipt_id}'"
                    )

            # 3. Re-bind byte_size and content_digest to actual stored bytes
            if not receipt.is_quarantined and self.storage_adapter.exists(
                receipt.content_digest
            ):
                try:
                    stored_bytes = self.storage_adapter.read_bytes(
                        receipt.content_digest
                    )
                    actual_size = len(stored_bytes)
                    actual_digest = hashlib.sha256(stored_bytes).hexdigest()
                    if receipt.byte_size != actual_size:
                        raise ArtifactIngestionError(
                            f"Receipt tamper detected: byte_size claim {receipt.byte_size} does not match stored content size {actual_size}"
                        )
                    if receipt.content_digest != actual_digest:
                        raise ArtifactIngestionError(
                            f"Receipt tamper detected: content digest {receipt.content_digest} does not match stored content digest {actual_digest}"
                        )
                except ArtifactNotFoundError:
                    pass

            # Populate dynamic derivatives list
            derivatives = self._get_derivatives_unlocked(receipt.receipt_id)
            if derivatives:
                receipt = ArtifactReceipt(
                    receipt_id=receipt.receipt_id,
                    content_digest=receipt.content_digest,
                    storage_id=receipt.storage_id,
                    source_commit=receipt.source_commit,
                    command_env=receipt.command_env,
                    evidence_digest=receipt.evidence_digest,
                    byte_size=receipt.byte_size,
                    media_type=receipt.media_type,
                    access_class=receipt.access_class,
                    status=receipt.status,
                    is_quarantined=receipt.is_quarantined,
                    quarantine_reason=receipt.quarantine_reason,
                    parent_artifact_id=receipt.parent_artifact_id,
                    derivative_ids=tuple(derivatives),
                    merge_evidence_refs=receipt.merge_evidence_refs,
                    created_at=receipt.created_at,
                    marker=receipt.marker,
                )

            return receipt
        except ArtifactIngestionError:
            raise
        except Exception as exc:
            raise ArtifactIngestionError(
                f"Failed to load receipt '{receipt_id}': {exc}"
            )

    def _write_proof_log_unlocked(self, action: str, receipt: ArtifactReceipt) -> None:
        log_file = self.proof_logs_dir / "proof_events.jsonl"
        safe_verify_path(self.proof_logs_dir, log_file)
        event = {
            "action": action,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "receipt": receipt.to_dict(),
        }
        event["integrity_digest"] = compute_event_integrity_digest(event)

        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

        os.chmod(log_file, 0o600)
        assert (log_file.stat().st_mode & 0o777) == 0o600

    def _write_lineage_event_unlocked(self, parent_id: str, derivative_id: str) -> None:
        log_file = self.proof_logs_dir / "lineage_events.jsonl"
        safe_verify_path(self.proof_logs_dir, log_file)
        event = {
            "parent_id": parent_id,
            "derivative_id": derivative_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        event["integrity_digest"] = compute_event_integrity_digest(event)

        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

        os.chmod(log_file, 0o600)
        assert (log_file.stat().st_mode & 0o777) == 0o600

    def _get_derivatives_unlocked(self, parent_id: str) -> list[str]:
        validate_receipt_id_format(parent_id)
        log_file = self.proof_logs_dir / "lineage_events.jsonl"
        if not log_file.is_file():
            return []
        safe_verify_path(self.proof_logs_dir, log_file)
        derivatives = []
        try:
            with log_file.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except Exception as exc:
                        raise ArtifactIngestionError(
                            f"Lineage event log corrupted: failed to parse JSON: {exc}"
                        )

                    digest = event.get("integrity_digest")
                    if not digest or digest != compute_event_integrity_digest(event):
                        raise ArtifactIngestionError(
                            "Lineage event log integrity digest verification failed"
                        )

                    if event.get("parent_id") == parent_id:
                        deriv_id = event.get("derivative_id")
                        if deriv_id and deriv_id not in derivatives:
                            derivatives.append(deriv_id)
        except ArtifactIngestionError:
            raise
        except Exception as exc:
            raise ArtifactIngestionError(f"Failed to read lineage event log: {exc}")
        return derivatives

    def run_garbage_collection(self, min_age_seconds: float = 0.0) -> dict[str, Any]:
        """Perform replay-safe, non-destructive retention and garbage collection.

        Fences all artifacts referenced as merge/review evidence or as active parent artifacts.
        """
        with self._lock:
            now = datetime.datetime.now(datetime.timezone.utc)
            all_receipts: dict[str, ArtifactReceipt] = {}

            for p in self.receipts_dir.glob("*.json"):
                try:
                    receipt = self._get_receipt_unlocked(p.stem)
                    if receipt:
                        all_receipts[receipt.receipt_id] = receipt
                except Exception:
                    continue

            # Build set of protected receipt IDs and content digests
            protected_ids: set[str] = set()
            protected_digests: set[str] = set()
            for r in all_receipts.values():
                if r.is_quarantined:
                    protected_ids.add(r.receipt_id)
                if r.merge_evidence_refs:
                    protected_ids.add(r.receipt_id)
                    for ref in r.merge_evidence_refs:
                        protected_ids.add(ref)
                if r.parent_artifact_id:
                    protected_ids.add(r.parent_artifact_id)

            # Protect IDs and digests registered in active process leases
            if self.leases_dir.is_dir():
                for lf in list(self.leases_dir.glob("*.json")):
                    try:
                        text = safe_read_text_nofollow(lf)
                        ldata = json.loads(text)
                        if not isinstance(ldata, dict):
                            continue

                        exp_str = ldata.get("expires_at")
                        if not isinstance(exp_str, str):
                            continue
                        try:
                            exp_dt = datetime.datetime.fromisoformat(
                                exp_str.replace("Z", "+00:00")
                            )
                        except Exception:
                            continue

                        holder_id = ldata.get("holder_id")
                        lease_token = ldata.get("lease_token")
                        generation = ldata.get("generation")
                        if (
                            not isinstance(holder_id, str)
                            or not isinstance(lease_token, str)
                            or not isinstance(generation, int)
                        ):
                            continue

                        active_rcpts = ldata.get("active_receipts")
                        active_digs = ldata.get("active_digests")
                        if not isinstance(active_rcpts, list) or not isinstance(
                            active_digs, list
                        ):
                            continue

                        if exp_dt > now:
                            for r_id in active_rcpts:
                                if isinstance(r_id, str):
                                    protected_ids.add(r_id)
                            for d_digest in active_digs:
                                if isinstance(d_digest, str):
                                    protected_digests.add(d_digest)
                        else:
                            lf.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Compute transitive closure: if A is protected:
            # - protect its parent
            # - protect its derivatives
            # - protect its merge evidence refs
            changed = True
            while changed:
                changed = False
                new_protected = set(protected_ids)
                for r_id in list(protected_ids):
                    r = all_receipts.get(r_id)
                    if r:
                        if (
                            r.parent_artifact_id
                            and r.parent_artifact_id not in new_protected
                        ):
                            new_protected.add(r.parent_artifact_id)
                            changed = True
                        for deriv in r.derivative_ids:
                            if deriv not in new_protected:
                                new_protected.add(deriv)
                                changed = True
                        for ref in r.merge_evidence_refs:
                            if ref not in new_protected:
                                new_protected.add(ref)
                                changed = True
                protected_ids = new_protected

            # Protect content_digests of protected receipts
            for r_id in protected_ids:
                if r_id in all_receipts:
                    protected_digests.add(all_receipts[r_id].content_digest)

            deleted_receipts: list[str] = []
            deleted_blobs: list[str] = []
            retained_count = 0
            fenced_count = len(protected_ids)

            for r in list(all_receipts.values()):
                # Quarantined or protected receipts are retained
                if r.is_quarantined or r.receipt_id in protected_ids:
                    retained_count += 1
                    continue

                # Check age
                try:
                    created_dt = datetime.datetime.fromisoformat(
                        r.created_at.replace("Z", "+00:00")
                    )
                    age = (now - created_dt).total_seconds()
                except Exception:
                    age = 0.0

                if age >= min_age_seconds:
                    # Unlink receipt file
                    receipt_path = self.receipts_dir / f"{r.receipt_id}.json"
                    if receipt_path.is_file():
                        receipt_path.unlink()
                        deleted_receipts.append(r.receipt_id)

                    # Delete blob only if no other active receipt references the content_digest
                    other_refs = [
                        other
                        for other in all_receipts.values()
                        if other.receipt_id != r.receipt_id
                        and other.receipt_id not in deleted_receipts
                        and other.content_digest == r.content_digest
                    ]
                    if not other_refs and r.content_digest not in protected_digests:
                        if self.storage_adapter.delete_bytes(
                            r.content_digest, force=False
                        ):
                            deleted_blobs.append(r.content_digest)
                else:
                    retained_count += 1

            summary = {
                "deleted_receipts": deleted_receipts,
                "deleted_blobs": deleted_blobs,
                "retained_count": retained_count,
                "fenced_merge_evidence_count": fenced_count,
                "marker": UNIVERSAL_ARTIFACT_STORE_MARKER,
            }
            return summary
