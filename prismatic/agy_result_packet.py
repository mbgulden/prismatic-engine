"""Strict raw AGY result-packet validation.

This validator is intentionally narrower than the completed-work normalizer.  It
only applies to canonical raw AGY packets before normalization, gate
classification, evidence retention, or SQLite persistence.  Existing normalized
Fred/Jules/completed-work packets are handled by the completed-work gate and are
not forced through this AGY-only schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

AGY_RESULT_PACKET_MARKER = "AGY_TASK_RESULT_PACKET_OK"
AGY_RESULT_PACKET_SCHEMA_MARKER = "AGY_RESULT_PACKET_SCHEMA_OK"
AGY_CLOSEOUT_V02_MARKER = "AGY_TASK_RESULT_PACKET_OK"
#: Tasks at or above this issue number require the v0.2 closeout contract.
#: Tasks below this number continue to use the legacy raw AGY dialect.
AGY_CLOSEOUT_V02_MIN_ISSUE = 4500

ALLOWED_BASE_BRANCHES = {"main", "origin/main"}
MERGE_LANES = {
    "dashboard-ui",
    "backend-api",
    "docs",
    "research",
    "mixed",
    "manual-review",
}
RISK_LEVELS = {"low", "medium", "high"}
NEXT_ACTIONS = {
    "merge-ready",
    "needs-fred-cleanup",
    "needs-human-review",
    "blocked",
    "superseded",
}
VERIFICATION_RESULTS = {"PASS", "FAIL", "BLOCKED"}
VERIFICATION_TYPES = {"ad-hoc targeted", "canonical suite"}
RAW_PACKET_FIELDS = {
    "agent",
    "issue_identifier",
    "branch",
    "base_branch",
    "source_commit_sha",
    "base_commit_sha",
    "changed_files",
    "pr_url",
    "result_artifacts",
    "verification",
    "non_claims",
    "merge_lane",
    "risk_level",
    "next_action",
    "marker",
}
RESULT_ARTIFACT_OBJECT_FIELDS = {"path"}
VERIFICATION_FIELDS = {"commands", "result", "log_path", "ad_hoc_or_canonical"}
REQUIRED_RAW_PACKET_FIELDS = RAW_PACKET_FIELDS - {
    "pr_url",
    "source_commit_sha",
    "base_commit_sha",
}
RAW_DIALECT_HINT_FIELDS = {
    "branch",
    "result_artifacts",
    "verification",
    "merge_lane",
    "risk_level",
    "next_action",
}
ISSUE_RE = re.compile(r"^GRO-[0-9A-Za-z-]+$")
PR_URL_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/[0-9]+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY-----|bearer\s+[A-Za-z0-9._-]{20,}|sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{20,}|\b(?:api[_-]?key|client[_-]?secret|private[_-]?key|secret[_-]?key|access[_-]?token|passwd|password|credential|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{12,}[\"']?)",
    re.IGNORECASE,
)
SECRET_PATH_RE = re.compile(
    r"(^|/)(\.env|\.env\.|secrets?|credentials?|tokens?|id_rsa|id_ed25519|\.ssh|\.aws|\.gemini|\.config)(/|$)",
    re.IGNORECASE,
)
JUNK_PATH_RE = re.compile(
    r"(^|/)(\.venv|venv|node_modules|__pycache__|\.pytest_cache|\.mypy_cache|dist|build|\.next|\.turbo|vendor)(/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise ResultPacketValidationError(self.errors)


class ResultPacketValidationError(ValueError):
    """Raised when a canonical raw AGY result packet violates policy."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("AGY raw result packet invalid: " + "; ".join(self.errors))


def is_v02_closeout_packet(packet: Mapping[str, Any]) -> bool:
    return (
        packet.get("agent") == "agy" and packet.get("MARKER") == AGY_CLOSEOUT_V02_MARKER
    )


