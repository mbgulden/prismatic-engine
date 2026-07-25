"""Pure GitHub verification trigger and check projection adapter."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence
import urllib.parse

from .receipt_validator import determine_merge_eligibility

SHA40_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
ZERO_SHA40 = "0" * 40
MAX_PULL_REQUEST_NUMBER = 2_147_483_647


def _valid_git_ref(ref: Any) -> bool:
    if not isinstance(ref, str) or not ref or len(ref) > 256:
        return False
    if ref == "@" or ref.startswith(("/", ".")) or ref.endswith(("/", ".")):
        return False
    if ".." in ref or "@{" in ref or "//" in ref:
        return False
    if any(ord(char) <= 32 or ord(char) == 127 for char in ref):
        return False
    if any(char in "~^:?*[\\" for char in ref):
        return False
    return all(
        component and not component.startswith(".") and not component.endswith(".lock")
        for component in ref.split("/")
    )


class GitHubAdapterError(ValueError):
    """Error raised when normalizing GitHub trigger or projecting check run fails."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GitHubVerificationTrigger:
    delivery_id: str
    replay_key: str
    event: Literal["pull_request"]
    action: Literal["opened", "reopened", "synchronize"]
    repository_id: str
    repository_node_id: str
    repository_full_name: str
    pull_request_number: int
    base_ref: str
    base_sha: str
    head_ref: str
    candidate_sha: str
    source_payload_digest: str
    task_id: str


@dataclass(frozen=True)
class GitHubCheckRunProjection:
    repository_full_name: str
    head_sha: str
    name: str
    external_id: str
    status: Literal["completed"]
    conclusion: Literal["success", "failure", "action_required"]
    title: str
    summary: str
    details_url: str | None


def _validate_details_url(url: str | None, allowed_hosts: Sequence[str]) -> str | None:
    if not url or not isinstance(url, str):
        return None
    if len(url) > 2048 or isinstance(allowed_hosts, (str, bytes)):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() != "https":
            return None
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        normalized_hosts = {
            host.lower()
            for host in allowed_hosts
            if isinstance(host, str) and host and len(host) <= 253
        }
        if parsed.hostname.lower() not in normalized_hosts:
            return None
        return url
    except Exception:
        return None


def _get_receipt_digest(receipt: Any) -> str:
    if isinstance(receipt, dict):
        try:
            from .attestation import canonicalize_receipt

            canon = canonicalize_receipt(receipt)
            return hashlib.sha256(canon).hexdigest()
        except Exception:
            pass
        try:
            raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            pass
    return "0" * 64


