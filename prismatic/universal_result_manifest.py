"""Strict, versioned Universal Result Manifest (v2) validation and compatibility.

This module implements the schema and validation logic for versioned universal
result manifests (Universal Result Manifest v2) covering code, web/app,
image/design, sprite/game asset, video, audio, document/data, and mixed bundles.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

UNIVERSAL_RESULT_MANIFEST_V2_MARKER = "UNIVERSAL_RESULT_MANIFEST_V2_OK"

SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY-----"
    r"|bearer\s+[A-Za-z0-9._-]{20,}"
    r"|sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{20,}"
    r"|[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@[^/\s]+"
    r"|[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s@]+@[^/\s]+"
    r"|[^/\s:@]+:[^/\s@]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    r"|\b(?:api[_-]?key|client[_-]?secret|private[_-]?key|secret[_-]?key|access[_-]?token|passwd|password|credential|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{12,}[\"']?)",
    re.IGNORECASE,
)


RFC3339_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    re.IGNORECASE,
)

MANIFEST_FORMAT_CHECKER = FormatChecker()


@MANIFEST_FORMAT_CHECKER.checks("date-time")
def _validate_rfc3339_date_time(val: Any) -> bool:
    if not isinstance(val, str):
        return True
    if not RFC3339_REGEX.match(val):
        return False
    val_iso = val.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(val_iso)
        return dt.tzinfo is not None
    except ValueError:
        return False


@dataclass(frozen=True)
class ManifestValidationResult:
    ok: bool
    errors: tuple[str, ...]

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise UniversalManifestValidationError(self.errors)


class UniversalManifestValidationError(ValueError):
    """Raised when a universal result manifest violates schema/policy constraints."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("Universal Result Manifest invalid: " + "; ".join(self.errors))


def sanitize_error_message(msg: str) -> str:
    """Sanitize any credentials, secrets, or control characters out of error messages."""
    # Redact secrets
    sanitized = SECRET_VALUE_RE.sub("[REDACTED_SECRET]", msg)
    # Remove control characters
    sanitized = re.sub(r"[\x00-\x1f\x7f]", "", sanitized)
    return sanitized


