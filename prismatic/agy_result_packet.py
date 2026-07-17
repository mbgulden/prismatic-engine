"""AGY result packet validation for the Prismatic autopilot lane.

This module intentionally avoids external JSON Schema dependencies. The adjacent
``schemas/agy-result-packet.schema.json`` file is the tool-facing contract; this
module is the runtime policy validator used by tests, ingestion, and future PR
helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

AGY_RESULT_PACKET_MARKER = "AGY_TASK_RESULT_PACKET_OK"
AGY_RESULT_PACKET_SCHEMA_MARKER = "AGY_RESULT_PACKET_SCHEMA_OK"

ALLOWED_BASE_BRANCHES = {"main"}
MERGE_LANES = {"dashboard-ui", "backend-api", "docs", "research", "mixed", "manual-review"}
RISK_LEVELS = {"low", "medium", "high"}
NEXT_ACTIONS = {"merge-ready", "needs-fred-cleanup", "needs-human-review", "blocked", "superseded"}
VERIFICATION_RESULTS = {"PASS", "FAIL", "BLOCKED"}
VERIFICATION_TYPES = {"ad-hoc targeted", "canonical suite"}
ISSUE_RE = re.compile(r"^GRO-[0-9]+$")
PR_URL_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/[0-9]+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY-----)",
    re.IGNORECASE,
)
SECRET_PATH_RE = re.compile(r"(^|/)(\.env|\.env\.|secrets?|credentials?|id_rsa|id_ed25519|\.ssh)(/|$)", re.IGNORECASE)
JUNK_PATH_RE = re.compile(
    r"(^|/)(\.venv|venv|node_modules|__pycache__|\.pytest_cache|\.mypy_cache|dist|build|\.next|\.turbo|vendor)(/|$)",
    re.IGNORECASE,
)
PRODUCTION_CLAIM_RE = re.compile(r"\b(production|prod|deployed|runtime|public)\b", re.IGNORECASE)
PRODUCTION_PROOF_RE = re.compile(r"\b(public|runtime|production|systemctl|curl|browser|screenshot|deploy)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise ResultPacketValidationError(self.errors)


class ResultPacketValidationError(ValueError):
    """Raised when an AGY result packet violates the contract."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("AGY result packet invalid: " + "; ".join(self.errors))


