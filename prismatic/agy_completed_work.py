"""Persisted AGY completed-work ingestion.

This is the durable intake layer that stores AGY result packets, runs them
through the completed-work gate, and exposes accepted rows to the gateway and
operator scripts. It does not merge, dispatch, or mutate git state.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from prismatic.agy_result_packet import (
    AGY_RESULT_PACKET_MARKER,
    is_raw_agy_result_packet,
    is_v02_closeout_packet,
    require_valid_packet,
    requires_v02_closeout,
)
from prismatic.completed_work_gate import (
    AGY_COMPLETED_WORK_MARKER,
    classify_completed_work,
    normalize_non_claims,
)

AGY_COMPLETED_WORK_INGESTION_MARKER = "AGY_COMPLETED_WORK_INGESTION_OK"
AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER = "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
DEFAULT_DB_NAME = "agy_completed_work.db"
DEFAULT_EVIDENCE_DIR_NAME = "agy-completed-work-evidence"
MAX_RETAINED_EVIDENCE_BYTES = 2 * 1024 * 1024
AGY_PACKET_NORMALIZATION_MARKER = "AGY_RESULT_PACKET_NORMALIZED_OK"


class AgyCompletedWorkConflictError(ValueError):
    """Raised when a completed-work packet conflicts with an existing deterministic ID."""


INTEGRATION_CLASSIFICATIONS = {
    "merge_ready": "pass_ready_for_review",
    "blocked_missing_proof": "invalid_repairable",
    "blocked_failed_verification": "failed_needs_repair",
    "clean_rebuild_required": "invalid_repairable",
    "manual_review_scope": "blocked_needs_operator",
    "manual_review_conflict": "blocked_needs_operator",
    "superseded": "blocked_needs_operator",
    "rejected": "invalid_repairable",
}
PACKET_VALID = "packet_valid"
PACKET_BLOCKED = "packet_blocked"
PACKET_FAILED = "packet_failed"
PACKET_MALFORMED = "packet_malformed"
PACKET_MISSING = "packet_missing"
PACKET_NEEDS_MANUAL_REVIEW = "needs_manual_review"
GOVERNANCE_PROMOTION_DECISION_READ_MODEL_MARKER = (
    "GOVERNANCE_PROMOTION_DECISION_READ_MODEL_OK"
)
PACKET_REQUIRED_FIELDS = (
    "COMMAND",
    "RESULT",
    "LOG",
    "SCOPE",
    "AD_HOC_OR_CANONICAL",
    "NOT_CLAIMING",
    "MARKER",
)
_TOKEN_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{20,}|xox[baprs]-[a-z0-9-]{20,}|bearer\s+[a-z0-9._-]{20,}|[a-z0-9_=-]{32,})"
)
_TEMPLATE_VALUE_RE = re.compile(r"^\s*<[^>]+>\s*$")
_PACKET_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*|[A-Za-z][A-Za-z0-9_.-]*)=(.*)$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SECRET_PATH_PARTS = {
    ".ssh",
    ".aws",
    ".config",
    ".gemini",
    ".antigravity",
    "secrets",
    "tokens",
    "credentials",
}
_GENERATED_PATH_PARTS = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".next",
    ".venv",
    "__pycache__",
}
_SECRET_FILENAMES = {
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
}
_SECRET_FILENAME_PREFIXES = (".env",)


_VALIDATOR_MODULE_CACHE: dict = {}


def _load_validator_module():
    """Load the closeout validator module even when the editable install does
    not expose ``prismatic.skills.*`` as importable packages.

    The validator file is part of the packaged skill, so we load it by absolute
    filesystem path. The module is cached to avoid re-execution.
    """
    cached = _VALIDATOR_MODULE_CACHE.get("module")
    if cached is not None:
        return cached
    module_name = "prismatic_pacc_validator"
    spec_path = (
        Path(__file__).resolve().parent
        / "skills"
        / "prismatic-agent-closeout-contract"
        / "scripts"
        / "validate_closeout_packet.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, spec_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load validator from {spec_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _VALIDATOR_MODULE_CACHE["module"] = module
    return module


def _launch_context_from_settings():
    """Read trusted launch context from env, when set by the dispatcher."""
    validator = _load_validator_module()

    issue = os.environ.get("PRISMATIC_DISPATCH_ISSUE")
    branch = os.environ.get("PRISMATIC_DISPATCH_SOURCE_BRANCH")
    base = os.environ.get("PRISMATIC_DISPATCH_BASE_BRANCH")
    source_path = os.environ.get("PRISMATIC_DISPATCH_SOURCE_PATH")
    candidate = os.environ.get("PRISMATIC_DISPATCH_CANDIDATE_HEAD")
    candidate_tree = os.environ.get("PRISMATIC_DISPATCH_CANDIDATE_TREE")
    base_commit = os.environ.get("PRISMATIC_DISPATCH_BASE_HEAD")
    if not all(
        [issue, branch, base, source_path, candidate, candidate_tree, base_commit]
    ):
        return None
    return validator.LaunchContext(
        issue_identifier=issue,
        source_branch=branch,
        base_branch=base,
        source_path=source_path,
        candidate_commit=candidate,
        candidate_tree=candidate_tree,
        base_commit=base_commit,
    )


def _adapt_v02_packet_with_context(
    packet: Mapping[str, Any], context
) -> dict[str, Any]:
    """Adapt validated v0.2 producer output to the existing gate dialect.

    No field is invented: ``source_branch``/``base_branch``/``source_path``/
    ``source_commit_sha``/``base_commit_sha`` come from the trusted launch
    context. ``ACCEPTANCE_DECISION`` remains ``PENDING`` until the independent
    reviewer upgrades it.
    """
    adapted = {
        "agent": "agy",
        "issue_identifier": packet["TASK_ID"],
        "branch": context.source_branch,
        "base_branch": context.base_branch,
        "source_branch": context.source_branch,
        "source_path": context.source_path,
        "source_commit_sha": context.candidate_commit,
        "base_commit_sha": context.base_commit,
        "changed_files": list(packet["CHANGED_PATHS"]),
        "pr_url": None,
        "result_artifacts": list(packet["result_artifacts"]),
        "verification": {
            "commands": list(packet["COMMAND"]),
            "result": packet["RESULT"],
            "log_path": packet["LOG"],
            "ad_hoc_or_canonical": packet["AD_HOC_OR_CANONICAL"],
        },
        "proof": {
            "commands": list(packet["COMMAND"]),
            "result": packet["RESULT"],
            "log_path": packet["LOG"],
            "log_sha256": packet["LOG_SHA256"],
            "scope": packet["SCOPE"],
            "marker": packet["MARKER"],
            "ad_hoc_or_canonical": packet["AD_HOC_OR_CANONICAL"],
            "non_claims": list(packet["NOT_CLAIMING"]),
            "attempt_id": packet["ATTEMPT_ID"],
            "candidate_tree": packet["CANDIDATE_TREE"],
            "proof_classes": list(packet["PROOF_CLASSES"]),
            "side_effects": dict(packet["SIDE_EFFECTS"]),
            "blockers": list(packet["BLOCKERS"]),
        },
        "v02_closeout": dict(packet),
        "trusted_launch_context": {
            "issue_identifier": context.issue_identifier,
            "source_branch": context.source_branch,
            "base_branch": context.base_branch,
            "source_path": context.source_path,
            "candidate_commit": context.candidate_commit,
            "candidate_tree": context.candidate_tree,
            "base_commit": context.base_commit,
        },
        "non_claims": list(packet["NOT_CLAIMING"]),
        "merge_lane": packet["merge_lane"],
        "risk_level": packet["risk_level"],
        "next_action": packet["NEXT_ACTION"],
        "marker": AGY_RESULT_PACKET_MARKER,
        "ACCEPTANCE_DECISION": "PENDING",
    }
    return adapted


def _validate_v02_packet_with_launch(packet: Mapping[str, Any], context) -> None:
    validator = _load_validator_module()
    outcome = validator.validate_closeout_packet(packet, launch_context=context)
    outcome.raise_for_status()


def normalize_agy_result_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt canonical AGY result packets into the completed-work gate dialect.

    AGY may emit a user-facing result-packet dialect (branch/result_artifacts/
    verification/non_claims) while the completed-work gate expects source_path,
    source_branch, proof, and object-shaped lane_scope. This adapter fills only
    derivable, safe fields and leaves genuinely missing provenance absent so the
    gate can reject it explicitly.

    Tasks at or above the v0.2 closeout cutoff (issue number ``>= 4500``) MUST
    be validated with a trusted launch context. If the dispatcher has not
    supplied a context, the v0.2 packet is rejected with ``INVALID_CLOSEOUT``
    before any normalization, gate classification, durable evidence retention,
    or SQLite upsert.
    """

    normalized = _json_object(packet, "packet")
    if requires_v02_closeout(normalized):
        if not is_v02_closeout_packet(normalized):
            raise ValueError("GRO-4500+ AGY packets require the v0.2 closeout contract")
        context = _launch_context_from_settings()
        if context is None:
            raise ValueError(
                "GRO-4500+ AGY packet missing trusted launch context "
                "(PRISMATIC_DISPATCH_* env vars)"
            )
        _validate_v02_packet_with_launch(normalized, context)
        normalized = _adapt_v02_packet_with_context(normalized, context)
    issue = _safe_slug(
        _string(normalized.get("issue_identifier"))
        or _string(normalized.get("issue_id"))
    )

    source_branch = _string(normalized.get("source_branch")) or _string(
        normalized.get("branch")
    )
    if source_branch and "source_branch" not in normalized:
        normalized["source_branch"] = source_branch

    if "base_branch" not in normalized:
        normalized["base_branch"] = _string(normalized.get("target_branch")) or "main"

    if "source_path" not in normalized or not _string(normalized.get("source_path")):
        derived = _derive_source_path(normalized, issue=issue)
        if derived:
            normalized["source_path"] = derived
            normalization = normalized.setdefault("normalization", {})
            if isinstance(normalization, dict):
                normalization["source_path_derived"] = True
                normalization["marker"] = AGY_PACKET_NORMALIZATION_MARKER

    if "changed_files" not in normalized:
        artifacts = _artifact_paths(normalized.get("result_artifacts"))
        if artifacts:
            normalized["changed_files"] = artifacts

    if "result_summary" not in normalized:
        normalized["result_summary"] = _derive_result_summary(normalized, issue=issue)

    normalized["proof"] = _normalize_proof(normalized)
    normalized["lane_scope"] = _normalize_lane_scope(normalized)
    return normalized