def load_universal_manifest_schema() -> dict[str, Any]:
    """Load the JSON Schema definition for the Universal Result Manifest v2."""
    try:
        from importlib import resources

        # Try importlib resources files API (Python 3.9+)
        schema_text = (
            resources.files("prismatic")
            .joinpath("schemas/universal-result-manifest.schema.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(schema_text)
    except Exception:
        # Fallback to direct path relative to file location
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "schemas" / "universal-result-manifest.schema.json"
        if not schema_path.is_file():
            schema_path = (
                Path(__file__).resolve().parent
                / "schemas"
                / "universal-result-manifest.schema.json"
            )
        with schema_path.open("r", encoding="utf-8") as f:
            return json.load(f)


def is_safe_path(path: str) -> tuple[bool, str | None]:
    """Check if a path string is safe from absolute path prefix, directory traversal, control characters, backslash, URL/userinfo forms."""
    if not isinstance(path, str):
        return False, "is not a string"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return False, "contains control characters"
    if path.startswith("/") or path.startswith("\\") or re.match(r"^[a-zA-Z]:", path):
        return False, "is an absolute path"
    parts = re.split(r"[/\\]", path)
    if ".." in parts:
        return False, "contains '..' directory traversal segment"
    if "\\" in path:
        return False, "contains backslash separator ambiguity"
    if "://" in path or re.search(r"[a-zA-Z0-9_.-]+(?::[^@]*)?@", path):
        return False, "contains URL or userinfo structure"
    return True, None


def _is_credential_key_name(name: str) -> bool:
    normalized = name.lower().replace("_", "").replace("-", "")
    credential_keywords = {
        "apikey",
        "password",
        "passwd",
        "token",
        "secret",
        "clientsecret",
        "privatekey",
        "secretkey",
        "accesskey",
        "accesstoken",
        "authtoken",
        "credential",
        "credentials",
    }
    return any(kw in normalized for kw in credential_keywords)


def find_secrets(val: Any, current_path: str = "root") -> list[str]:
    """Recursively search for credentials/secrets without retaining or printing the secret values."""
    found_errors = []
    if isinstance(val, str):
        if SECRET_VALUE_RE.search(val):
            found_errors.append(
                f"[{current_path}] contains secret-like content [REDACTED_SECRET]"
            )
    elif isinstance(val, dict):
        for k, v in val.items():
            # Check key itself for secrets
            if SECRET_VALUE_RE.search(str(k)):
                found_errors.append(
                    f"[{current_path} -> key:{sanitize_error_message(str(k))}] contains secret-like content [REDACTED_SECRET]"
                )
            # Check if key is a credential key name, and check if value is a credential-shaped string
            if (
                _is_credential_key_name(str(k))
                and isinstance(v, str)
                and len(v.strip()) >= 8
            ):
                found_errors.append(
                    f"[{current_path} -> {k}] contains secret-like content [REDACTED_SECRET]"
                )
            # Check value recursively
            found_errors.extend(find_secrets(v, f"{current_path} -> {k}"))
    elif isinstance(val, (list, tuple, set)):
        for idx, item in enumerate(val):
            found_errors.extend(find_secrets(item, f"{current_path} -> {idx}"))
    return found_errors


def validate_universal_manifest(
    manifest: Mapping[str, Any],
) -> ManifestValidationResult:
    """Perform strict nested schema validation on a Universal Result Manifest (v2)."""
    errors: list[str] = []

    # 1. Base JSON Schema structural validation
    try:
        schema = load_universal_manifest_schema()
        validator = Draft202012Validator(schema, format_checker=MANIFEST_FORMAT_CHECKER)
        schema_errors = sorted(
            validator.iter_errors(manifest), key=lambda e: list(e.path)
        )
        for err in schema_errors:
            # Capture the JSON pointer path to the error
            path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
            errors.append(sanitize_error_message(f"[{path_str}] {err.message}"))
    except Exception as exc:
        errors.append(
            sanitize_error_message(f"Failed to load or execute validator: {exc}")
        )
        return ManifestValidationResult(False, tuple(errors))

    # 2. Strict semantic & cross-field validation rules
    if not errors:
        # Check marker
        if manifest.get("marker") != UNIVERSAL_RESULT_MANIFEST_V2_MARKER:
            errors.append(f"marker must be '{UNIVERSAL_RESULT_MANIFEST_V2_MARKER}'")

        deliverables = manifest.get("deliverables", [])
        content_hashes = manifest.get("content_hashes", {})

        # Validate path safety
        # A. Deliverables paths
        deliverable_paths = []
        for idx, item in enumerate(deliverables):
            p = (
                item
                if isinstance(item, str)
                else (item.get("path") if isinstance(item, dict) else None)
            )
            if p is not None:
                ok, reason = is_safe_path(p)
                if not ok:
                    errors.append(f"Unsafe path in deliverables[{idx}]: '{p}' {reason}")
                else:
                    deliverable_paths.append(p)
            else:
                errors.append(f"Invalid deliverable item at index {idx}")

        # B. Content hashes keys
        for p in content_hashes.keys():
            ok, reason = is_safe_path(p)
            if not ok:
                errors.append(f"Unsafe path key in content_hashes: '{p}' {reason}")

        # C. Path scope touched paths
        touched_paths = manifest.get("path_scope", {}).get("touched_paths", [])
        for idx, p in enumerate(touched_paths):
            if isinstance(p, str):
                ok, reason = is_safe_path(p)
                if not ok:
                    errors.append(
                        f"Unsafe path in path_scope.touched_paths[{idx}]: '{p}' {reason}"
                    )

        # D. Path scope allowed paths
        allowed_paths = manifest.get("path_scope", {}).get("allowed_paths", [])
        for idx, p in enumerate(allowed_paths):
            if isinstance(p, str):
                ok, reason = is_safe_path(p)
                if not ok:
                    errors.append(
                        f"Unsafe path in path_scope.allowed_paths[{idx}]: '{p}' {reason}"
                    )

        # E. Rollback script path
        rollback = manifest.get("rollback")
        rollback_script = (
            rollback.get("script_path") if isinstance(rollback, dict) else None
        )
        if isinstance(rollback_script, str):
            ok, reason = is_safe_path(rollback_script)
            if not ok:
                errors.append(
                    f"Unsafe path in rollback.script_path: '{rollback_script}' {reason}"
                )

        # F. Path/hash key disagreement
        # The set of deliverable paths must match the set of content_hashes keys exactly
        if not errors:
            deliv_set = set(deliverable_paths)
            hash_set = set(content_hashes.keys())
            if deliv_set != hash_set:
                disagreement_deliv = sorted(list(deliv_set - hash_set))
                disagreement_hash = sorted(list(hash_set - deliv_set))
                msg = "Path/hash key disagreement:"
                if disagreement_deliv:
                    msg += f" deliverables missing hashes: {disagreement_deliv};"
                if disagreement_hash:
                    msg += (
                        f" content_hashes key not in deliverables: {disagreement_hash};"
                    )
                errors.append(msg)

        # Verify type-specific proofs match the bundle type (exactly the matching proof contract)
        bundle_type = manifest.get("bundle_type")
        proofs = manifest.get("type_specific_proofs", {})

        bundle_to_proof_key = {
            "code": "code",
            "web/app": "web_app",
            "image/design": "image_design",
            "sprite/game asset": "sprite_game_asset",
            "video": "video",
            "audio": "audio",
            "document/data": "document_data",
            "mixed": "mixed",
        }

        expected_key = bundle_to_proof_key.get(bundle_type)
        if not expected_key:
            errors.append(f"Unknown bundle_type: {bundle_type}")
        else:
            # Must have exactly the expected_key and nothing else
            proof_keys = set(proofs.keys())
            if expected_key not in proof_keys:
                errors.append(
                    f"bundle_type is '{bundle_type}' but type-specific proof is missing (matching proof '{expected_key}' is missing)"
                )
            elif len(proof_keys) > 1:
                errors.append(
                    f"bundle_type is '{bundle_type}' but proofs object contains extra keys: {sorted(list(proof_keys - {expected_key}))}"
                )

        # Secret scanning of fields
        secret_errors = find_secrets(dict(manifest))
        errors.extend(secret_errors)

    # Sanitize all captured error messages
    sanitized_errors = [sanitize_error_message(err) for err in errors]
    return ManifestValidationResult(len(sanitized_errors) == 0, tuple(sanitized_errors))


def adapt_legacy_packet_to_manifest_v2(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility adapter from legacy completed-work result packets to v2 manifest."""
    merge_lane = packet.get("merge_lane") or ""
    if "dashboard" in merge_lane or "ui" in merge_lane:
        bundle_type = "web/app"
    elif "api" in merge_lane or "backend" in merge_lane:
        bundle_type = "code"
    elif "docs" in merge_lane:
        bundle_type = "document/data"
    else:
        bundle_type = "mixed"

    raw_artifacts = (
        packet.get("result_artifacts") or packet.get("changed_files") or ["RESULT.md"]
    )
    deliverables = []
    content_hashes = {}

    unverified_status = {"status": "unverified", "reason": "adapted_from_legacy_packet"}

    for artifact in raw_artifacts:
        path = (
            artifact
            if isinstance(artifact, str)
            else (artifact.get("path") if isinstance(artifact, dict) else "RESULT.md")
        )
        if not path:
            path = "RESULT.md"
        deliverables.append(path)
        content_hashes[path] = unverified_status

    provenance = {
        "toolchain": unverified_status,
        "builder": packet.get("agent") or unverified_status,
    }

    def get_commit_sha(val: Any) -> Any:
        if isinstance(val, str) and re.match(r"^[0-9a-fA-F]{40}$", val):
            return val
        return unverified_status

    source_revision = {
        "source_commit_sha": get_commit_sha(
            packet.get("source_commit_sha") or packet.get("commit")
        ),
        "base_commit_sha": get_commit_sha(packet.get("base_commit_sha")),
        "branch": packet.get("branch") or packet.get("source_branch") or "main",
    }

    allowed_paths = packet.get("lane_scope", {}).get("allowed_paths")
    if not allowed_paths:
        allowed_paths = ["*"]

    path_scope = {
        "allowed_paths": allowed_paths,
        "touched_paths": packet.get("changed_files")
        or packet.get("lane_scope", {}).get("touched_paths")
        or deliverables,
    }

    privacy_retention = {
        "privacy_level": "internal",
        "retention_status": "retained",
        "retention_reasons": ["adapted_from_legacy_packet"],
        "retention_policy": "default",
    }

    proofs: dict[str, Any] = {}
    legacy_verification = packet.get("verification") or packet.get("proof") or {}
    legacy_res = legacy_verification.get("result") or legacy_verification.get("status")

    tests_passed = True if legacy_res == "PASS" else unverified_status

    if bundle_type == "code":
        proofs["code"] = {
            "tests_passed": tests_passed,
            "coverage": unverified_status,
            "lint_status": unverified_status,
        }
    elif bundle_type == "web/app":
        proofs["web_app"] = {
            "build_status": unverified_status,
            "lighthouse": {
                "performance": unverified_status,
                "accessibility": unverified_status,
                "best_practices": unverified_status,
                "seo": unverified_status,
            },
            "visual_qa": unverified_status,
        }
    elif bundle_type == "document/data":
        proofs["document_data"] = {
            "format": "markdown"
            if any(p.endswith((".md", ".txt", ".json")) for p in deliverables)
            else unverified_status,
            "valid": unverified_status,
            "schema_compliant": unverified_status,
        }
    else:
        proofs["mixed"] = {"submanifests_validated": unverified_status}

    licenses = [unverified_status]

    candidate_sha_destination = {
        "candidate_sha": source_revision["source_commit_sha"],
        "destination_branch": packet.get("base_branch") or "main",
    }

    integration_receipt = unverified_status
    review_digest = unverified_status
    rollback = unverified_status
    post_integration_proof = unverified_status

    v2_manifest = {
        "schema_version": 2.0,
        "bundle_type": bundle_type,
        "deliverables": deliverables,
        "content_hashes": content_hashes,
        "provenance": provenance,
        "source_revision": source_revision,
        "path_scope": path_scope,
        "privacy_retention": privacy_retention,
        "type_specific_proofs": proofs,
        "preview_handles": [],
        "licenses": licenses,
        "non_claims": list(packet.get("non_claims", [])),
        "candidate_sha_destination": candidate_sha_destination,
        "review_digest": review_digest,
        "integration_receipt": integration_receipt,
        "rollback": rollback,
        "post_integration_proof": post_integration_proof,
        "marker": UNIVERSAL_RESULT_MANIFEST_V2_MARKER,
    }

    return v2_manifest


def is_promotion_ready(manifest: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Check if the manifest satisfies all promotion-ready fields without unverified/unavailable/held evidence."""
    reasons = []

    res = validate_universal_manifest(manifest)
    if not res.ok:
        return False, list(res.errors)

    def check_unverified(val: Any, path: str = "root"):
        if isinstance(val, str):
            if val in ("unverified", "unavailable", "held"):
                reasons.append(f"Field '{path}' is {val}")
        elif isinstance(val, dict):
            if "status" in val and val.get("status") in (
                "unverified",
                "unavailable",
                "held",
            ):
                reasons.append(f"Field '{path}' status is {val.get('status')}")
            else:
                for k, v in val.items():
                    check_unverified(v, f"{path}.{k}")
        elif isinstance(val, (list, tuple)):
            for i, item in enumerate(val):
                check_unverified(item, f"{path}[{i}]")

    check_unverified(manifest)

    bundle_type = manifest.get("bundle_type")
    proofs = manifest.get("type_specific_proofs", {})
    if bundle_type == "code":
        code_proof = proofs.get("code", {})
        if code_proof.get("tests_passed") is not True:
            reasons.append("Code tests did not pass")
    elif bundle_type == "web/app":
        web_proof = proofs.get("web_app", {})
        if web_proof.get("build_status") != "success":
            reasons.append("Web build did not succeed")

    return len(reasons) == 0, reasons
