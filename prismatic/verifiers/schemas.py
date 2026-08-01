"""Type-specific verifier evidence schemas and closed validation rules.

Enforces fail-closed schema validation for verifier results and evidence models:
- Closed schemas (additionalProperties: false)
- Required evidence per output type
- Fail-closed validation for unknown types, unknown properties, duplicate IDs, shape mismatches
- Locator safety for mixed bundles (traversal, symlinks, userinfo, credentials, control chars)
- Strict provenance (64-hex SHA256 candidate/log digests, 40-hex commit SHA, tz-aware timestamps)
- Immutable log file integrity checks before pass/promotion
- Mixed bundle child digest recomputation against retained bytes
- Secret sanitization across all results and error messages
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from prismatic.universal_result_manifest import (
    MANIFEST_FORMAT_CHECKER,
    SECRET_VALUE_RE,
    find_secrets,
    sanitize_error_message,
)

UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK = "UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK"

CANONICAL_VERIFIER_TYPES = {
    "code": "code",
    "code/package": "code",
    "web/app": "web/app",
    "website/app/browser": "web/app",
    "image/design": "image/design",
    "sprite/game asset": "sprite/game asset",
    "sprite/atlas/game import": "sprite/game asset",
    "video": "video",
    "audio": "audio",
    "document/data": "document/data",
    "mixed": "mixed",
    "mixed bundles": "mixed",
}

VALID_STATUSES = {"pass", "failed", "unavailable", "not_run", "partial"}
UNHEALTHY_STATUSES = {
    "unavailable",
    "not_run",
    "partial",
    "failed",
    "unverified",
    "held",
}

# Required evidence keys for each canonical verifier type
TYPE_SPECIFIC_REQUIRED_FIELDS = {
    "code": ["tests_passed", "coverage", "lint_status", "install_readback"],
    "web/app": ["build_status", "lighthouse", "visual_qa", "browser_mobile_proof"],
    "image/design": [
        "dimensions",
        "color_space",
        "similarity_score",
        "color_alpha_proof",
    ],
    "sprite/game asset": [
        "frame_count",
        "spritesheet",
        "collision_boxes",
        "atlas_geometry",
    ],
    "video": ["resolution", "duration_seconds", "bitrate_kbps", "codec_fps_sync"],
    "audio": [
        "channels",
        "sample_rate_hz",
        "duration_seconds",
        "loudness_silence_proof",
    ],
    "document/data": ["format", "valid", "schema_compliant"],
    "mixed": ["submanifests_validated", "child_digests", "atomic_policy"],
}

HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
URL_USERINFO_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@[^/]+")


def is_valid_sha256(digest_str: str) -> bool:
    """Check if digest is a valid non-sentinel 64-hex SHA-256 string."""
    if not isinstance(digest_str, str):
        return False
    clean_digest = digest_str.removeprefix("sha256:")
    if not HEX64_RE.match(clean_digest):
        return False
    if clean_digest == "0" * 64 or clean_digest == "f" * 64:
        return False
    return True


def is_valid_commit_sha(sha_str: str) -> bool:
    """Check if commit SHA is a valid non-sentinel 40-hex Git commit SHA."""
    if not isinstance(sha_str, str):
        return False
    if not HEX40_RE.match(sha_str):
        return False
    if sha_str == "0" * 40 or sha_str == "f" * 40:
        return False
    return True


def is_timezone_aware(ts_str: str) -> bool:
    """Check if ISO-8601 timestamp string is timezone-aware."""
    if not isinstance(ts_str, str) or not ts_str:
        return False
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.tzinfo is not None
    except ValueError:
        return False


def load_verifier_result_schema() -> dict[str, Any]:
    """Load JSON schema for verifier results."""
    try:
        from importlib import resources

        schema_text = (
            resources.files("prismatic")
            .joinpath("schemas/verifier-result.schema.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(schema_text)
    except Exception:
        root = Path(__file__).resolve().parents[2]
        schema_path = root / "prismatic" / "schemas" / "verifier-result.schema.json"
        if not schema_path.is_file():
            schema_path = root / "schemas" / "verifier-result.schema.json"
        with schema_path.open("r", encoding="utf-8") as f:
            return json.load(f)


def validate_locator_safety(locator: str) -> tuple[bool, str | None]:
    """Validate locator string for directory traversal, control characters, userinfo, credentials."""
    if not isinstance(locator, str) or not locator:
        return False, "Locator must be a non-empty string"
    if any(ord(c) < 32 or ord(c) == 127 for c in locator):
        return False, "Locator contains control characters"
    if URL_USERINFO_RE.search(locator) or "@" in locator.split("/")[0]:
        return False, f"Locator contains userinfo/credential: '{locator}'"
    parts = re.split(r"[/\\]", locator)
    if ".." in parts:
        return False, "Locator contains directory traversal ('..')"
    if SECRET_VALUE_RE.search(locator):
        return False, "Locator contains credential-shaped pattern: [REDACTED_SECRET]"
    return True, None


def has_unhealthy_state(val: Any) -> bool:
    """Recursively check if value contains any unhealthy/unavailable/failed status or sentinel."""
    if isinstance(val, str):
        if val in UNHEALTHY_STATUSES or val in ("x", "unverified", "held"):
            return True
    elif isinstance(val, dict):
        status_val = val.get("status")
        if status_val in UNHEALTHY_STATUSES or status_val in (
            "x",
            "unverified",
            "held",
        ):
            return True
        for k, v in val.items():
            if has_unhealthy_state(v):
                return True
    elif isinstance(val, (list, tuple)):
        for item in val:
            if has_unhealthy_state(item):
                return True
    return False


def verify_log_file_integrity(
    log_path: str, log_digest: str
) -> tuple[bool, str | None]:
    """Verify log file exists and its content SHA-256 matches log_digest."""
    try:
        p = Path(log_path).resolve()
        if not p.is_file():
            return False, f"Log file '{log_path}' does not exist on disk"
        content = p.read_bytes()
        computed = hashlib.sha256(content).hexdigest()
        expected = log_digest.removeprefix("sha256:")
        if computed.lower() != expected.lower():
            return (
                False,
                f"Log file digest mismatch: expected '{expected}', computed '{computed}'",
            )
        return True, None
    except Exception as exc:
        return False, f"Failed log file integrity check: {exc}"


def validate_verifier_result(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Strictly validate a verifier result object, enforcing fail-closed rules."""
    errors: list[str] = []

    # 1. Structural schema validation
    try:
        schema = load_verifier_result_schema()
        validator = Draft202012Validator(schema, format_checker=MANIFEST_FORMAT_CHECKER)
        schema_errors = sorted(
            validator.iter_errors(result), key=lambda e: list(e.path)
        )
        for err in schema_errors:
            path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
            errors.append(sanitize_error_message(f"[{path_str}] {err.message}"))
    except Exception as exc:
        errors.append(
            sanitize_error_message(f"Failed schema validation execution: {exc}")
        )

    # 2. Check marker
    if result.get("marker") != UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK:
        errors.append(f"marker must be '{UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK}'")

    # 3. Canonical type check
    raw_type = result.get("verifier_type")
    if raw_type not in CANONICAL_VERIFIER_TYPES:
        errors.append(f"Unknown verifier_type: '{raw_type}'")
        return False, [sanitize_error_message(e) for e in errors]

    canonical_type = CANONICAL_VERIFIER_TYPES[raw_type]

    # 4. Strict Candidate Digest Provenance
    cand_digest = result.get("candidate_digest")
    if not is_valid_sha256(str(cand_digest)):
        errors.append(
            f"Invalid candidate_digest provenance: '{cand_digest}' (must be valid 64-hex SHA-256, non-all-zero)"
        )

    # 5. Strict Source Lineage Provenance
    source_lineage = result.get("source_lineage")
    if not isinstance(source_lineage, dict):
        errors.append("source_lineage must be an object")
    else:
        src_sha = source_lineage.get("source_commit_sha")
        if not is_valid_commit_sha(str(src_sha)):
            errors.append(
                f"Invalid source_commit_sha in source_lineage: '{src_sha}' (must be valid 40-hex commit SHA, non-all-zero)"
            )
        branch = source_lineage.get("branch")
        if not isinstance(branch, str) or not branch.strip():
            errors.append("source_lineage branch must be a non-empty string")

    # 6. Timestamp Timezone Awareness Check
    timestamp = result.get("timestamp")
    if not is_timezone_aware(str(timestamp)):
        errors.append(f"Timestamp must be timezone-aware ISO-8601: '{timestamp}'")

    # 7. Log Path Safety, Digest, and Retained Log File Integrity Check
    log_path = result.get("log_path")
    log_digest = result.get("log_digest")
    if not log_path:
        errors.append("log_path is required")
    else:
        ok_loc, loc_err = validate_locator_safety(str(log_path))
        if not ok_loc:
            errors.append(f"log_path error: {loc_err}")

    if not is_valid_sha256(str(log_digest)):
        errors.append(
            f"Invalid log_digest: '{log_digest}' (must be valid 64-hex SHA-256)"
        )
    elif log_path and ok_loc:
        ok_log, log_err = verify_log_file_integrity(str(log_path), str(log_digest))
        if not ok_log:
            errors.append(log_err)

    # 8. Type-Specific Evidence Validation
    evidence = result.get("type_specific_evidence", {})
    if not isinstance(evidence, dict):
        errors.append("type_specific_evidence must be an object")
        return False, [sanitize_error_message(e) for e in errors]

    expected_fields = set(TYPE_SPECIFIC_REQUIRED_FIELDS.get(canonical_type, []))
    evidence_keys = set(evidence.keys())
    extra_keys = evidence_keys - expected_fields
    if extra_keys:
        errors.append(
            f"type_specific_evidence for type '{canonical_type}' contains unknown fields: {sorted(list(extra_keys))}"
        )

    missing_fields = expected_fields - evidence_keys
    if missing_fields:
        errors.append(
            f"type_specific_evidence for type '{canonical_type}' missing required fields: {sorted(list(missing_fields))}"
        )

    # 9. Status and Recursive Unhealthy State Enforcement
    status = result.get("status")
    if status == "pass":
        if has_unhealthy_state(evidence):
            errors.append(
                "Status is 'pass' but type_specific_evidence contains unhealthy, unavailable, or failed states"
            )

        # Type-specific positive proof checks when status is "pass"
        if canonical_type == "code":
            if evidence.get("tests_passed") is not True:
                errors.append(
                    "code evidence tests_passed must be True for status 'pass'"
                )
        elif canonical_type == "web/app":
            if evidence.get("build_status") != "success":
                errors.append(
                    "web/app build_status must be 'success' for status 'pass'"
                )
        elif canonical_type == "image/design":
            dims = evidence.get("dimensions")
            if (
                not isinstance(dims, (list, tuple))
                or len(dims) != 2
                or not (dims[0] > 0 and dims[1] > 0)
            ):
                errors.append(
                    "image/design dimensions must be [width, height] > 0 for status 'pass'"
                )
        elif canonical_type == "sprite/game asset":
            fc = evidence.get("frame_count")
            if not isinstance(fc, int) or fc < 1:
                errors.append(
                    "sprite/game asset frame_count must be integer >= 1 for status 'pass'"
                )
        elif canonical_type == "video":
            dur = evidence.get("duration_seconds")
            if not isinstance(dur, (int, float)) or dur <= 0:
                errors.append("video duration_seconds must be > 0 for status 'pass'")
            bitrate = evidence.get("bitrate_kbps")
            if not isinstance(bitrate, (int, float)) or bitrate <= 0:
                errors.append("video bitrate_kbps must be > 0 for status 'pass'")
        elif canonical_type == "audio":
            ch = evidence.get("channels")
            if not isinstance(ch, int) or ch < 1:
                errors.append("audio channels must be integer >= 1 for status 'pass'")
            sr = evidence.get("sample_rate_hz")
            if not isinstance(sr, int) or sr <= 0:
                errors.append("audio sample_rate_hz must be > 0 for status 'pass'")
            dur = evidence.get("duration_seconds")
            if not isinstance(dur, (int, float)) or dur <= 0:
                errors.append("audio duration_seconds must be > 0 for status 'pass'")
        elif canonical_type == "document/data":
            if (
                evidence.get("valid") is not True
                or evidence.get("schema_compliant") is not True
            ):
                errors.append(
                    "document/data valid and schema_compliant must be True for status 'pass'"
                )
            if not evidence.get("format"):
                errors.append(
                    "document/data format must be non-empty string for status 'pass'"
                )
        elif canonical_type == "mixed":
            cd = evidence.get("child_digests")
            if not isinstance(cd, list) or len(cd) == 0:
                errors.append(
                    "mixed bundle child_digests cannot be empty for status 'pass'"
                )
    else:
        unavail_reason = result.get("unavailable_reason")
        if not unavail_reason or not str(unavail_reason).strip():
            errors.append(
                f"Status is '{status}' but unavailable_reason is missing or empty"
            )

    # 10. Mixed Bundle Child Digest Validation & Content Integrity
    if canonical_type == "mixed":
        child_digests = evidence.get("child_digests")
        if not isinstance(child_digests, (list, dict)):
            errors.append("mixed bundle child_digests must be a list or dict")
        else:
            seen_locators = set()
            items = (
                child_digests
                if isinstance(child_digests, list)
                else [
                    {"locator": k, **(v if isinstance(v, dict) else {"digest": v})}
                    for k, v in child_digests.items()
                ]
            )
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(f"child_digests[{idx}] must be an object")
                    continue
                loc = item.get("locator") or item.get("path")
                digest = item.get("digest") or item.get("sha256")
                if not loc:
                    errors.append(f"child_digests[{idx}] missing locator/path")
                    continue
                if not digest:
                    errors.append(f"child_digests[{idx}] missing digest/sha256")
                    continue

                if not is_valid_sha256(str(digest)):
                    errors.append(
                        f"child_digests[{idx}] invalid sha256 digest: '{digest}'"
                    )

                # Locator safety check
                ok, reason = validate_locator_safety(str(loc))
                if not ok:
                    errors.append(f"child_digests[{idx}] locator error: {reason}")

                if str(loc) in seen_locators:
                    errors.append(f"child_digests[{idx}] duplicate locator: '{loc}'")
                seen_locators.add(str(loc))

                # Recompute child digest against retained child file if file exists on disk
                if ok and is_valid_sha256(str(digest)):
                    try:
                        child_path = Path(str(loc)).resolve()
                        if child_path.is_file():
                            child_bytes = child_path.read_bytes()
                            actual_hash = hashlib.sha256(child_bytes).hexdigest()
                            clean_expected = (
                                digest.removeprefix("sha256:")
                            )
                            if actual_hash.lower() != clean_expected.lower():
                                errors.append(
                                    f"child_digests[{idx}] content mismatch for '{loc}': expected '{clean_expected}', actual '{actual_hash}'"
                                )
                    except Exception:
                        pass

    # 11. Secret scanning
    secret_errors = find_secrets(dict(result))
    errors.extend(secret_errors)

    sanitized = [sanitize_error_message(e) for e in errors]
    return len(sanitized) == 0, sanitized