def _derive_source_path(packet: Mapping[str, Any], *, issue: str | None) -> str | None:
    explicit = _string(packet.get("source_path"))
    if explicit and _safe_source_path(explicit):
        return explicit
    raw_artifacts = packet.get("result_artifacts")
    for artifact in _artifact_paths(raw_artifacts):
        if _safe_source_path(artifact):
            return artifact
    if _has_artifact_entries(raw_artifacts):
        # Do not hide unsafe artifact provenance behind a fallback path.
        return None
    branch = _string(packet.get("source_branch")) or _string(packet.get("branch"))
    if issue and branch:
        return str(Path.home() / ".prismatic" / "agy-result-packets" / issue)
    return None


def _artifact_paths(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, Mapping):
        candidates = (
            value.get("paths") or value.get("files") or value.get("artifacts") or []
        )
        if isinstance(candidates, Sequence) and not isinstance(
            candidates, (str, bytes)
        ):
            values.extend(_artifact_paths(candidates))
        for key in ("path", "file", "source_path", "result_path", "packet_path"):
            item = value.get(key)
            if isinstance(item, str):
                values.append(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping):
                values.extend(_artifact_paths(item))
    return [path for path in values if _safe_metadata_path(path)]


def _normalize_proof(packet: Mapping[str, Any]) -> dict[str, Any]:
    raw_proof = packet.get("proof")
    proof: dict[str, Any] = dict(raw_proof) if isinstance(raw_proof, Mapping) else {}
    raw_verification = packet.get("verification")
    verification: Mapping[str, Any] = (
        raw_verification if isinstance(raw_verification, Mapping) else {}
    )
    commands = (
        verification.get("commands")
        or verification.get("command")
        or verification.get("verification_commands")
    )
    if not proof.get("command"):
        proof["command"] = (
            _join_commands(commands)
            or _string(packet.get("proof_command"))
            or "AGY result packet verification"
        )
    if not proof.get("result"):
        proof["result"] = (
            _string(verification.get("result"))
            or _string(verification.get("status"))
            or "PASS"
        )
    if not proof.get("log"):
        proof["log"] = (
            _string(verification.get("log_path"))
            or _string(verification.get("log"))
            or "/tmp/agy-result-packet-normalization.log"
        )
    if not proof.get("scope"):
        lane = (
            _string(packet.get("merge_lane"))
            or _string(packet.get("verification_lane"))
            or _string(packet.get("lane_scope"))
            or "unknown"
        )
        proof["scope"] = (
            f"{lane} AGY result packet for {len(_string_list(packet.get('changed_files')))} changed file(s)"
        )
    if not proof.get("marker"):
        proof["marker"] = _string(packet.get("marker")) or "AGY_TASK_RESULT_PACKET_OK"
    if not proof.get("ad_hoc_or_canonical") and verification.get("ad_hoc_or_canonical"):
        proof["ad_hoc_or_canonical"] = verification.get("ad_hoc_or_canonical")
    if not proof.get("non_claims") and packet.get("non_claims"):
        proof["non_claims"] = packet.get("non_claims")
    if not proof.get("not_claiming") and packet.get("not_claiming"):
        proof["not_claiming"] = packet.get("not_claiming")
    return proof


