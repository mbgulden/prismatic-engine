"""Reusable Handoff Contract validation helpers.

This module backs both the standalone CLI validator and the dispatcher
preflight gate. Keep semantic checks here so runtime behavior cannot drift from
review fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SCHEMA_PATH = ROOT / "schemas" / "handoff-contract.schema.json"
DEFAULT_SCHEMA_PATH = REFERENCE_SCHEMA_PATH
PACKAGE_SCHEMA_RESOURCE = "schemas/handoff-contract.schema.json"
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
        if isinstance(allowed, str)
    )


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def load_default_schema() -> dict[str, Any]:
    """Load the packaged runtime schema using installed-package-safe resources."""
    try:
        resource = resources.files("prismatic").joinpath(PACKAGE_SCHEMA_RESOURCE)
        return json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"packaged handoff schema unavailable: {exc}") from exc


def semantic_errors(packet: Any) -> list[str]:
    """Return focused review-contract errors not expressed in JSON Schema.

    JSON Schema owns type validation. These semantic checks must therefore be
    defensive: malformed nested values should add schema errors, not crash here.
    """
    errors: list[str] = []
    if not isinstance(packet, dict):
        return errors
    work = _object(packet.get("work"))
    result = _object(packet.get("result"))
    evidence = _object(packet.get("evidence"))

    required_artifacts = set(_string_list(evidence.get("required_artifacts")))
    actual_artifacts = set(_string_list(result.get("artifacts")))
    if result.get("status") == "pass" and not required_artifacts.issubset(
        actual_artifacts
    ):
        missing = sorted(required_artifacts - actual_artifacts)
        errors.append(f"missing required result artifacts: {missing}")

    allowed_paths = _string_list(work.get("allowed_paths"))
    for changed_path in _string_list(result.get("changed_paths")):
        if not path_allowed(changed_path, allowed_paths):
            errors.append(f"changed path outside allowed_paths: {changed_path}")

    claims = {claim.lower() for claim in _string_list(result.get("claims"))}
    production_facing = bool(work.get("production_facing")) or any(
        "production" in claim for claim in claims
    )
    production_proof = _object(evidence.get("production_proof"))
    proof_required = bool(production_proof.get("required")) or production_facing
    if proof_required and not _string_list(production_proof.get("artifacts")):
        errors.append("production-facing handoff is missing production_proof artifacts")

    return errors


def validate_packet(packet: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """Validate a handoff packet against JSON Schema plus semantic checks."""
    schema = schema if schema is not None else load_default_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for error in sorted(validator.iter_errors(packet), key=str)]
    errors.extend(semantic_errors(packet))
    return errors


def validation_result(
    packet: Any, schema: dict[str, Any] | None = None
) -> HandoffValidationResult:
    """Return a dispatch-friendly validation result for *packet*."""
    target_agent = None
    target = packet.get("target") if isinstance(packet, dict) else None
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


def _decode_handoff_value(value: Any, key: str) -> dict[str, Any] | None:
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


def extract_handoff_packet(container: Any) -> dict[str, Any] | None:
    """Extract an embedded handoff packet from issue/task metadata-like objects.

    Supported keys are ``handoff_contract``, ``handoff_packet``, and ``handoff``.
    Values may be direct, task metadata, Linear webhook ``data`` payloads, or
    metadata nested under either location. Missing handoff metadata means the
    dispatch path is not a handoff and should continue unchanged.
    """
    sources: list[Any] = [container]
    if hasattr(container, "metadata"):
        sources.append(container.metadata)
    if isinstance(container, dict):
        data = container.get("data")
        metadata = container.get("metadata")
        sources.extend([metadata, data])
        if isinstance(data, dict):
            sources.append(data.get("metadata"))

    seen: set[int] = set()
    for source in sources:
        if not isinstance(source, dict) or id(source) in seen:
            continue
        seen.add(id(source))
        for key in HANDOFF_PACKET_KEYS:
            if key not in source:
                continue
            decoded = _decode_handoff_value(source.get(key), key)
            if decoded is not None:
                return decoded
    return None
