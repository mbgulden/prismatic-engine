"""Verifier durable log management, secret redaction, and evidence building.

Enforces:
- Durable log writing and digest calculation (SHA-256)
- Secret redaction across all logs, error messages, and evidence metadata
- Binding of task ID, verifier lineage, candidate/artifact digest, source lineage,
  command/environment, proof class, status, scope, non-claims, timestamp, and explicit reasons
"""

from __future__ import annotations

import datetime
import hashlib
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from prismatic.universal_result_manifest import (
    SECRET_VALUE_RE,
)
from prismatic.verifiers.schemas import (
    CANONICAL_VERIFIER_TYPES,
    UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK,
    validate_verifier_result,
)


def write_durable_log(
    log_text: str,
    log_path: str | Path | None = None,
    log_dir: str | Path = "/tmp",
    prefix: str = "agy-GRO-4114-verify",
) -> tuple[Path, str]:
    """Write secret-safe log text to a durable log file and return (Path, sha256_digest)."""
    # Redact secrets
    sanitized_text = SECRET_VALUE_RE.sub("[REDACTED_SECRET]", log_text)
    # Remove control characters
    sanitized_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", sanitized_text)

    if log_path is not None:
        target_path = Path(log_path).resolve()
    else:
        target_dir = Path(log_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        unique_name = f"{prefix}-{uuid.uuid4().hex[:8]}.log"
        target_path = target_dir / unique_name

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as f:
        f.write(sanitized_text)

    # Compute digest
    sha256 = hashlib.sha256(sanitized_text.encode("utf-8")).hexdigest()
    return target_path, sha256


def build_verifier_result(
    task_id: str,
    verifier_type: str,
    verifier_version: str,
    candidate_digest: str,
    source_lineage: dict[str, Any],
    command: str | list[str],
    environment: dict[str, Any],
    log_path: str | Path,
    log_digest: str,
    proof_class: str,
    status: str,
    scope: dict[str, Any] | list[str],
    non_claims: Sequence[str],
    type_specific_evidence: dict[str, Any],
    unavailable_reason: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a complete verifier result dictionary, applying secret sanitization and schema checks."""
    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Format scope to object
    if isinstance(scope, list):
        scope_obj = {"target_paths": [str(p) for p in scope]}
    elif isinstance(scope, dict):
        scope_obj = dict(scope)
        if "target_paths" not in scope_obj:
            scope_obj["target_paths"] = []
    else:
        scope_obj = {"target_paths": []}

    canonical_type = CANONICAL_VERIFIER_TYPES.get(verifier_type, verifier_type)

    result = {
        "task_id": task_id,
        "verifier_type": canonical_type,
        "verifier_version": verifier_version,
        "candidate_digest": candidate_digest,
        "source_lineage": source_lineage,
        "command": command,
        "environment": environment,
        "log_path": str(log_path),
        "log_digest": log_digest,
        "proof_class": proof_class,
        "status": status,
        "scope": scope_obj,
        "non_claims": list(non_claims),
        "timestamp": timestamp,
        "unavailable_reason": unavailable_reason if status != "pass" else None,
        "type_specific_evidence": type_specific_evidence,
        "marker": UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK,
    }

    # Validate result secret safety and structure
    ok, errors = validate_verifier_result(result)
    if not ok:
        raise ValueError(f"Invalid verifier result generated: {'; '.join(errors)}")

    return result