def load_packet(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ResultPacketValidationError(("packet must be a JSON object",))
    return data


def validate_packet(packet: Mapping[str, Any]) -> ValidationResult:
    errors: list[str] = []
    _validate_required_fields(packet, errors)
    if errors:
        return ValidationResult(False, tuple(errors))

    _expect(packet.get("agent") == "agy", "agent must be 'agy'", errors)
    issue = packet.get("issue_identifier")
    _expect(isinstance(issue, str) and bool(ISSUE_RE.match(issue)), "issue_identifier must match GRO-####", errors)
    _expect(_non_empty_string(packet.get("branch")), "branch must be a non-empty string", errors)
    _expect(packet.get("base_branch") in ALLOWED_BASE_BRANCHES, "base_branch must be main", errors)
    _expect(packet.get("merge_lane") in MERGE_LANES, "merge_lane is invalid", errors)
    _expect(packet.get("risk_level") in RISK_LEVELS, "risk_level is invalid", errors)
    _expect(packet.get("next_action") in NEXT_ACTIONS, "next_action is invalid", errors)
    _expect(packet.get("marker") == AGY_RESULT_PACKET_MARKER, f"marker must be {AGY_RESULT_PACKET_MARKER}", errors)

    pr_url = packet.get("pr_url")
    _expect(pr_url is None or (isinstance(pr_url, str) and bool(PR_URL_RE.match(pr_url))), "pr_url must be null or GitHub PR URL", errors)

    changed_files = _string_list(packet.get("changed_files"), "changed_files", errors)
    artifacts = _string_list(packet.get("result_artifacts"), "result_artifacts", errors)
    non_claims = _string_list(packet.get("non_claims"), "non_claims", errors)
    verification = packet.get("verification")
    if not isinstance(verification, Mapping):
        errors.append("verification must be an object")
        verification = {}
    commands = _string_list(verification.get("commands"), "verification.commands", errors, min_items=1)
    _expect(verification.get("result") in VERIFICATION_RESULTS, "verification.result is invalid", errors)
    _expect(_non_empty_string(verification.get("log_path")), "verification.log_path is required", errors)
    _expect(verification.get("ad_hoc_or_canonical") in VERIFICATION_TYPES, "verification.ad_hoc_or_canonical is invalid", errors)

    for field, values in [
        ("changed_files", changed_files),
        ("result_artifacts", artifacts),
        ("non_claims", non_claims),
        ("verification.commands", commands),
    ]:
        _reject_control_or_secret_values(field, values, errors)

    for value in changed_files:
        _validate_repoish_path("changed_files", value, errors)
    for value in artifacts:
        _validate_repoish_path("result_artifacts", value, errors, allow_absolute_tmp=True)

    log_path = verification.get("log_path")
    if isinstance(log_path, str):
        _reject_control_or_secret_values("verification.log_path", [log_path], errors)
        _validate_repoish_path("verification.log_path", log_path, errors, allow_absolute_tmp=True)

    if packet.get("merge_lane") == "dashboard-ui" and packet.get("next_action") == "merge-ready":
        _expect(bool(changed_files), "dashboard-ui merge-ready packets must list changed_files", errors)
        proof_text = "\n".join(commands + [str(log_path or "")])
        _expect(
            any(token in proof_text.lower() for token in ("browser", "screenshot", "dashboard", "node --check")),
            "dashboard-ui merge-ready packets require UI/browser or dashboard JS proof",
            errors,
        )

    if packet.get("next_action") == "merge-ready":
        _expect(verification.get("result") == "PASS", "merge-ready packets require verification.result PASS", errors)
        _expect(bool(artifacts), "merge-ready packets require at least one result_artifact", errors)
        _expect(packet.get("risk_level") != "high", "high risk packets cannot be merge-ready", errors)
        _expect(packet.get("merge_lane") not in {"mixed", "manual-review"}, "mixed/manual-review lanes cannot be merge-ready", errors)

    packet_text = json.dumps(packet, sort_keys=True, default=str)
    if PRODUCTION_CLAIM_RE.search(packet_text):
        proof_text = "\n".join(commands + [str(log_path or "")])
        _expect(bool(PRODUCTION_PROOF_RE.search(proof_text)), "production/runtime/public claims require explicit proof reference", errors)

    return ValidationResult(not errors, tuple(errors))


def require_valid_packet(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    result = validate_packet(packet)
    result.raise_for_errors()
    return packet


def _validate_required_fields(packet: Mapping[str, Any], errors: list[str]) -> None:
    required = {
        "agent",
        "issue_identifier",
        "branch",
        "base_branch",
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
    missing = sorted(required - set(packet))
    if missing:
        errors.append("missing required field(s): " + ", ".join(missing))


def _string_list(value: Any, field: str, errors: list[str], *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    if len(value) < min_items:
        errors.append(f"{field} must contain at least {min_items} item(s)")
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not _non_empty_string(item):
            errors.append(f"{field} items must be non-empty strings")
            continue
        if item in seen:
            errors.append(f"{field} contains duplicate value: {item}")
        seen.add(item)
        output.append(item)
    return output


def _validate_repoish_path(field: str, value: str, errors: list[str], *, allow_absolute_tmp: bool = False) -> None:
    if CONTROL_RE.search(value):
        errors.append(f"{field} contains control characters: {value!r}")
        return
    if value.startswith("/") and not (allow_absolute_tmp and value.startswith("/tmp/")):
        errors.append(f"{field} must not be an absolute path: {value}")
    path = PurePosixPath(value)
    if ".." in path.parts:
        errors.append(f"{field} must not contain traversal: {value}")
    normalized = str(path).strip("/")
    if SECRET_PATH_RE.search(normalized):
        errors.append(f"{field} contains secret-like path: {value}")
    if JUNK_PATH_RE.search(normalized):
        errors.append(f"{field} contains generated/vendor/cache path: {value}")


def _reject_control_or_secret_values(field: str, values: Sequence[str], errors: list[str]) -> None:
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