def normalize_github_trigger(
    raw_body: bytes,
    headers: Mapping[str, str],
    *,
    secrets: Sequence[str],
    expected_repository_full_name: str,
    repository_id: str,
    max_body_bytes: int = 1_048_576,
) -> GitHubVerificationTrigger:
    if not isinstance(raw_body, bytes):
        raise GitHubAdapterError("raw_body must be bytes", code="invalid_body")

    if len(raw_body) > max_body_bytes:
        raise GitHubAdapterError(
            f"Body size {len(raw_body)} exceeds maximum allowed {max_body_bytes} bytes",
            code="body_size_exceeded",
        )

    repository_name_pattern = re.compile(
        r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z"
    )
    repository_id_text = str(repository_id)
    if not isinstance(
        expected_repository_full_name, str
    ) or not repository_name_pattern.fullmatch(expected_repository_full_name):
        raise GitHubAdapterError(
            "Invalid expected repository name", code="invalid_configuration"
        )
    if not repository_id_text.isdigit() or int(repository_id_text) <= 0:
        raise GitHubAdapterError(
            "Invalid expected repository id", code="invalid_configuration"
        )

    if isinstance(secrets, (str, bytes)) or not isinstance(secrets, Sequence):
        raise GitHubAdapterError(
            "Secrets must be a sequence of strings", code="missing_secrets"
        )

    if any(not isinstance(secret, str) for secret in secrets):
        raise GitHubAdapterError(
            "Secrets must contain only strings", code="invalid_secrets"
        )
    valid_secrets = [secret for secret in secrets if secret]
    if not valid_secrets:
        raise GitHubAdapterError(
            "No valid non-empty secret provided", code="missing_secrets"
        )

    headers_lower: dict[str, str] = {}
    for key, value in headers.items():
        normalized_key = str(key).lower()
        if normalized_key in headers_lower:
            raise GitHubAdapterError(
                "Ambiguous duplicate header", code="ambiguous_header"
            )
        headers_lower[normalized_key] = str(value)

    sig_header = headers_lower.get("x-hub-signature-256")
    if not sig_header:
        raise GitHubAdapterError(
            "Missing X-Hub-Signature-256 header", code="missing_signature"
        )

    if not re.fullmatch(r"sha256=[0-9a-f]{64}", sig_header):
        raise GitHubAdapterError(
            "Malformed X-Hub-Signature-256 header", code="malformed_signature"
        )
    provided_sig = sig_header[7:]

    sig_matched = False
    for secret in valid_secrets:
        computed = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        sig_matched |= hmac.compare_digest(computed, provided_sig)

    if not sig_matched:
        raise GitHubAdapterError(
            "HMAC signature verification failed", code="invalid_signature"
        )

    event_header = headers_lower.get("x-github-event")
    if not event_header:
        raise GitHubAdapterError("Missing X-GitHub-Event header", code="missing_event")
    if event_header != "pull_request":
        raise GitHubAdapterError("Unsupported event type", code="unsupported_event")

    delivery_id = headers_lower.get("x-github-delivery")
    if not delivery_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", delivery_id):
        raise GitHubAdapterError(
            "Missing or invalid X-GitHub-Delivery header", code="invalid_delivery_id"
        )

    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        raise GitHubAdapterError("Raw body is not valid UTF-8", code="invalid_utf8")

    try:
        payload = json.loads(body_text)
    except Exception:
        raise GitHubAdapterError("Raw body is not valid JSON", code="invalid_json")

    if not isinstance(payload, dict):
        raise GitHubAdapterError("JSON body must be an object", code="invalid_json")

    action = payload.get("action")
    if action not in ("opened", "reopened", "synchronize"):
        raise GitHubAdapterError("Unsupported action", code="unsupported_action")

    repo_data = payload.get("repository")
    if not isinstance(repo_data, dict):
        raise GitHubAdapterError("Missing repository object", code="invalid_payload")

    repo_full_name = repo_data.get("full_name")
    if repo_full_name != expected_repository_full_name:
        raise GitHubAdapterError(
            "Repository full_name mismatch", code="repository_mismatch"
        )

    repo_id_val = repo_data.get("id")
    if repo_id_val is None or str(repo_id_val) != str(repository_id):
        raise GitHubAdapterError("Repository id mismatch", code="repository_mismatch")

    repo_node_id = repo_data.get("node_id")
    if not isinstance(repo_node_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", repo_node_id
    ):
        raise GitHubAdapterError(
            "Missing or invalid repository node_id", code="invalid_payload"
        )

    pr_data = payload.get("pull_request")
    if not isinstance(pr_data, dict):
        raise GitHubAdapterError("Missing pull_request object", code="invalid_payload")

    pr_number = pr_data.get("number")
    if (
        isinstance(pr_number, bool)
        or not isinstance(pr_number, int)
        or not 1 <= pr_number <= MAX_PULL_REQUEST_NUMBER
    ):
        raise GitHubAdapterError(
            "Invalid pull_request number", code="invalid_pr_number"
        )

    base_data = pr_data.get("base")
    head_data = pr_data.get("head")
    if not isinstance(base_data, dict) or not isinstance(head_data, dict):
        raise GitHubAdapterError(
            "Missing base or head object in pull_request", code="invalid_payload"
        )

    base_repo = base_data.get("repo")
    head_repo = head_data.get("repo")

    if not isinstance(base_repo, dict) or not isinstance(head_repo, dict):
        raise GitHubAdapterError(
            "Fork or base repository object missing/ambiguous", code="fork_ambiguity"
        )

    if (
        base_repo.get("full_name") != expected_repository_full_name
        or str(base_repo.get("id")) != repository_id_text
        or base_repo.get("node_id") != repo_node_id
    ):
        raise GitHubAdapterError("Base repository mismatch", code="repository_mismatch")

    if (
        head_repo.get("full_name") != expected_repository_full_name
        or str(head_repo.get("id")) != repository_id_text
        or head_repo.get("node_id") != repo_node_id
    ):
        raise GitHubAdapterError("Fork repository mismatch", code="fork_mismatch")

    base_sha = base_data.get("sha")
    head_sha = head_data.get("sha")

    if (
        not isinstance(base_sha, str)
        or not SHA40_PATTERN.match(base_sha)
        or base_sha.lower() == ZERO_SHA40
    ):
        raise GitHubAdapterError("Invalid base SHA", code="invalid_sha")

    if (
        not isinstance(head_sha, str)
        or not SHA40_PATTERN.match(head_sha)
        or head_sha.lower() == ZERO_SHA40
    ):
        raise GitHubAdapterError("Invalid candidate head SHA", code="invalid_sha")

    base_sha = base_sha.lower()
    candidate_sha = head_sha.lower()

    base_ref = base_data.get("ref")
    head_ref = head_data.get("ref")

    if not _valid_git_ref(base_ref):
        raise GitHubAdapterError("Invalid base ref", code="invalid_ref")

    if not _valid_git_ref(head_ref):
        raise GitHubAdapterError("Invalid head ref", code="invalid_ref")
    assert isinstance(base_ref, str) and isinstance(head_ref, str)

    source_payload_digest = f"sha256:{hashlib.sha256(raw_body).hexdigest()}"
    replay_key = f"github:delivery:{delivery_id}"
    task_id = f"github:pr:{repository_id}:{pr_number}:{candidate_sha}"

    return GitHubVerificationTrigger(
        delivery_id=delivery_id,
        replay_key=replay_key,
        event="pull_request",
        action=action,
        repository_id=str(repository_id),
        repository_node_id=repo_node_id,
        repository_full_name=repo_full_name,
        pull_request_number=pr_number,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        candidate_sha=candidate_sha,
        source_payload_digest=source_payload_digest,
        task_id=task_id,
    )