def _parse_task_number(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"GRO-([0-9]+)", value.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def requires_v02_closeout(
    packet: Mapping[str, Any], trusted_issue: str | None = None
) -> bool:
    """Tasks at or above the cutoff MUST use the v0.2 closeout contract.

    The trusted issue identifier (when supplied by the dispatcher) is the
    authoritative source for activation. Falling back to producer-controlled
    fields alone would let a producer bypass the v0.2 contract by claiming a
    legacy task id under a modern dispatch.

    When ``trusted_issue`` is supplied and does not match any producer-declared
    numeric id, the producer's claim is rejected entirely.
    """
    trusted_number = _parse_task_number(trusted_issue)
    producer_numbers: list[int] = []
    for field in ("TASK_ID", "issue_identifier", "issue_id"):
        number = _parse_task_number(packet.get(field))
        if number is not None:
            producer_numbers.append(number)
    if trusted_number is not None:
        if any(n != trusted_number for n in producer_numbers):
            raise ResultPacketValidationError(
                (
                    f"producer issue identifier disagrees with trusted dispatch "
                    f"issue '{trusted_issue}'",
                )
            )
        return trusted_number >= AGY_CLOSEOUT_V02_MIN_ISSUE
    # No trusted context available: only treat modern dialect as required when
    # every producer-declared numeric id is at or above the cutoff. Any
    # producer field claiming a legacy id below the cutoff short-circuits.
    if not producer_numbers:
        return False
    if all(n < AGY_CLOSEOUT_V02_MIN_ISSUE for n in producer_numbers):
        return False
    return all(n >= AGY_CLOSEOUT_V02_MIN_ISSUE for n in producer_numbers)


def v02_provenance_required(
    packet: Mapping[str, Any], trusted_issue: str | None = None
) -> bool:
    """True if the v0.2 schema requires a trusted launch context for this packet."""
    return requires_v02_closeout(
        packet, trusted_issue=trusted_issue
    ) and is_v02_closeout_packet(packet)


def require_valid_v02_closeout(
    packet: Mapping[str, Any],
    launch_context: Any | None = None,
    directory: Path | None = None,
) -> Mapping[str, Any]:
    """Delegate to the importable v0.2 schema-driven validator.

    The runtime caller must separately enforce a launch context binding via
    :func:`require_valid_closeout_with_launch`; this entry point validates
    structure only. ``directory`` is forwarded so log/SHA/RESULT.md checks
    run by default whenever the runtime knows where the artifacts live.
    """
    from prismatic.skills.prismatic_agent_closeout_contract.scripts.validate_closeout_packet import (
        validate_closeout_packet,
    )

    outcome = validate_closeout_packet(
        packet, launch_context=launch_context, directory=directory
    )
    outcome.raise_for_status()
    return packet


def load_packet(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ResultPacketValidationError(("packet must be a JSON object",))
    return data


def is_raw_agy_result_packet(packet: Mapping[str, Any]) -> bool:
    """Return True only for the canonical raw AGY-result dialect.

    Normalized completed-work packets can also contain ``agent=agy`` and proof
    data.  They are not raw packets unless they carry the AGY marker and the
    full raw dialect shape, including risk/next-action fields.
    """

    return (
        packet.get("agent") == "agy"
        and packet.get("marker") == AGY_RESULT_PACKET_MARKER
        and RAW_DIALECT_HINT_FIELDS.issubset(packet.keys())
    )


def validate_packet(packet: Mapping[str, Any]) -> ValidationResult:
    errors: list[str] = []
    extra = sorted(set(packet) - RAW_PACKET_FIELDS)
    if extra:
        errors.append("unknown field(s): " + ", ".join(extra))

    missing = sorted(REQUIRED_RAW_PACKET_FIELDS - set(packet))
    if missing:
        errors.append("missing required field(s): " + ", ".join(missing))
    if errors:
        return ValidationResult(False, tuple(errors))

    _expect(packet.get("agent") == "agy", "agent must be 'agy'", errors)
    issue = packet.get("issue_identifier")
    _expect(
        isinstance(issue, str) and bool(ISSUE_RE.match(issue)),
        "issue_identifier must match GRO-*",
        errors,
    )

    branch = packet.get("branch")
    _expect(
        isinstance(branch, str) and branch.startswith("feature/"),
        "branch must be a feature/* branch",
        errors,
    )
    _expect(
        packet.get("base_branch") in ALLOWED_BASE_BRANCHES,
        "base_branch must be main or origin/main",
        errors,
    )
    _expect(packet.get("merge_lane") in MERGE_LANES, "merge_lane is invalid", errors)
    _expect(packet.get("risk_level") in RISK_LEVELS, "risk_level is invalid", errors)
    _expect(packet.get("next_action") in NEXT_ACTIONS, "next_action is invalid", errors)
    _expect(
        packet.get("marker") == AGY_RESULT_PACKET_MARKER,
        f"marker must be {AGY_RESULT_PACKET_MARKER}",
        errors,
    )

    pr_url = packet.get("pr_url")
    if "pr_url" in packet:
        _expect(
            pr_url is None
            or (isinstance(pr_url, str) and bool(PR_URL_RE.match(pr_url))),
            "pr_url must be null or GitHub PR URL",
            errors,
        )

    changed_files = _string_list(
        packet.get("changed_files"), "changed_files", errors, min_items=1
    )
    artifacts = _string_list(
        packet.get("result_artifacts"),
        "result_artifacts",
        errors,
        min_items=1,
        allow_object_paths=True,
    )
    non_claims = _string_list(packet.get("non_claims"), "non_claims", errors)
    verification = packet.get("verification")
    if not isinstance(verification, Mapping):
        errors.append("verification must be an object")
        verification = {}
    elif sorted(set(verification) - VERIFICATION_FIELDS):
        errors.append(
            "verification contains unknown field(s): "
            + ", ".join(sorted(set(verification) - VERIFICATION_FIELDS))
        )
    commands = _string_list(
        verification.get("commands"), "verification.commands", errors, min_items=1
    )
    _expect(
        verification.get("result") in VERIFICATION_RESULTS,
        "verification.result is invalid",
        errors,
    )
    _expect(
        _non_empty_string(verification.get("log_path")),
        "verification.log_path is required",
        errors,
    )
    _expect(
        verification.get("ad_hoc_or_canonical") in VERIFICATION_TYPES,
        "verification.ad_hoc_or_canonical is invalid",
        errors,
    )

    for field, values in (
        ("changed_files", changed_files),
        ("result_artifacts", artifacts),
        ("verification.commands", commands),
        ("verification.log_path", [str(verification.get("log_path", ""))]),
    ):
        _reject_control_or_secret_values(field, values, errors)
    # non_claims are explicitly negative claims; scan for secrets/control chars,
    # not for production words as positive assertions.
    _reject_control_or_secret_values("non_claims", non_claims, errors)
    # Same secret/path primitives also apply to changed_files and verification
    # commands (defense in depth: a secret-shaped command or a traversed path
    # must fail validation independently of how the legacy dialect labels them).
    for value in changed_files:
        _validate_repo_path("changed_files", value, errors)
    for value in commands:
        _validate_command_value("verification.commands", value, errors)
    for value in artifacts:
        _validate_artifact_path(value, errors)
    log_path = verification.get("log_path")
    if isinstance(log_path, str):
        _validate_log_path(log_path, errors)

    if packet.get("next_action") == "merge-ready":
        _expect(
            verification.get("result") == "PASS",
            "merge-ready packets require verification.result PASS",
            errors,
        )
        _expect(
            packet.get("risk_level") != "high",
            "high risk packets cannot be merge-ready",
            errors,
        )
        _expect(
            packet.get("merge_lane") not in {"mixed", "manual-review"},
            "mixed/manual-review lanes cannot be merge-ready",
            errors,
        )
    if packet.get("next_action") == "blocked":
        _expect(
            verification.get("result") in {"FAIL", "BLOCKED"},
            "next_action blocked requires verification.result FAIL or BLOCKED",
            errors,
        )
    if packet.get("risk_level") == "high":
        _expect(
            packet.get("next_action") != "merge-ready",
            "high risk packets must remain manual review or blocked",
            errors,
        )

    return ValidationResult(not errors, tuple(errors))


def require_valid_packet(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    result = validate_packet(packet)
    result.raise_for_errors()
    return packet


def _string_list(
    value: Any,
    field: str,
    errors: list[str],
    *,
    min_items: int = 0,
    allow_object_paths: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    if len(value) < min_items:
        errors.append(f"{field} must contain at least {min_items} item(s)")
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        path_item = item
        if allow_object_paths and isinstance(item, Mapping):
            extra_keys = sorted(set(item) - RESULT_ARTIFACT_OBJECT_FIELDS)
            if extra_keys:
                errors.append(
                    f"{field} object contains unknown key(s): " + ", ".join(extra_keys)
                )
            path_item = item.get("path")
        if not _non_empty_string(path_item):
            errors.append(f"{field} items must be non-empty strings")
            continue
        path_value = str(path_item)
        if path_value in seen:
            errors.append(f"{field} contains duplicate value")
        seen.add(path_value)
        output.append(path_value)
    return output


def _validate_repo_path(field: str, value: str, errors: list[str]) -> None:
    if value.startswith("/"):
        errors.append(f"{field} must not be an absolute path")
    _validate_safe_path(field, value, errors)


def _validate_command_value(field: str, value: str, errors: list[str]) -> None:
    """Apply the same secret/junk/path primitives to verification commands.

    Verification commands are not paths but they must still be free of control
    characters, secret-shaped payloads, and absolute paths that escape the
    repository tree. This mirrors what the v0.2 schema validator enforces for
    CHANGED_PATHS/COMMAND.
    """
    if CONTROL_RE.search(value):
        errors.append(f"{field} contains control characters")
        return
    if SECRET_VALUE_RE.search(value):
        errors.append(f"{field} contains secret-like content")
        return
    # Absolute paths inside commands must stay under the operator home.
    stripped = value.strip()
    if stripped.startswith("/"):
        home = str(Path.home()) + "/"
        if not (stripped.startswith(home) or stripped.startswith("/tmp/")):
            errors.append(f"{field} absolute path must stay under operator home")


def _validate_artifact_path(value: str, errors: list[str]) -> None:
    home_prefix = str(Path.home()) + "/"
    if value.startswith("/tmp/") and not value.startswith(home_prefix):
        errors.append("result_artifacts must not use arbitrary /tmp provenance paths")
    if value.startswith("/") and not value.startswith(str(Path.home()) + "/"):
        errors.append("result_artifacts absolute paths must stay under operator home")
    _validate_safe_path("result_artifacts", value, errors)


def _validate_log_path(value: str, errors: list[str]) -> None:
    if value.startswith("/") and not (
        value.startswith("/tmp/") or value.startswith(str(Path.home()) + "/")
    ):
        errors.append(
            "verification.log_path absolute paths must stay under /tmp or operator home"
        )
    _validate_safe_path("verification.log_path", value, errors)


def _validate_safe_path(field: str, value: str, errors: list[str]) -> None:
    if CONTROL_RE.search(value):
        errors.append(f"{field} contains control characters")
        return
    path = PurePosixPath(value)
    if ".." in path.parts:
        errors.append(f"{field} must not contain traversal")
    normalized = str(path).strip("/")
    if SECRET_PATH_RE.search(normalized):
        errors.append(f"{field} contains secret-like path")
    if JUNK_PATH_RE.search(normalized):
        errors.append(f"{field} contains generated/vendor/cache path")


def _reject_control_or_secret_values(
    field: str, values: Sequence[str], errors: list[str]
) -> None:
    for value in values:
        if CONTROL_RE.search(value):
            errors.append(f"{field} contains control characters")
        if SECRET_VALUE_RE.search(value):
            errors.append(f"{field} contains secret-like content")


def _expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