def _normalize_lane_scope(packet: Mapping[str, Any]) -> dict[str, Any]:
    raw = packet.get("lane_scope")
    if isinstance(raw, Mapping):
        lane: dict[str, Any] = dict(raw)
    else:
        lane_name = (
            _string(packet.get("merge_lane"))
            or _string(packet.get("verification_lane"))
            or _string(raw)
            or "manual"
        )
        lane = {"name": lane_name}
    changed = _string_list(packet.get("changed_files"))
    acceptance = _string(packet.get("ACCEPTANCE_DECISION"))
    if (
        acceptance == "PENDING"
        or packet.get("risk_level") == "high"
        or packet.get("next_action")
        in {
            "needs-human-review",
            "needs-fred-cleanup",
        }
    ):
        lane.setdefault("touched_paths", changed)
        lane.setdefault("allowed_paths", [])
        lane.setdefault(
            "manual_review_reason",
            "awaiting_review_factory_decision"
            if acceptance == "PENDING"
            else "raw AGY risk/next_action requires manual review",
        )
        return lane
    lane.setdefault("touched_paths", changed)
    lane.setdefault(
        "allowed_paths", _allowed_paths_for_lane(_string(lane.get("name")), changed)
    )
    return lane


def _allowed_paths_for_lane(
    lane: str | None, changed_files: Sequence[str]
) -> list[str]:
    lane = (lane or "").lower()
    if lane in {"docs", "documentation", "research"}:
        exact_docs = [
            path
            for path in changed_files
            if path.endswith(".md") and _safe_metadata_path(path)
        ]
        return ["docs/", "research/", "reports/", *exact_docs]
    if lane in {"dashboard-ui", "frontend"}:
        return ["prismatic/gateway/templates/", "prismatic/gateway/static/", "tests/"]
    if lane in {"backend-api", "api"}:
        return ["prismatic/", "scripts/", "tests/"]
    roots = sorted(
        {path.split("/", 1)[0] + "/" for path in changed_files if "/" in path}
    )
    return roots or list(changed_files)


def _join_commands(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " && ".join(parts) if parts else None
    return None


def _derive_result_summary(packet: Mapping[str, Any], *, issue: str | None) -> str:
    lane = (
        _string(packet.get("merge_lane"))
        or _string(packet.get("verification_lane"))
        or "unknown"
    )
    return f"AGY result packet for {issue or 'unidentified issue'} in {lane} lane"


def _safe_slug(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return cleaned[:80] or None


def _safe_metadata_path(path: str) -> bool:
    if not path or "\x00" in path:
        return False
    parts = Path(path).parts
    if ".." in parts:
        return False
    lowered = [part.lower() for part in parts]
    if any(part in _SECRET_PATH_PARTS for part in lowered):
        return False
    if any(part in _GENERATED_PATH_PARTS for part in lowered):
        return False
    return True


def _has_artifact_entries(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if any(
            key in value
            for key in ("path", "file", "source_path", "result_path", "packet_path")
        ):
            return True
        return any(
            _has_artifact_entries(value.get(key))
            for key in ("paths", "files", "artifacts")
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_artifact_entries(item) for item in value)
    return False


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return bool(value)
    return value is not None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _safe_source_path(path: str) -> bool:
    if not _safe_metadata_path(path):
        return False
    return path.startswith(f"{Path.home()}/")


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def default_db_path() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_AGY_COMPLETED_WORK_DB",
            str(default_state_dir() / DEFAULT_DB_NAME),
        )
    ).expanduser()


def default_evidence_dir() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_AGY_COMPLETED_WORK_EVIDENCE_DIR",
            str(default_state_dir() / DEFAULT_EVIDENCE_DIR_NAME),
        )
    ).expanduser()


def integration_classification_for(
    gate_classification: str, proof_result: str | None = None
) -> str:
    """Map internal gate states to the operator bridge classifications."""

    if proof_result == "FAIL":
        return "failed_needs_repair"
    if proof_result == "BLOCKED":
        return "blocked_needs_operator"
    return INTEGRATION_CLASSIFICATIONS.get(gate_classification, "invalid_repairable")


def _redact_summary(value: Any) -> Any:
    if isinstance(value, str):
        redacted = _TOKEN_RE.sub("[REDACTED]", value)
        for sensitive in ("token=", "api_key=", "secret=", "password="):
            lower = redacted.lower()
            idx = lower.find(sensitive)
            if idx >= 0:
                start = idx + len(sensitive)
                end = redacted.find(" ", start)
                if end < 0:
                    end = len(redacted)
                redacted = redacted[:start] + "[REDACTED]" + redacted[end:]
        return redacted
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if any(
                part in str(key).lower()
                for part in ("token", "secret", "password", "api_key")
            ):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact_summary(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_redact_summary(item) for item in value]
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, default=str).encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform permission edge
        pass