def validate_trigger_receipt(
    trigger: GitHubVerificationTrigger,
    receipt: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    expected_tree_sha: str,
    revocation_store: Path | str | None = None,
    evidence_base_path: Path | str | None = None,
) -> tuple[bool, str | None]:
    if not isinstance(trigger, GitHubVerificationTrigger):
        return False, "invalid_trigger"
    if not isinstance(receipt, Mapping):
        return False, "invalid_receipt"
    if not isinstance(policy, Mapping):
        return False, "invalid_policy"

    if receipt.get("task_id") != trigger.task_id:
        return False, "task_id_mismatch"

    if str(receipt.get("repository_id")) != str(trigger.repository_id):
        return False, "repository_id_mismatch"

    if receipt.get("base_sha") != trigger.base_sha:
        return False, "base_sha_mismatch"

    if receipt.get("candidate_sha") != trigger.candidate_sha:
        return False, "candidate_sha_mismatch"

    if receipt.get("tree_sha") != expected_tree_sha:
        return False, "tree_sha_mismatch"

    provider_metadata = receipt.get("provider_metadata")
    if not isinstance(provider_metadata, dict):
        return False, "missing_provider_metadata"

    pr_id = provider_metadata.get("pull_request_id")
    if pr_id is None or str(pr_id) != str(trigger.pull_request_number):
        return False, "pull_request_id_mismatch"

    policy_copy = copy.deepcopy(dict(policy))
    bindings = dict(policy_copy.get("bindings", {}))
    bindings["expected_candidate_sha"] = trigger.candidate_sha
    bindings["expected_base_sha"] = trigger.base_sha
    bindings["expected_tree_sha"] = expected_tree_sha
    policy_copy["bindings"] = bindings

    eligible, reason = determine_merge_eligibility(
        dict(receipt),
        policy_copy,
        revocation_store=revocation_store,
        evidence_base_path=evidence_base_path,
    )
    if not eligible:
        return False, reason

    return True, None


def project_github_check_run(
    trigger: GitHubVerificationTrigger,
    receipt: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    expected_tree_sha: str,
    details_url: str | None = None,
    allowed_details_hosts: Sequence[str] = (),
    revocation_store: Path | str | None = None,
    evidence_base_path: Path | str | None = None,
) -> GitHubCheckRunProjection:
    validated_url = _validate_details_url(details_url, allowed_details_hosts)

    is_valid, reason = validate_trigger_receipt(
        trigger,
        receipt,
        policy,
        expected_tree_sha=expected_tree_sha,
        revocation_store=revocation_store,
        evidence_base_path=evidence_base_path,
    )

    policy_id = (
        policy.get("policy_id", "unknown") if isinstance(policy, Mapping) else "unknown"
    )
    policy_ver = (
        policy.get("policy_version", "unknown")
        if isinstance(policy, Mapping)
        else "unknown"
    )

    receipt_digest = _get_receipt_digest(receipt)

    ext_key = f"{policy_id}:{policy_ver}:{trigger.task_id}:{trigger.candidate_sha}:{expected_tree_sha}:{receipt_digest}"
    external_id = f"checkrun:{hashlib.sha256(ext_key.encode('utf-8')).hexdigest()}"

    safe_reason = (
        reason
        if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,96}", reason)
        else "unprovable_receipt"
    )

    if is_valid:
        conclusion: Literal["success", "failure", "action_required"] = "success"
        title = "Verification Passed"
        summary = f"Verification passed for task {trigger.task_id} and head SHA {trigger.candidate_sha[:8]}."
    else:
        if safe_reason in {
            "decision_status_fail",
            "decision_not_merge_eligible",
        }:
            conclusion = "failure"
            title = "Verification Failed"
            summary = f"Verification failed for task {trigger.task_id}: {safe_reason}."
        else:
            conclusion = "action_required"
            title = "Verification Action Required"
            summary = f"Verification blocked or unprovable for task {trigger.task_id}: {safe_reason}."

    return GitHubCheckRunProjection(
        repository_full_name=trigger.repository_full_name,
        head_sha=trigger.candidate_sha,
        name="prismatic/provider-neutral-verification",
        external_id=external_id,
        status="completed",
        conclusion=conclusion,
        title=title,
        summary=summary,
        details_url=validated_url,
    )
