"""Reusable Handoff Contract validation helpers.

This module backs both the standalone CLI validator and the dispatcher
preflight gate. Keep semantic checks here so runtime behavior cannot drift from
review fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = ROOT / "schemas" / "handoff-contract.schema.json"
HANDOFF_PACKET_KEYS = ("handoff_contract", "handoff_packet", "handoff")


@dataclass(frozen=True)
class HandoffValidationResult:
    """Result returned by handoff packet validation/preflight."""

    ok: bool
    status: str
    reason: str
    errors: tuple[str, ...] = ()
    target_agent: str | None = None

    @property
    def is_manual_review(self) -> bool:
        return self.status == "needs_manual_review"


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from *path*."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def path_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(
        path == allowed or path.startswith(allowed.rstrip("/") + "/")
        for allowed in allowed_paths
    )


def semantic_errors(packet: dict[str, Any]) -> list[str]:
    """Return focused review-contract errors not expressed in JSON Schema."""
    errors: list[str] = []
    work = packet.get("work", {})
    result = packet.get("result", {})
    evidence = packet.get("evidence", {})

    required_artifacts = set(evidence.get("required_artifacts", []))
    actual_artifacts = set(result.get("artifacts", []))
    if result.get("status") == "pass" and not required_artifacts.issubset(
        actual_artifacts
    ):
        missing = sorted(required_artifacts - actual_artifacts)
        errors.append(f"missing required result artifacts: {missing}")

    allowed_paths = work.get("allowed_paths", [])
    for changed_path in result.get("changed_paths", []):
        if not path_allowed(changed_path, allowed_paths):
            errors.append(f"changed path outside allowed_paths: {changed_path}")

    claims = {claim.lower() for claim in result.get("claims", [])}
    production_facing = bool(work.get("production_facing")) or any(
        "production" in claim for claim in claims
    )
    production_proof = evidence.get("production_proof") or {}
    proof_required = bool(production_proof.get("required")) or production_facing
    if proof_required and not production_proof.get("artifacts", []):
        errors.append("production-facing handoff is missing production_proof artifacts")

    return errors


def validate_packet(
    packet: dict[str, Any], schema: dict[str, Any] | None = None
) -> list[str]:
    """Validate a handoff packet against JSON Schema plus semantic checks."""
    schema = schema if schema is not None else load_json(DEFAULT_SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for error in sorted(validator.iter_errors(packet), key=str)]
    errors.extend(semantic_errors(packet))
    return errors


def validation_result(
    packet: dict[str, Any], schema: dict[str, Any] | None = None
) -> HandoffValidationResult:
    """Return a dispatch-friendly validation result for *packet*."""
    target_agent = None
    target = packet.get("target")
    if isinstance(target, dict):
        raw_target = target.get("agent")
        target_agent = (
            str(raw_target).strip().lower() if raw_target is not None else None
        )

    errors = tuple(validate_packet(packet, schema))
    if not target_agent:
        if "ambiguous or empty target agent" not in errors:
            errors = (*errors, "ambiguous or empty target agent")
        return HandoffValidationResult(
            ok=False,
            status="needs_manual_review",
            reason="ambiguous_target_agent",
            errors=errors,
            target_agent=target_agent,
        )
    if errors:
        return HandoffValidationResult(
            ok=False,
            status="blocked",
            reason="handoff_contract_invalid",
            errors=errors,
            target_agent=target_agent,
        )
    return HandoffValidationResult(
        ok=True,
        status="allowed",
        reason="handoff_contract_valid",
        target_agent=target_agent,
    )


def extract_handoff_packet(container: Any) -> dict[str, Any] | None:
    """Extract an embedded handoff packet from issue/task metadata-like objects.

    Supported keys are ``handoff_contract``, ``handoff_packet``, and ``handoff``.
    Values may already be dicts or JSON strings. Missing handoff metadata means
    the dispatch path is not a handoff and should continue unchanged.
    """
    source: Any = container
    if hasattr(container, "metadata"):
        source = getattr(container, "metadata")
    if not isinstance(source, dict):
        return None

    for key in HANDOFF_PACKET_KEYS:
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {
                    "target": {"agent": ""},
                    "_decode_error": f"invalid JSON handoff payload in {key}",
                }
            return (
                decoded
                if isinstance(decoded, dict)
                else {
                    "target": {"agent": ""},
                    "_decode_error": f"handoff payload in {key} is not an object",
                }
            )
    return None
