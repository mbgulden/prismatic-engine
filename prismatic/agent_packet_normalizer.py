"""Agent result packet normalization and repair-hint classification.

This layer deliberately preserves failure as data. It never promotes repaired
content to success and never launches a rerun.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from prismatic.agy_completed_work import normalize_agy_result_packet
from prismatic.completed_work_gate import GateClassification, classify_completed_work

RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER = "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK"


class NormalizationStatus(str, Enum):
    ACCEPTED = "accepted"
    NORMALIZED_WITH_WARNINGS = "normalized_with_warnings"
    REJECTED_REPAIRABLE = "rejected_repairable"
    REJECTED_RERUN_REQUIRED = "rejected_rerun_required"
    REJECTED_POLICY_VIOLATION = "rejected_policy_violation"


REPAIR_HINTS = {
    "missing_source_path",
    "missing_proof_log",
    "missing_non_claims",
    "invalid_changed_files",
    "production_claim_without_proof",
    "agent_prose_only",
    "secret_like_content_detected",
    "wrong_agent_or_ambiguous_agent",
}

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|LINEAR_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\b\s*[:=]"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class NormalizationResult:
    status: NormalizationStatus
    normalized_packet: dict[str, Any] | None
    canonical_packet_id: str | None
    rejection_reason: str | None
    repair_hint: str | None
    rerun_allowed: bool
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalization_status": self.status.value,
            "normalized_packet": self.normalized_packet,
            "canonical_packet_id": self.canonical_packet_id,
            "rejection_reason": self.rejection_reason,
            "repair_hint": self.repair_hint,
            "rerun_allowed": self.rerun_allowed,
            "warnings": list(self.warnings),
        }


def normalize_agent_output(
    raw_text: str, *, expected_agent: str | None = None
) -> NormalizationResult:
    """Normalize raw agent output into a packet classification without side effects."""

    if _contains_secret_like_content(raw_text):
        return NormalizationResult(
            status=NormalizationStatus.REJECTED_POLICY_VIOLATION,
            normalized_packet=None,
            canonical_packet_id=None,
            rejection_reason="secret-like content detected in raw output",
            repair_hint="secret_like_content_detected",
            rerun_allowed=False,
        )

    packet = _extract_json_object(raw_text)
    if packet is None:
        return NormalizationResult(
            status=NormalizationStatus.REJECTED_REPAIRABLE,
            normalized_packet=None,
            canonical_packet_id=None,
            rejection_reason="raw output does not contain a JSON object result packet",
            repair_hint="agent_prose_only",
            rerun_allowed=False,
        )

    original_hint = repair_hint_for_packet(packet, expected_agent=expected_agent)

    try:
        normalized = normalize_agy_result_packet(packet)
        gate = classify_completed_work(normalized)
    except Exception as exc:  # defensive: malformed objects stay queued, not lost
        return NormalizationResult(
            status=NormalizationStatus.REJECTED_REPAIRABLE,
            normalized_packet=packet,
            canonical_packet_id=None,
            rejection_reason=str(exc),
            repair_hint="agent_prose_only",
            rerun_allowed=False,
        )

    hint = original_hint or repair_hint_for_packet(
        normalized, expected_agent=expected_agent, gate_reasons=gate.reasons
    )
    canonical_id = (
        _canonical_packet_id(normalized)
        if gate.classification == GateClassification.MERGE_READY and hint is None
        else None
    )

    if hint == "secret_like_content_detected" or hint == "wrong_agent_or_ambiguous_agent":
        status = NormalizationStatus.REJECTED_POLICY_VIOLATION
        rerun_allowed = False
    elif hint in {
        "missing_proof_log",
        "missing_non_claims",
        "production_claim_without_proof",
    }:
        status = NormalizationStatus.REJECTED_REPAIRABLE
        rerun_allowed = False
    elif hint in {"missing_source_path", "invalid_changed_files", "agent_prose_only"}:
        status = NormalizationStatus.REJECTED_RERUN_REQUIRED
        rerun_allowed = True
    elif gate.classification == GateClassification.MERGE_READY:
        warnings = _normalization_warnings(normalized)
        status = (
            NormalizationStatus.NORMALIZED_WITH_WARNINGS
            if warnings
            else NormalizationStatus.ACCEPTED
        )
        return NormalizationResult(
            status, normalized, canonical_id, None, None, False, tuple(warnings)
        )
    else:
        status = NormalizationStatus.REJECTED_REPAIRABLE
        rerun_allowed = False

    return NormalizationResult(
        status=status,
        normalized_packet=normalized,
        canonical_packet_id=canonical_id,
        rejection_reason="; ".join(gate.reasons) or gate.classification.value,
        repair_hint=hint,
        rerun_allowed=rerun_allowed,
    )


def repair_hint_for_packet(
    packet: Mapping[str, Any],
    *,
    expected_agent: str | None = None,
    gate_reasons: tuple[str, ...] = (),
) -> str | None:
    text = " | ".join(gate_reasons).lower()
    agent = packet.get("agent")
    if expected_agent and agent != expected_agent:
        return "wrong_agent_or_ambiguous_agent"
    if (
        not isinstance(agent, str)
        or not agent.strip()
        or agent not in {"agy", "kai", "becca", "fred"}
    ):
        return "wrong_agent_or_ambiguous_agent"
    if _contains_secret_like_content(json.dumps(packet, sort_keys=True, default=str)):
        return "secret_like_content_detected"
    raw_proof = packet.get("proof")
    proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
    non_claims = proof.get("non_claims") or proof.get("not_claiming")
    changed_files = packet.get("changed_files")
    deployed = str(
        packet.get("runtime_deployed") or packet.get("production_deployed") or ""
    ).lower() in {"true", "yes", "1"}
    proof_scope = json.dumps(proof, sort_keys=True, default=str).lower()
    if "source_path" in text or not packet.get("source_path"):
        return "missing_source_path"
    if (
        "changed_files" in text
        or not isinstance(changed_files, list)
        or not changed_files
    ):
        return "invalid_changed_files"
    if "log" in text or not proof.get("log"):
        return "missing_proof_log"
    if deployed and not any(
        token in proof_scope for token in ("deploy", "runtime", "production")
    ):
        return "production_claim_without_proof"
    if "non_claim" in text or not non_claims:
        if deployed:
            return "production_claim_without_proof"
        return "missing_non_claims"
    return None


def repair_preview(
    raw_text: str, *, expected_agent: str | None = None
) -> dict[str, Any]:
    result = normalize_agent_output(raw_text, expected_agent=expected_agent)
    return {
        "status": "preview",
        "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
        "normalization_status": result.status.value,
        "repair_hint": result.repair_hint,
        "rejection_reason": result.rejection_reason,
        "rerun_allowed": result.rerun_allowed,
        "would_auto_repair": False,
        "would_auto_rerun": False,
        "warnings": list(result.warnings),
    }


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    candidates = [raw_text.strip()]
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    loose = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if loose:
        candidates.append(loose.group(1).strip())
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _contains_secret_like_content(value: str) -> bool:
    return any(pattern.search(value or "") for pattern in _SECRET_PATTERNS)


def _canonical_packet_id(packet: Mapping[str, Any]) -> str:
    import hashlib

    basis = json.dumps(packet, sort_keys=True, separators=(",", ":"), default=str)
    return "packet_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _normalization_warnings(packet: Mapping[str, Any]) -> list[str]:
    normalization = packet.get("normalization")
    if isinstance(normalization, Mapping):
        return [
            key for key, value in normalization.items() if key != "marker" and value
        ]
    return []