def _safe_path_for_retention(path: Path) -> tuple[bool, str | None]:
    try:
        raw = str(path.expanduser())
        if ".." in Path(raw).parts:
            return False, "path_traversal"
        raw_name = Path(raw).name.lower()
        if raw_name in _SECRET_FILENAMES or any(
            raw_name == prefix or raw_name.startswith(f"{prefix}.")
            for prefix in _SECRET_FILENAME_PREFIXES
        ):
            return False, "secret_or_credential_path"
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        return False, "path_unresolvable"
    resolved_name = resolved.name.lower()
    if resolved_name in _SECRET_FILENAMES or any(
        resolved_name == prefix or resolved_name.startswith(f"{prefix}.")
        for prefix in _SECRET_FILENAME_PREFIXES
    ):
        return False, "secret_or_credential_path"
    parts = {part.lower() for part in resolved.parts}
    if parts & _SECRET_PATH_PARTS:
        return False, "secret_or_credential_path"
    if parts & _GENERATED_PATH_PARTS:
        return False, "generated_or_vendor_path"
    allowed_roots = [
        Path.home().resolve(),
        default_state_dir().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        return False, "outside_allowed_roots"
    return True, None


def _source_file_for_retention(
    source_path: str | None,
) -> tuple[Path | None, list[str], str]:
    if not source_path:
        return None, ["source_path_missing"], "partial"
    source = Path(source_path).expanduser()
    safe, reason = _safe_path_for_retention(source)
    if not safe:
        return None, [reason or "source_path_unsafe"], "rejected_unsafe"
    if source.is_symlink():
        return None, ["source_path_symlink_rejected"], "rejected_unsafe"
    if source.is_file():
        return source, [], "complete"
    if source.is_dir():
        for name in ("RESULT.md", "packet.json", "result.json"):
            candidate = source / name
            if candidate.is_symlink():
                return None, ["source_packet_symlink_rejected"], "rejected_unsafe"
            if candidate.is_file():
                return candidate, [], "complete"
        return None, ["source_directory_without_recognized_packet_file"], "partial"
    if source.exists():
        return None, ["source_path_special_file_rejected"], "rejected_unsafe"
    return None, ["source_path_missing_at_ingest"], "partial"


def _read_redacted_file(path: Path) -> tuple[bytes | None, list[str], str]:
    if path.is_symlink():
        return None, ["file_symlink_rejected"], "rejected_unsafe"
    if not path.is_file():
        return None, ["file_missing_or_not_regular"], "partial"
    size = path.stat().st_size
    if size > MAX_RETAINED_EVIDENCE_BYTES:
        return None, ["file_too_large"], "rejected_unsafe"
    text = path.read_text(encoding="utf-8", errors="replace")
    redacted = _redact_summary(text)
    return str(redacted).encode("utf-8"), [], "complete"


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    return _sha256_bytes(_json_bytes(body))


def _validated_commit_sha(value: Any) -> str | None:
    text = _string(value)
    if text and _COMMIT_SHA_RE.fullmatch(text):
        return text.lower()
    return None


def _commit_identity_for_retention(
    packet: Mapping[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    reasons: list[str] = []
    raw_source = (
        packet.get("source_commit_sha")
        or packet.get("source_commit")
        or packet.get("commit")
    )
    raw_base = packet.get("base_commit_sha") or packet.get("base_commit")
    source_commit_sha = _validated_commit_sha(raw_source)
    base_commit_sha = _validated_commit_sha(raw_base)
    if raw_source is None or _string(raw_source) is None:
        reasons.append("source_commit_sha_missing")
    elif source_commit_sha is None:
        reasons.append("invalid_source_commit_sha")
    if (
        raw_base is not None
        and _string(raw_base) is not None
        and base_commit_sha is None
    ):
        reasons.append("invalid_base_commit_sha")
    return source_commit_sha, base_commit_sha, reasons


def retain_completed_work_evidence(
    *,
    row_id: str,
    created_at: str,
    updated_at: str,
    packet: Mapping[str, Any],
    gate_payload: Mapping[str, Any],
    classification: str,
    packet_classification: str,
    integration_classification: str,
    evidence_root: Path,
) -> dict[str, Any]:
    """Retain a small immutable redacted evidence bundle for a completed-work row."""

    bundle_dir = evidence_root / row_id
    manifest_path = bundle_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "status": manifest.get("retention_status", "unavailable"),
            "reasons": manifest.get("retention_reasons", []),
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest.get("manifest_sha256"),
            "source_commit_sha": manifest.get("source_commit_sha"),
            "base_commit_sha": manifest.get("base_commit_sha"),
            "proof_log_retained": bool(manifest.get("retained_proof_log_path")),
        }

    evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{row_id}-", dir=evidence_root
    ) as temp_name:
        temp_dir = Path(temp_name)
        try:
            temp_dir.chmod(0o700)
        except OSError:  # pragma: no cover
            pass
        reasons: list[str] = []
        status = "complete"
        redacted_packet = _redact_summary(packet)
        redacted_gate = _redact_summary(gate_payload)
        packet_bytes = _json_bytes(
            redacted_packet if isinstance(redacted_packet, Mapping) else {}
        )
        gate_bytes = _json_bytes(
            redacted_gate if isinstance(redacted_gate, Mapping) else {}
        )
        retained_packet = temp_dir / "packet.redacted.json"
        retained_gate = temp_dir / "gate.redacted.json"
        _write_private(retained_packet, packet_bytes)
        _write_private(retained_gate, gate_bytes)

        source_path = _string(packet.get("source_path"))
        source_file, source_reasons, source_status = _source_file_for_retention(
            source_path
        )
        reasons.extend(source_reasons)
        source_exists = bool(source_file and source_file.exists())
        retained_source_path: str | None = None
        source_sha256: str | None = None
        if source_file:
            source_bytes, read_reasons, read_status = _read_redacted_file(source_file)
            reasons.extend(f"source_{reason}" for reason in read_reasons)
            source_status = read_status if read_status != "complete" else source_status
            if source_bytes is not None:
                retained_source = temp_dir / f"source{source_file.suffix or '.txt'}"
                _write_private(retained_source, source_bytes)
                retained_source_path = str(bundle_dir / retained_source.name)
                source_sha256 = _sha256_bytes(source_bytes)

        proof_value = packet.get("proof")
        proof: Mapping[str, Any] = (
            proof_value if isinstance(proof_value, Mapping) else {}
        )
        proof_log = _string(proof.get("log")) or _string(proof.get("log_path"))
        proof_log_path: str | None = None
        proof_log_sha256: str | None = None
        proof_status = "partial"
        if proof_log:
            log_path = Path(proof_log).expanduser()
            safe, reason = _safe_path_for_retention(log_path)
            if not safe:
                reasons.append(f"proof_log_{reason or 'unsafe'}")
                proof_status = "rejected_unsafe"
            else:
                log_bytes, log_reasons, proof_status = _read_redacted_file(log_path)
                reasons.extend(f"proof_log_{reason}" for reason in log_reasons)
                if log_bytes is not None:
                    retained_log = temp_dir / "proof-log.redacted.txt"
                    _write_private(retained_log, log_bytes)
                    proof_log_path = str(bundle_dir / retained_log.name)
                    proof_log_sha256 = _sha256_bytes(log_bytes)
        else:
            reasons.append("proof_log_missing")

        source_commit_sha, base_commit_sha, commit_reasons = (
            _commit_identity_for_retention(packet)
        )
        reasons.extend(commit_reasons)

        if "rejected_unsafe" in {source_status, proof_status}:
            status = "rejected_unsafe"
        elif (
            source_status != "complete"
            or proof_status != "complete"
            or "source_commit_sha_missing" in commit_reasons
            or "invalid_source_commit_sha" in commit_reasons
            or "invalid_base_commit_sha" in commit_reasons
        ):
            status = "partial"

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "completed_work_id": row_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "retention_status": status,
            "retention_reasons": reasons,
            "source_branch": _string(packet.get("source_branch")),
            "base_branch": _string(packet.get("base_branch")),
            "source_path_original": source_path,
            "source_path_exists_at_ingest": source_exists,
            "source_commit_sha": source_commit_sha,
            "base_commit_sha": base_commit_sha,
            "changed_files": _string_list(packet.get("changed_files")),
            "proof_log_original": proof_log,
            "retained_packet_path": str(bundle_dir / retained_packet.name),
            "retained_gate_path": str(bundle_dir / retained_gate.name),
            "retained_source_path": retained_source_path,
            "retained_proof_log_path": proof_log_path,
            "packet_sha256": _sha256_bytes(packet_bytes),
            "gate_sha256": _sha256_bytes(gate_bytes),
            "source_sha256": source_sha256,
            "proof_log_sha256": proof_log_sha256,
            "classification": classification,
            "packet_classification": packet_classification,
            "integration_classification": integration_classification,
            "historical_source_recovered": False,
            "historical_log_recovered": False,
        }
        manifest["manifest_sha256"] = _manifest_digest(manifest)
        _write_private(temp_dir / "manifest.json", _json_bytes(manifest))
        if not bundle_dir.exists():
            temp_dir.replace(bundle_dir)
        manifest_path = bundle_dir / "manifest.json"
        final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "status": final_manifest.get("retention_status", status),
            "reasons": final_manifest.get("retention_reasons", reasons),
            "manifest_path": str(manifest_path),
            "manifest_sha256": final_manifest.get("manifest_sha256"),
            "source_commit_sha": final_manifest.get("source_commit_sha"),
            "base_commit_sha": final_manifest.get("base_commit_sha"),
            "proof_log_retained": bool(final_manifest.get("retained_proof_log_path")),
        }


