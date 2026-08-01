"""Tests for pure GitHub verification trigger and check projection adapter."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import socket
import subprocess
from types import MappingProxyType

import pytest

from prismatic.verification.github_adapter import (
    GitHubAdapterError,
    normalize_github_trigger,
    project_github_check_run,
    validate_trigger_receipt,
)
from tests.test_receipt_validator import (
    resign_receipt,
)
from tests.test_receipt_validator import (
    valid_policy as _base_valid_policy,
)
from tests.test_receipt_validator import (
    valid_receipt as _base_valid_receipt,
)


def _make_payload(
    action: str = "opened",
    repo_full_name: str = "org/repo",
    repo_id: int | str = 123456,
    repo_node_id: str = "R_kgDO123456",
    pr_number: int = 42,
    base_ref: str = "main",
    base_sha: str = "a" * 40,
    head_ref: str = "feature",
    head_sha: str = "b" * 40,
    fork: bool = False,
    head_repo_override: dict | None = None,
) -> dict:
    base_repo_dict = {
        "id": int(repo_id) if str(repo_id).isdigit() else repo_id,
        "full_name": repo_full_name,
        "node_id": repo_node_id,
    }
    if head_repo_override is not None:
        head_repo_dict = head_repo_override
    elif fork:
        head_repo_dict = {
            "id": 999999,
            "full_name": "forkowner/repo",
            "node_id": "R_fork999999",
        }
    else:
        head_repo_dict = copy.deepcopy(base_repo_dict)

    return {
        "action": action,
        "repository": {
            "id": int(repo_id) if str(repo_id).isdigit() else repo_id,
            "full_name": repo_full_name,
            "node_id": repo_node_id,
        },
        "pull_request": {
            "number": pr_number,
            "title": "Confidential PR Title Do Not Leak",
            "body": "Confidential PR body containing secret data",
            "base": {
                "ref": base_ref,
                "sha": base_sha,
                "repo": base_repo_dict,
            },
            "head": {
                "ref": head_ref,
                "sha": head_sha,
                "repo": head_repo_dict,
            },
        },
    }


def _sign(body: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _make_valid_policy(
    repo_id: str = "123456",
    candidate_sha: str = "b" * 40,
    base_sha: str = "a" * 40,
    tree_sha: str = "c" * 40,
) -> dict:
    pol = _base_valid_policy()
    pol["repository"]["repository_id"] = str(repo_id)
    pol["repository"]["source_requirements"]["allowed_source_kinds"] = [
        "provider_remote",
        "local_bare_repository",
        "offline_git_bundle",
    ]
    pol["repository"]["source_requirements"]["allowed_source_providers"] = [
        "github",
        "gitlab",
        "bitbucket",
        "forgejo",
        "gitea",
        "other",
        "none",
    ]
    pol["bindings"]["expected_candidate_sha"] = candidate_sha
    pol["bindings"]["expected_base_sha"] = base_sha
    pol["bindings"]["expected_tree_sha"] = tree_sha
    return pol


def _make_valid_receipt(
    task_id: str,
    repo_id: str = "123456",
    candidate_sha: str = "b" * 40,
    base_sha: str = "a" * 40,
    tree_sha: str = "c" * 40,
    pr_number: int = 42,
    decision_status: str = "pass",
    merge_eligible: bool = True,
) -> dict:
    rec = _base_valid_receipt()
    rec["task_id"] = task_id
    rec["repository_id"] = str(repo_id)
    rec["candidate_sha"] = candidate_sha
    rec["base_sha"] = base_sha
    rec["tree_sha"] = tree_sha
    rec["provider_metadata"] = {"pull_request_id": str(pr_number)}
    rec["decision"] = {"status": decision_status, "merge_eligible": merge_eligible}
    return resign_receipt(rec)


# Test 1: Valid signed pull_request actions: opened, reopened, synchronize
@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_valid_signed_pr_actions(action: str) -> None:
    secret = "my-secret-key"
    payload = _make_payload(action=action)
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "deliv-12345",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )
    assert trigger.action == action
    assert trigger.event == "pull_request"
    assert trigger.delivery_id == "deliv-12345"
    assert trigger.pull_request_number == 42


# Test 2: Primary and secondary rotation secrets
def test_primary_and_secondary_rotation_secrets() -> None:
    primary = "primary-secret"
    secondary = "secondary-secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")

    headers = {
        "x-hub-signature-256": _sign(body, secondary),
        "x-github-event": "pull_request",
        "x-github-delivery": "deliv-sec",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[primary, secondary],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )
    assert trigger.delivery_id == "deliv-sec"


# Test 3: Missing/malformed/wrong signature and missing/empty secrets
def test_signature_and_secret_errors() -> None:
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    valid_sig = _sign(body, "sec")

    headers = {
        "X-Hub-Signature-256": valid_sig,
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }

    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers,
            secrets=[],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "missing_secrets"

    for invalid_secrets in ("sec", b"sec"):
        with pytest.raises(GitHubAdapterError) as exc_info:
            normalize_github_trigger(
                body,
                headers,
                secrets=invalid_secrets,  # type: ignore[arg-type]
                expected_repository_full_name="org/repo",
                repository_id="123456",
            )
        assert exc_info.value.code == "missing_secrets"

    headers_no_sig = {k: v for k, v in headers.items() if k != "X-Hub-Signature-256"}
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_no_sig,
            secrets=["sec"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "missing_signature"

    headers_malformed = {**headers, "X-Hub-Signature-256": "sha256=invalidhex"}
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_malformed,
            secrets=["sec"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "malformed_signature"

    headers_uppercase = {
        **headers,
        "X-Hub-Signature-256": valid_sig.upper().replace("SHA256=", "sha256="),
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_uppercase,
            secrets=["sec"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "malformed_signature"

    duplicate_headers = {
        **headers,
        "x-hub-signature-256": valid_sig,
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            duplicate_headers,
            secrets=["sec"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "ambiguous_header"

    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers,
            secrets=["wrong-secret"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_signature"


# Test 4: Raw-body HMAC contract and body-size bound
def test_raw_body_hmac_and_body_size_bound() -> None:
    secret = "secret"
    body = b"x" * 200
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }

    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
            max_body_bytes=100,
        )
    assert exc_info.value.code == "body_size_exceeded"


# Test 5: Invalid UTF-8/JSON and scalar JSON
def test_invalid_utf8_json_and_scalar_json() -> None:
    secret = "secret"

    bad_utf8 = b"\x80\x81\x82"
    headers_bad_utf8 = {
        "X-Hub-Signature-256": _sign(bad_utf8, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            bad_utf8,
            headers_bad_utf8,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_utf8"

    bad_json = b"{not json}"
    headers_bad_json = {
        "X-Hub-Signature-256": _sign(bad_json, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            bad_json,
            headers_bad_json,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_json"

    scalar_json = b'"just a string"'
    headers_scalar = {
        "X-Hub-Signature-256": _sign(scalar_json, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            scalar_json,
            headers_scalar,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_json"


# Test 6: Missing/unknown event, unsupported action, missing/oversized delivery ID
def test_missing_event_unsupported_action_invalid_delivery() -> None:
    secret = "secret"
    payload = _make_payload(action="closed")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body, secret)

    headers_no_event = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_no_event,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "missing_event"

    headers_wrong_event = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_wrong_event,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "unsupported_event"

    headers_ok = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_ok,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "unsupported_action"

    headers_huge_delivery = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "a" * 300,
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers_huge_delivery,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_delivery_id"


# Test 7: Repository mismatch, fork ambiguity, malformed PR number/ref/SHA, and all-zero SHA
def test_repo_mismatch_fork_ambiguity_and_sha_errors() -> None:
    secret = "secret"

    p_mismatch = _make_payload(repo_full_name="other/repo")
    b_mismatch = json.dumps(p_mismatch).encode("utf-8")
    h_mismatch = {
        "X-Hub-Signature-256": _sign(b_mismatch, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            b_mismatch,
            h_mismatch,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "repository_mismatch"

    p_fork = _make_payload(fork=True)
    b_fork = json.dumps(p_fork).encode("utf-8")
    h_fork = {
        "X-Hub-Signature-256": _sign(b_fork, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            b_fork,
            h_fork,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "fork_mismatch"

    p_zero = _make_payload(head_sha="0" * 40)
    b_zero = json.dumps(p_zero).encode("utf-8")
    h_zero = {
        "X-Hub-Signature-256": _sign(b_zero, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d1",
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            b_zero,
            h_zero,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_sha"


# Test 8: Deterministic trigger/task/replay identities and input non-mutation
def test_deterministic_identities_and_input_non_mutation() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    body_copy = bytes(body)
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    headers_copy = copy.deepcopy(headers)

    t1 = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )
    t2 = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    assert t1 == t2
    assert t1.task_id == f"github:pr:123456:42:{('b' * 40)}"
    assert t1.replay_key == "github:delivery:d123"
    assert body == body_copy
    assert headers == headers_copy


# Test 9: No raw payload/secret/arbitrary PR text in normalized output or errors
def test_no_raw_payload_or_secrets_leaked() -> None:
    secret = "SUPER_SECRET_KEY_123"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    trig_repr = str(trigger)
    assert secret not in trig_repr
    assert "Confidential" not in trig_repr

    try:
        normalize_github_trigger(
            body,
            headers,
            secrets=["wrong_secret_abc"],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    except GitHubAdapterError as exc:
        assert "wrong_secret_abc" not in str(exc)
        assert secret not in str(exc)


# Test 10: Receipt task/repository/base/candidate/tree/PR mismatches
def test_receipt_binding_mismatches() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    policy = _make_valid_policy()
    tree_sha = "c" * 40

    r_bad_task = _make_valid_receipt(task_id="wrong_task", tree_sha=tree_sha)
    ok, reason = validate_trigger_receipt(
        trigger, r_bad_task, policy, expected_tree_sha=tree_sha
    )
    assert not ok
    assert reason == "task_id_mismatch"

    r_bad_repo = _make_valid_receipt(
        task_id=trigger.task_id, repo_id="999", tree_sha=tree_sha
    )
    ok, reason = validate_trigger_receipt(
        trigger, r_bad_repo, policy, expected_tree_sha=tree_sha
    )
    assert not ok
    assert reason == "repository_id_mismatch"

    r_bad_pr = _make_valid_receipt(
        task_id=trigger.task_id, pr_number=999, tree_sha=tree_sha
    )
    ok, reason = validate_trigger_receipt(
        trigger, r_bad_pr, policy, expected_tree_sha=tree_sha
    )
    assert not ok
    assert reason == "pull_request_id_mismatch"


# Test 11: Local/offline source receipts remain acceptable when policy permits
def test_local_offline_source_receipt_acceptable() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    policy = _make_valid_policy()
    tree_sha = "c" * 40
    receipt = _make_valid_receipt(task_id=trigger.task_id, tree_sha=tree_sha)
    receipt["source_provider"] = "none"
    receipt["source_kind"] = "local_bare_repository"
    receipt = resign_receipt(receipt)

    ok, reason = validate_trigger_receipt(
        trigger, receipt, policy, expected_tree_sha=tree_sha
    )
    assert ok, f"Failed with reason: {reason}"
    assert reason is None


# Test 12: Invalid/stale/revoked/unsigned/untrusted receipts never project success
def test_invalid_receipt_projects_action_required_or_failure() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    policy = _make_valid_policy()
    tree_sha = "c" * 40

    r_fail = _make_valid_receipt(
        task_id=trigger.task_id,
        tree_sha=tree_sha,
        decision_status="fail",
        merge_eligible=False,
    )
    proj_fail = project_github_check_run(
        trigger, r_fail, policy, expected_tree_sha=tree_sha
    )
    assert proj_fail.conclusion == "failure"
    assert proj_fail.status == "completed"

    r_mismatch = _make_valid_receipt(task_id="wrong_task", tree_sha=tree_sha)
    proj_block = project_github_check_run(
        trigger, r_mismatch, policy, expected_tree_sha=tree_sha
    )
    assert proj_block.conclusion == "action_required"


# Test 13: Deterministic check-run payload/external ID, bounded fields, and URL restrictions
def test_check_run_projection_determinism_and_urls() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    policy = _make_valid_policy()
    tree_sha = "c" * 40
    receipt = _make_valid_receipt(task_id=trigger.task_id, tree_sha=tree_sha)

    proj1 = project_github_check_run(
        trigger,
        receipt,
        policy,
        expected_tree_sha=tree_sha,
        details_url="https://ci.example.com/build/123",
        allowed_details_hosts=["ci.example.com"],
    )
    proj2 = project_github_check_run(
        trigger,
        receipt,
        policy,
        expected_tree_sha=tree_sha,
        details_url="https://ci.example.com/build/123",
        allowed_details_hosts=["ci.example.com"],
    )

    assert proj1 == proj2
    assert proj1.conclusion == "success"
    assert proj1.name == "prismatic/provider-neutral-verification"
    assert proj1.details_url == "https://ci.example.com/build/123"

    proj_http = project_github_check_run(
        trigger,
        receipt,
        policy,
        expected_tree_sha=tree_sha,
        details_url="http://insecure.example.com",
    )
    assert proj_http.details_url is None

    for rejected_url, allowed_hosts in (
        ("https://ci.example.com/build/123", ()),
        ("https://evil.example/build/123", ("ci.example.com",)),
        ("https://user:pass@ci.example.com/build/123", ("ci.example.com",)),
    ):
        projection = project_github_check_run(
            trigger,
            receipt,
            policy,
            expected_tree_sha=tree_sha,
            details_url=rejected_url,
            allowed_details_hosts=allowed_hosts,
        )
        assert projection.details_url is None


# Test 14: Monkeypatch environment, config, subprocess, network, and event-bus access to raise
def test_pure_api_isolation_under_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: list, **kwargs: dict) -> None:
        raise AssertionError("Pure API attempted forbidden side effect")

    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d123",
    }

    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )

    policy = _make_valid_policy()
    tree_sha = "c" * 40
    receipt = _make_valid_receipt(task_id=trigger.task_id, tree_sha=tree_sha)

    ok, reason = validate_trigger_receipt(
        trigger, receipt, policy, expected_tree_sha=tree_sha
    )
    assert ok, f"Failed with reason: {reason}"
    assert reason is None

    proj = project_github_check_run(
        trigger, receipt, policy, expected_tree_sha=tree_sha
    )
    assert proj.conclusion == "success"


def test_independent_review_adversarial_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "primary-secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "review-adversarial",
    }

    original_compare = hmac.compare_digest
    compare_calls = 0

    def _counting_compare(left: str, right: str) -> bool:
        nonlocal compare_calls
        compare_calls += 1
        return original_compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", _counting_compare)
    normalize_github_trigger(
        body,
        headers,
        secrets=[secret, "secondary-secret"],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )
    assert compare_calls == 2

    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            headers,
            secrets=(secret, b"not-a-string"),  # type: ignore[arg-type]
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_secrets"

    arbitrary_event = "ARBITRARY_HEADER_LEAK_MARKER"
    event_headers = {**headers, "X-GitHub-Event": arbitrary_event}
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            body,
            event_headers,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert arbitrary_event not in str(exc_info.value)

    arbitrary_action = "ARBITRARY_PAYLOAD_LEAK_MARKER"
    action_payload = _make_payload(action=arbitrary_action)
    action_body = json.dumps(action_payload).encode("utf-8")
    action_headers = {
        **headers,
        "X-Hub-Signature-256": _sign(action_body, secret),
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            action_body,
            action_headers,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert arbitrary_action not in str(exc_info.value)

    for invalid_ref in ("feature\x00injected", "feature..branch", ".hidden", "x.lock"):
        ref_payload = _make_payload(head_ref=invalid_ref)
        ref_body = json.dumps(ref_payload).encode("utf-8")
        ref_headers = {**headers, "X-Hub-Signature-256": _sign(ref_body, secret)}
        with pytest.raises(GitHubAdapterError) as exc_info:
            normalize_github_trigger(
                ref_body,
                ref_headers,
                secrets=[secret],
                expected_repository_full_name="org/repo",
                repository_id="123456",
            )
        assert exc_info.value.code == "invalid_ref"

    huge_pr_payload = _make_payload(pr_number=int("9" * 1001))
    huge_pr_body = json.dumps(huge_pr_payload).encode("utf-8")
    huge_pr_headers = {
        **headers,
        "X-Hub-Signature-256": _sign(huge_pr_body, secret),
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            huge_pr_body,
            huge_pr_headers,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "invalid_pr_number"

    for location in ("base", "head"):
        node_payload = _make_payload()
        node_payload["pull_request"][location]["repo"]["node_id"] = "R_mismatch"
        node_body = json.dumps(node_payload).encode("utf-8")
        node_headers = {**headers, "X-Hub-Signature-256": _sign(node_body, secret)}
        with pytest.raises(GitHubAdapterError) as exc_info:
            normalize_github_trigger(
                node_body,
                node_headers,
                secrets=[secret],
                expected_repository_full_name="org/repo",
                repository_id="123456",
            )
        expected_code = "repository_mismatch" if location == "base" else "fork_mismatch"
        assert exc_info.value.code == expected_code


def test_mapping_receipt_digest_and_repository_id_are_bounded() -> None:
    secret = "secret"
    payload = _make_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body, secret),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "mapping-and-repo-bound",
    }
    trigger = normalize_github_trigger(
        body,
        headers,
        secrets=[secret],
        expected_repository_full_name="org/repo",
        repository_id="123456",
    )
    policy = _make_valid_policy()
    receipt_one = _make_valid_receipt(trigger.task_id)
    receipt_two = copy.deepcopy(receipt_one)
    receipt_two["source_locator"] = "different-but-valid-locator"
    receipt_two = resign_receipt(receipt_two)

    projection_one = project_github_check_run(
        trigger,
        MappingProxyType(receipt_one),
        policy,
        expected_tree_sha="c" * 40,
    )
    projection_two = project_github_check_run(
        trigger,
        MappingProxyType(receipt_two),
        policy,
        expected_tree_sha="c" * 40,
    )
    assert projection_one.conclusion == projection_two.conclusion == "success"
    assert projection_one.external_id != projection_two.external_id

    for invalid_repository_id in ("9" * 1000, str(2**63), "0", "01"):
        with pytest.raises(GitHubAdapterError) as exc_info:
            normalize_github_trigger(
                body,
                headers,
                secrets=[secret],
                expected_repository_full_name="org/repo",
                repository_id=invalid_repository_id,
            )
        assert exc_info.value.code == "invalid_configuration"

    huge_payload_id = 10**1000
    huge_payload = _make_payload(repo_id=huge_payload_id)
    huge_body = json.dumps(huge_payload).encode("utf-8")
    huge_headers = {
        **headers,
        "X-Hub-Signature-256": _sign(huge_body, secret),
    }
    with pytest.raises(GitHubAdapterError) as exc_info:
        normalize_github_trigger(
            huge_body,
            huge_headers,
            secrets=[secret],
            expected_repository_full_name="org/repo",
            repository_id="123456",
        )
    assert exc_info.value.code == "repository_mismatch"
