"""Persisted AGY completed-work ingestion.

This is the durable intake layer that stores AGY result packets, runs them
through the completed-work gate, and exposes accepted rows to the gateway and
operator scripts. It does not merge, dispatch, or mutate git state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from prismatic.completed_work_gate import (
    AGY_COMPLETED_WORK_MARKER,
    classify_completed_work,
    normalize_non_claims,
)

AGY_COMPLETED_WORK_INGESTION_MARKER = "AGY_COMPLETED_WORK_INGESTION_OK"
AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER = "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
DEFAULT_DB_NAME = "agy_completed_work.db"
AGY_PACKET_NORMALIZATION_MARKER = "AGY_RESULT_PACKET_NORMALIZED_OK"
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


def normalize_agy_result_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt canonical AGY result packets into the completed-work gate dialect.

    AGY may emit a user-facing result-packet dialect (branch/result_artifacts/
    verification/non_claims) while the completed-work gate expects source_path,
    source_branch, proof, and object-shaped lane_scope. This adapter fills only
    derivable, safe fields and leaves genuinely missing provenance absent so the
    gate can reject it explicitly.
    """

    normalized = _json_object(packet, "packet")
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
        elif key == "BASE_BRANCH":
            packet["base_branch"] = value
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
        normalized_packet = normalize_agy_result_packet(packet)
        gate = classify_completed_work(
            normalized_packet,
            dirty_source=dirty_source,
            source_is_stale=source_is_stale,
            conflicts=conflicts or (),
            trusted_agents=("agy", "fred", "jules"),
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
    )


def _json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    # Round-trip to ensure the persisted packet is JSON-safe and detached from callers.
    return json.loads(json.dumps(dict(value), sort_keys=True, default=str))