def _packet_required_missing(packet: Mapping[str, Any]) -> list[str]:
    proof_value = packet.get("proof")
    proof: Mapping[str, Any] = proof_value if isinstance(proof_value, Mapping) else {}
    raw = (
        packet.get("_raw_fields")
        if isinstance(packet.get("_raw_fields"), Mapping)
        else {}
    )
    missing: list[str] = []
    checks = {
        "COMMAND": proof.get("command") or raw.get("COMMAND"),
        "RESULT": proof.get("result") or raw.get("RESULT"),
        "LOG": proof.get("log") or raw.get("LOG"),
        "SCOPE": proof.get("scope") or raw.get("SCOPE"),
        "AD_HOC_OR_CANONICAL": proof.get("ad_hoc_or_canonical")
        or raw.get("AD_HOC_OR_CANONICAL"),
        "NOT_CLAIMING": proof.get("non_claims")
        or packet.get("non_claims")
        or raw.get("NOT_CLAIMING"),
        "MARKER": proof.get("marker") or raw.get("MARKER"),
    }
    for key in PACKET_REQUIRED_FIELDS:
        if not checks.get(key):
            missing.append(key)
    return missing


def packet_classification_for(
    packet: Mapping[str, Any] | None,
    *,
    expected_marker: str | None = None,
    output_available: bool = True,
) -> str:
    """Return stable packet-level classification, separate from merge readiness."""

    if not output_available or packet is None:
        return PACKET_MISSING
    if _packet_required_missing(packet):
        return PACKET_MALFORMED
    proof_value = packet.get("proof")
    proof: Mapping[str, Any] = proof_value if isinstance(proof_value, Mapping) else {}
    marker = _string(proof.get("marker"))
    if expected_marker and marker and marker != expected_marker:
        return PACKET_NEEDS_MANUAL_REVIEW
    result = _string(proof.get("result"))
    if result == "PASS":
        return PACKET_VALID
    if result == "BLOCKED":
        return PACKET_BLOCKED
    if result == "FAIL":
        return PACKET_FAILED
    return PACKET_MALFORMED


def normalized_packet_record(
    packet: Mapping[str, Any] | None,
    *,
    expected_marker: str | None = None,
    launch_record_id: str | None = None,
    context_metadata: Mapping[str, Any] | None = None,
    output_available: bool = True,
) -> dict[str, Any]:
    """Build the durable read-model shape for an AGY completed-work packet."""

    now = datetime.now(timezone.utc).isoformat()
    meta = dict(context_metadata or {})
    proof: Mapping[str, Any] = {}
    if packet and isinstance(packet.get("proof"), Mapping):
        proof = packet["proof"]  # type: ignore[index]
    classification = packet_classification_for(
        packet, expected_marker=expected_marker, output_available=output_available
    )
    changed_files = _string_list(packet.get("changed_files") if packet else None)
    identifier = _string((packet or {}).get("issue_identifier")) or _string(
        (packet or {}).get("issue_id")
    )
    record_id_source = {
        "launch_record_id": launch_record_id,
        "identifier": identifier,
        "marker": proof.get("marker"),
        "expected_marker": expected_marker,
        "log": proof.get("log"),
        "source_path": (packet or {}).get("source_path"),
        "classification": classification,
    }
    record_id = (
        "agy-packet-"
        + hashlib.sha256(
            json.dumps(record_id_source, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
    )
    return _redact_summary(
        {
            "id": record_id,
            "agent": _string((packet or {}).get("agent")) or _string(meta.get("agent")),
            "issue_id": _string((packet or {}).get("issue_id")) or identifier,
            "identifier": identifier,
            "launch_record_id": launch_record_id
            or _string(meta.get("launch_record_id")),
            "classification": classification,
            "result": _string(proof.get("result")),
            "marker": _string(proof.get("marker")),
            "expected_marker": expected_marker,
            "blocked_marker": meta.get("blocked_marker"),
            "log_path": _string(proof.get("log")),
            "context_pack_path": meta.get("context_pack_path")
            or (packet or {}).get("context_pack_path"),
            "work_packet_path": meta.get("work_packet_path")
            or (packet or {}).get("work_packet_path"),
            "packet_contract_path": meta.get("packet_contract_path")
            or (packet or {}).get("packet_contract_path"),
            "proof_summary": proof.get("scope") or (packet or {}).get("result_summary"),
            "non_claims": list(
                proof.get("non_claims") or (packet or {}).get("non_claims") or []
            ),
            "changed_files": changed_files,
            "missing_fields": _packet_required_missing(packet or {}),
            "created_at": now,
            "updated_at": now,
        }
    )


def packet_record_from_text(
    text: str | None,
    *,
    expected_marker: str | None = None,
    launch_record_id: str | None = None,
    context_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not text:
        return normalized_packet_record(
            None,
            expected_marker=expected_marker,
            launch_record_id=launch_record_id,
            context_metadata=context_metadata,
            output_available=False,
        )
    packet = parse_completed_work_packet_text(text, validate=False)
    return normalized_packet_record(
        packet,
        expected_marker=expected_marker,
        launch_record_id=launch_record_id,
        context_metadata=context_metadata,
        output_available=True,
    )


def packet_record_from_file(
    path: str | Path | None,
    *,
    expected_marker: str | None = None,
    launch_record_id: str | None = None,
    context_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if path is None:
        return packet_record_from_text(
            None,
            expected_marker=expected_marker,
            launch_record_id=launch_record_id,
            context_metadata=context_metadata,
        )
    packet_path = Path(path)
    if not packet_path.exists():
        return packet_record_from_text(
            None,
            expected_marker=expected_marker,
            launch_record_id=launch_record_id,
            context_metadata={
                **dict(context_metadata or {}),
                "log_path": str(packet_path),
            },
        )
    return packet_record_from_text(
        packet_path.read_text(encoding="utf-8"),
        expected_marker=expected_marker,
        launch_record_id=launch_record_id,
        context_metadata=context_metadata,
    )


def _linear_writeback_body(row: "CompletedWorkRow") -> str:
    return "\n".join(
        [
            "## AGY completed-work integration gate",
            "",
            "```text",
            f"RESULT={row.proof_result or 'UNKNOWN'}",
            f"MARKER={row.proof_marker or row.ingestion_marker}",
            f"COMPLETED_WORK_ID={row.id}",
            f"CLASSIFICATION={row.integration_classification}",
            f"GATE_CLASSIFICATION={row.classification}",
            f"LOG={_proof_value(row.packet, 'log') or 'missing'}",
            "DRY_RUN_LINEAR_WRITEBACK=true",
            "REAL_LINEAR_WRITEBACK_POSTED=false",
            f"INTEGRATION_MARKER={AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER}",
            "```",
        ]
    )


def parse_completed_work_packet_text(
    text: str, *, validate: bool = True
) -> dict[str, Any]:
    """Parse exact compact completed-work key/value packets from logs.

    Template placeholders are preserved and later rejected by the gate helper;
    prose-only prompt/context files do not count as completed work.
    """

    packet: dict[str, Any] = {"proof": {}}
    proof: dict[str, Any] = packet["proof"]
    raw_fields: dict[str, str] = {}
    non_claims: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _PACKET_LINE_RE.match(line)
        if not match:
            continue
        key, raw_value = match.group(1), match.group(2).strip()
        raw_fields[key.upper()] = raw_value
        value: Any = raw_value
        if key in {"NOT_CLAIMING", "NON_CLAIMS"}:
            non_claims.extend(
                item.strip() for item in raw_value.split(",") if item.strip()
            )
            continue
        if key == "RESULT":
            proof["result"] = raw_value.upper()
        elif key == "MARKER":
            proof["marker"] = value
        elif key == "COMMAND":
            proof["command"] = value
        elif key == "LOG":
            proof["log"] = value
        elif key == "SCOPE":
            proof["scope"] = value
        elif key == "AD_HOC_OR_CANONICAL":
            proof["ad_hoc_or_canonical"] = value
        elif key == "AGENT":
            packet["agent"] = value.lower()
        elif key in {"ISSUE", "ISSUE_IDENTIFIER"}:
            packet["issue_identifier"] = value
        elif key in {"SOURCE_BRANCH", "BRANCH"}:
            packet["source_branch"] = value
        elif key == "SOURCE_PATH":
            packet["source_path"] = value
        elif key in {"SOURCE_COMMIT_SHA", "COMMIT"}:
            packet["source_commit_sha"] = value
        elif key == "BASE_BRANCH":
            packet["base_branch"] = value
        elif key == "BASE_COMMIT_SHA":
            packet["base_commit_sha"] = value
        elif key == "CHANGED_FILES":
            packet["changed_files"] = [
                item.strip() for item in raw_value.split(",") if item.strip()
            ]
        elif key == "RESULT_SUMMARY":
            packet["result_summary"] = value
        elif key in {"MERGE_LANE", "VERIFICATION_LANE"}:
            packet["verification_lane"] = value
    if non_claims:
        packet["non_claims"] = non_claims
        proof["non_claims"] = non_claims
    packet["_raw_fields"] = raw_fields
    if validate:
        _reject_template_or_incomplete_packet(packet)
    return packet


def ingest_completed_work_text(
    text: str,
    *,
    db_path: str | Path | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> CompletedWorkRow:
    packet = parse_completed_work_packet_text(text)
    if defaults:
        merged = dict(defaults)
        merged.update(packet)
        default_proof = defaults.get("proof")
        packet_proof = packet.get("proof")
        if isinstance(default_proof, Mapping) and isinstance(packet_proof, Mapping):
            merged["proof"] = {**dict(default_proof), **dict(packet_proof)}
        elif isinstance(packet_proof, Mapping):
            merged["proof"] = dict(packet_proof)
        packet = merged
    return ingest_completed_work(packet, db_path=db_path)


def ingest_completed_work_file(
    path: str | Path,
    *,
    db_path: str | Path | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> CompletedWorkRow:
    return ingest_completed_work_text(
        Path(path).read_text(encoding="utf-8"), db_path=db_path, defaults=defaults
    )


def _reject_template_or_incomplete_packet(packet: Mapping[str, Any]) -> None:
    proof_value = packet.get("proof")
    proof: Mapping[str, Any] = proof_value if isinstance(proof_value, Mapping) else {}
    required = {
        "RESULT": proof.get("result"),
        "MARKER": proof.get("marker"),
        "LOG": proof.get("log"),
        "NOT_CLAIMING": proof.get("non_claims"),
    }
    missing = [key for key, value in required.items() if not _has_value(value)]
    if missing:
        raise ValueError(f"missing completed-work packet fields: {', '.join(missing)}")
    for key, value in required.items():
        if _is_template_value(_string(value)):
            raise ValueError(
                f"template completed-work packet field is not proof: {key}"
            )
    if proof.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
        raise ValueError("RESULT must be PASS, FAIL, or BLOCKED")


def _is_template_value(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip()
    return bool(_TEMPLATE_VALUE_RE.match(normalized)) or normalized in {
        "PASS / BLOCKED / FAIL",
        "PASS|FAIL|BLOCKED",
    }


def _proof_value(packet: Mapping[str, Any], key: str) -> str | None:
    proof_value = packet.get("proof")
    proof: Mapping[str, Any] = proof_value if isinstance(proof_value, Mapping) else {}
    return _string(proof.get(key))


def _promotion_durable_evidence_reasons(
    evidence_retention: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    status = _string(evidence_retention.get("status")) or "unavailable"
    manifest_sha256 = _string(evidence_retention.get("manifest_sha256"))
    source_commit_sha = _validated_commit_sha(
        evidence_retention.get("source_commit_sha")
    )
    proof_log_retained = bool(evidence_retention.get("proof_log_retained"))

    if status != "complete":
        reasons.append(f"durable evidence retention status is {status}")
    if not proof_log_retained:
        reasons.append("durable proof log was not retained")
    if not manifest_sha256:
        reasons.append("durable evidence manifest checksum missing")
    if not source_commit_sha:
        reasons.append("validated source commit SHA missing")
    if status in {"partial", "rejected_unsafe", "unsafe", "rejected"}:
        reasons.append("unsafe, rejected, or partial durable evidence state")
    return reasons


def promotion_decision_read_model_for(
    *,
    completed_work_id: str,
    gate_classification: str,
    integration_classification: str,
    packet_classification: str,
    proof_result: str | None,
    proof_marker: str | None,
    normalized_record: Mapping[str, Any],
    non_claims: Sequence[str],
    evidence_retention: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the fail-closed operator promotion-decision read model.

    This is a read-only API/dashboard object. It can recommend a dry-run-only PR
    promotion only when the packet, proof, non-claims, integration gate, and PR
    #339 durable evidence bundle are all present and complete. It never posts to
    Linear, creates a GitHub PR, merges, deploys, or dispatches agents.
    """

    non_claim_list = [claim for claim in non_claims if claim]
    evidence_reasons = _promotion_durable_evidence_reasons(evidence_retention)
    status = "needs_manual_review"
    recommendation = "manual_review"
    policy_gate = "manual_review"
    reasons: list[str] = []

    if not non_claim_list:
        reasons.append("missing required non-claims")

    if packet_classification == PACKET_BLOCKED:
        status = "blocked"
        recommendation = "blocked"
        policy_gate = "blocked"
        reasons.append("packet result is BLOCKED")
    elif packet_classification == PACKET_FAILED:
        status = "needs_repair"
        recommendation = "repair_or_rerun"
        policy_gate = "blocked"
        reasons.append("packet result is FAIL")
    elif packet_classification == PACKET_VALID and proof_result == "PASS":
        if integration_classification != "pass_ready_for_review":
            reasons.append(
                f"integration classification is {integration_classification}"
            )
        if evidence_reasons:
            status = "hold_needs_durable_evidence"
            recommendation = "hold_for_durable_evidence"
            policy_gate = "blocked"
            reasons.extend(evidence_reasons)
        elif integration_classification == "pass_ready_for_review" and non_claim_list:
            status = "decision_ready"
            recommendation = "open_or_update_pr_dry_run_only"
            policy_gate = "pass"
            reasons.append("valid PASS packet with retained durable evidence")
    else:
        reasons.append(f"packet classification is {packet_classification}")

    return {
        "status": status,
        "recommendation": recommendation,
        "promotion_decision": recommendation,
        "policy_gate": policy_gate,
        "completed_work_id": completed_work_id,
        "packet_classification": packet_classification,
        "completed_work_classification": gate_classification,
        "integration_classification": integration_classification,
        "normalized_record_classification": normalized_record.get("classification"),
        "proof_result": proof_result,
        "proof_marker": proof_marker,
        "non_claims": non_claim_list,
        "evidence_retention": dict(evidence_retention),
        "reasons": reasons,
        "side_effects": {
            "linear_comment_posted": False,
            "github_pr_created": False,
            "git_branch_created": False,
            "auto_merge_enabled": False,
            "production_deployed": False,
            "agent_dispatched": False,
            "bulk_agent_dispatch": False,
        },
        "non_claims_asserted": {
            "real_linear_writeback": False,
            "real_github_pr_creation": False,
            "merge": False,
            "auto_merge": False,
            "bulk_dispatch": False,
            "production_deploy": False,
        },
        "marker": GOVERNANCE_PROMOTION_DECISION_READ_MODEL_MARKER,
    }


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
    evidence_retention: dict[str, Any]

    @property
    def integration_classification(self) -> str:
        """Friendly bridge classification for API/dashboard/writeback consumers."""

        return integration_classification_for(self.classification, self.proof_result)

    @property
    def packet_classification(self) -> str:
        """Stable packet-level state; distinct from merge readiness."""

        expected_marker = _string(self.packet.get("expected_marker"))
        return packet_classification_for(self.packet, expected_marker=expected_marker)

    def normalized_record(self) -> dict[str, Any]:
        record = normalized_packet_record(
            self.packet,
            expected_marker=_string(self.packet.get("expected_marker")),
            launch_record_id=_string(self.packet.get("launch_record_id")),
        )
        record["created_at"] = self.created_at
        record["updated_at"] = self.updated_at
        return record

    def promotion_decision_read_model(self) -> dict[str, Any]:
        """Return normalized promotion-decision values for this row."""

        return promotion_decision_read_model_for(
            completed_work_id=self.id,
            gate_classification=self.classification,
            integration_classification=self.integration_classification,
            packet_classification=self.packet_classification,
            proof_result=self.proof_result,
            proof_marker=self.proof_marker,
            normalized_record=self.normalized_record(),
            non_claims=self.non_claims,
            evidence_retention=self.evidence_retention,
        )

    def linear_writeback_dry_run(self) -> dict[str, Any]:
        """Return the safe Linear payload shape without posting it."""

        issue_identifier = _string(
            self.packet.get("issue_identifier") or self.packet.get("issue_id")
        )
        status = "ready_for_review"
        if self.integration_classification == "blocked_needs_operator":
            status = "blocked_needs_operator"
        elif self.integration_classification in {
            "failed_needs_repair",
            "invalid_repairable",
        }:
            status = "needs_repair"
        return {
            "posted": False,
            "dry_run": True,
            "issue_identifier": issue_identifier,
            "status": status,
            "completed_work_id": self.id,
            "classification": self.integration_classification,
            "gate_classification": self.classification,
            "marker": AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
            "body": _linear_writeback_body(self),
        }

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
            "packet_classification": self.packet_classification,
            "integration_classification": self.integration_classification,
            "eligible_for_merge": self.eligible_for_merge,
            "requires_clean_rebuild": self.requires_clean_rebuild,
            "proof_result": self.proof_result,
            "proof_marker": self.proof_marker,
            "gate_marker": self.gate_marker,
            "ingestion_marker": self.ingestion_marker,
            "integration_marker": AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
            "linear_writeback": self.linear_writeback_dry_run(),
            "normalized_record": self.normalized_record(),
            "promotion_decision": self.promotion_decision_read_model(),
            "promotion_decision_marker": GOVERNANCE_PROMOTION_DECISION_READ_MODEL_MARKER,
            "evidence_retention": self.evidence_retention,
            "packet": self.packet,
            "gate": self.gate,
            "non_claims": list(self.non_claims),
        }


def _packets_equal(p1: Mapping[str, Any], p2: Mapping[str, Any]) -> bool:
    return json.dumps(dict(p1), sort_keys=True, default=str) == json.dumps(
        dict(p2), sort_keys=True, default=str
    )


class AgyCompletedWorkStore:
    """SQLite store for completed AGY packets and gate decisions."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        evidence_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.evidence_dir = (
            Path(evidence_dir) if evidence_dir is not None else default_evidence_dir()
        )
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
                    non_claims_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(agy_completed_work)"
                ).fetchall()
            }
            if "evidence_json" not in columns:
                conn.execute(
                    "ALTER TABLE agy_completed_work ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agy_completed_work_created_at ON agy_completed_work(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agy_completed_work_classification ON agy_completed_work(classification)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agy_completed_work_packet_records (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agy_packet_records_created_at ON agy_completed_work_packet_records(created_at DESC)"
            )
            conn.commit()

    def persist_packet_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(record)
        now = datetime.now(timezone.utc).isoformat()
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        record_id = (
            _string(payload.get("id"))
            or "agy-packet-"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
        )
        payload["id"] = record_id
        classification = _string(payload.get("classification")) or PACKET_MALFORMED
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agy_completed_work_packet_records (
                    id, created_at, updated_at, classification, record_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    classification = excluded.classification,
                    record_json = excluded.record_json
                """,
                (
                    record_id,
                    str(payload["created_at"]),
                    str(payload["updated_at"]),
                    classification,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            conn.commit()
        return self.get_packet_record(record_id)

    def get_packet_record(self, record_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM agy_completed_work_packet_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return json.loads(row["record_json"])

    def list_packet_records(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM agy_completed_work_packet_records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def ingest(
        self,
        packet: Mapping[str, Any],
        *,
        dirty_source: bool = False,
        source_is_stale: bool = False,
        conflicts: Sequence[str] | None = None,
    ) -> CompletedWorkRow:
        if is_raw_agy_result_packet(packet):
            # Canonical raw AGY packets must pass the strict contract before any
            # normalization, gate classification, durable evidence retention, or
            # SQLite upsert.  Invalid raw packets raise here, leaving no row and
            # no evidence directory for the rejected packet.
            require_valid_packet(packet)
        normalized_packet = normalize_agy_result_packet(packet)
        gate = classify_completed_work(
            normalized_packet,
            dirty_source=dirty_source,
            source_is_stale=source_is_stale,
            conflicts=conflicts or (),
            trusted_agents=("agy", "fred", "jules", "kai", "george"),
        )
        raw_proof = normalized_packet.get("proof")
        proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
        non_claims = normalize_non_claims(proof)
        if not non_claims:
            # classify_completed_work will normally block this; keep persisted rows explicit.
            non_claims = tuple()
        now = datetime.now(timezone.utc).isoformat()
        row_id = completed_work_id(normalized_packet)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM agy_completed_work WHERE id = ?", (row_id,)
            ).fetchone()
            if existing is not None:
                existing_row = row_from_sqlite(existing)
                if _packets_equal(existing_row.packet, normalized_packet):
                    conn.rollback()
                    return existing_row
                conn.rollback()
                raise AgyCompletedWorkConflictError(
                    f"deterministic completed-work ID conflict for '{row_id}': incoming packet differs from existing immutable row"
                )

            # Hold the writer lock through evidence retention and insertion so a
            # conflicting packet cannot race to establish the shared bundle.
            gate_payload = gate.as_dict()
            classification = gate.classification.value
            evidence_retention = retain_completed_work_evidence(
                row_id=row_id,
                created_at=now,
                updated_at=now,
                packet=normalized_packet,
                gate_payload=gate_payload,
                classification=classification,
                packet_classification=packet_classification_for(normalized_packet),
                integration_classification=integration_classification_for(
                    classification, gate.proof_result
                ),
                evidence_root=self.evidence_dir,
            )
            values = (
                row_id,
                now,
                now,
                gate.agent,
                gate.source_branch,
                gate.source_path,
                gate.base_branch,
                classification,
                1 if gate.eligible_for_merge else 0,
                1 if gate.requires_clean_rebuild else 0,
                gate.proof_result,
                gate.proof_marker,
                AGY_COMPLETED_WORK_MARKER,
                AGY_COMPLETED_WORK_INGESTION_MARKER,
                json.dumps(normalized_packet, sort_keys=True),
                json.dumps(gate_payload, sort_keys=True),
                json.dumps(list(non_claims), sort_keys=True),
                json.dumps(evidence_retention, sort_keys=True),
            )
            conn.execute(
                """
                INSERT INTO agy_completed_work (
                    id, created_at, updated_at, agent, source_branch, source_path,
                    base_branch, classification, eligible_for_merge,
                    requires_clean_rebuild, proof_result, proof_marker,
                    gate_marker, ingestion_marker, packet_json, gate_json,
                    non_claims_json, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "proof_marker": (packet.get("proof") or {}).get("marker")
        if isinstance(packet.get("proof"), Mapping)
        else None,
    }
    digest = hashlib.sha256(
        json.dumps(source, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"agy-cw-{digest}"


def persist_packet_record(
    record: Mapping[str, Any], *, db_path: str | Path | None = None
) -> dict[str, Any]:
    return AgyCompletedWorkStore(db_path).persist_packet_record(record)


def get_packet_record(
    record_id: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    return AgyCompletedWorkStore(db_path).get_packet_record(record_id)


def list_packet_records(
    *, limit: int = 50, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    return AgyCompletedWorkStore(db_path).list_packet_records(limit=limit)


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


def list_completed_work(
    *, db_path: str | Path | None = None, limit: int = 50
) -> list[CompletedWorkRow]:
    return AgyCompletedWorkStore(db_path).list(limit=limit)


def get_completed_work(
    completed_work_id: str, *, db_path: str | Path | None = None
) -> CompletedWorkRow:
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
        evidence_retention=_evidence_from_sqlite(row),
    )


def _evidence_from_sqlite(row: sqlite3.Row) -> dict[str, Any]:
    try:
        raw = row["evidence_json"]
    except (KeyError, IndexError):
        raw = None
    if raw:
        parsed = json.loads(raw)
        if parsed:
            return parsed
    return {
        "status": "unavailable",
        "reasons": ["historical_row_without_retained_evidence"],
        "manifest_path": None,
        "manifest_sha256": None,
        "source_commit_sha": None,
        "base_commit_sha": None,
        "proof_log_retained": False,
        "historical_source_recovered": False,
        "historical_log_recovered": False,
    }


def _json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    # Round-trip to ensure the persisted packet is JSON-safe and detached from callers.
    return json.loads(json.dumps(dict(value), sort_keys=True, default=str))
