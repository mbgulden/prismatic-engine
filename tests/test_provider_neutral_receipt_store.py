from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismatic.agy_operator_action_approval import (
    _policy_gate,
    _revalidated_approval_view,
)
from prismatic.agy_promotion_ledger import _native_acceptance_for
from prismatic.doctor import CapabilityReport, ProviderReport, _compute_verdict
from prismatic.gateway.server import app
from prismatic.verification.receipt_store import (
    OPTIONAL_HOSTED_SIGNAL,
    PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER,
    VerificationReceiptStore,
)
from tests.test_receipt_validator import (
    resign_receipt,
    valid_policy as _base_policy,
    valid_receipt as _base_receipt,
)

_NATIVE_GIT: dict[str, str] = {}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def native_git_checkout(tmp_path) -> None:
    root = (tmp_path / "clean-room").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Verifier Fixture")
    _git(root, "config", "user.email", "verifier@example.invalid")
    changed = root / "prismatic" / "verification" / "receipt_validator.py"
    changed.parent.mkdir(parents=True)
    changed.write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    base_sha = _git(root, "rev-parse", "HEAD")
    base_tree_sha = _git(root, "rev-parse", "HEAD^{tree}")
    changed.write_text("base\ncandidate\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "candidate")
    _NATIVE_GIT.clear()
    _NATIVE_GIT.update(
        root=str(root),
        base_sha=base_sha,
        base_tree_sha=base_tree_sha,
        candidate_sha=_git(root, "rev-parse", "HEAD"),
        tree_sha=_git(root, "rev-parse", "HEAD^{tree}"),
    )


def valid_receipt(finished_offset: float = -10) -> dict:
    receipt = _base_receipt(finished_offset)
    changed_paths = ["prismatic/verification/receipt_validator.py"]
    changed_paths_sha256 = hashlib.sha256(
        json.dumps(
            changed_paths, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    receipt.update(
        base_sha=_NATIVE_GIT["base_sha"],
        base_tree_sha=_NATIVE_GIT["base_tree_sha"],
        candidate_sha=_NATIVE_GIT["candidate_sha"],
        tree_sha=_NATIVE_GIT["tree_sha"],
        canonical_repository_root=_NATIVE_GIT["root"],
        checkout_clean_state={
            "status": "clean",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "porcelain_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        },
        source_locator=_NATIVE_GIT["root"],
        changed_paths=changed_paths,
        changed_path_containment={
            "allowed_roots": ["prismatic"],
            "contained": True,
            "changed_paths_sha256": f"sha256:{changed_paths_sha256}",
        },
        verifier_isolation={
            "independent": True,
            "network_isolated": True,
            "filesystem_isolated": True,
            "clean_room_id": "checkout-4208",
        },
        proof_scope_status={
            "focused": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "canonical": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "clean_room": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "package": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "production": {
                "status": "unavailable",
                "evidence_ids": [],
                "reason": "not claimed before deployment",
            },
            "browser": {
                "status": "unavailable",
                "evidence_ids": [],
                "reason": "not claimed by this fixture",
            },
        },
    )
    resign_receipt(receipt)
    return receipt


def valid_policy() -> dict:
    policy = _base_policy()
    policy["bindings"].update(
        expected_base_sha=_NATIVE_GIT["base_sha"],
        expected_candidate_sha=_NATIVE_GIT["candidate_sha"],
        expected_tree_sha=_NATIVE_GIT["tree_sha"],
    )
    return policy


def test_native_receipt_accepts_without_github_or_hosted_ci(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    row = store.persist(valid_receipt(), valid_policy())

    assert row.classification == "accepted"
    assert row.merge_eligible is True
    assert row.hosted_signals == []
    read_model = row.as_dict()
    assert read_model["hosted_signals_required"] is False
    assert read_model["marker"] == PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_MARKER
    assert read_model["source_provider"] == "none"
    assert read_model["source_kind"] == "local_bare_repository"
    assert read_model["clean_checkout_id"] == "checkout-4208"
    assert read_model["backend_class"] == "self_hosted_clean_room"
    assert read_model["source_acquisition_digest"]
    assert read_model["environment_digest"]
    assert store.counts() == {
        "accepted": 1,
        "blocked": 0,
        "stale": 0,
        "revoked": 0,
        "superseded": 0,
        "total": 1,
    }


def test_billing_red_hosted_signal_is_optional_and_cannot_block(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    row = store.persist(
        valid_receipt(),
        valid_policy(),
        hosted_signals=[
            {
                "provider": "github_actions",
                "status": "failure",
                "reason_code": "billing_spending_limit",
                "run_id": "30194453306",
            }
        ],
    )

    assert row.merge_eligible is True
    assert row.classification == "accepted"
    assert row.hosted_signals == [
        {
            "provider": "github_actions",
            "status": "failure",
            "signal_class": OPTIONAL_HOSTED_SIGNAL,
            "reason_code": "billing_spending_limit",
            "run_id": "30194453306",
        }
    ]


def test_same_receipt_replay_is_idempotent_and_conflict_fails_closed(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    receipt = valid_receipt()
    first = store.persist(receipt, valid_policy())
    replay = store.persist(copy.deepcopy(receipt), valid_policy())
    assert replay == first
    assert store.counts()["total"] == 1

    changed = copy.deepcopy(receipt)
    changed["non_claims"] = [*changed["non_claims"], "changed_after_verification"]
    resign_receipt(changed)
    with pytest.raises(ValueError, match="conflicting immutable"):
        store.persist(changed, valid_policy())
    assert store.get(first.receipt_id) == first
    assert store.counts()["total"] == 1


def test_concurrent_conflicting_replays_preserve_one_immutable_winner(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    original = valid_receipt()
    conflicting = copy.deepcopy(original)
    conflicting["non_claims"] = [*conflicting["non_claims"], "alternate_claim"]
    resign_receipt(conflicting)

    def attempt(receipt):
        try:
            return ("stored", store.persist(receipt, valid_policy()).receipt_sha256)
        except ValueError as exc:
            return ("conflict", str(exc))

    inputs = [original, conflicting] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, inputs))

    rows = store.list()
    assert len(rows) == 1
    assert store.counts()["total"] == 1
    assert {status for status, _ in outcomes} == {"stored", "conflict"}
    assert all(
        "conflicting immutable" in detail
        for status, detail in outcomes
        if status == "conflict"
    )


def test_supersession_disables_prior_receipt_without_mutating_signed_payload(
    tmp_path,
) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    original = store.persist(valid_receipt(), valid_policy())
    successor_receipt = valid_receipt()
    root = Path(_NATIVE_GIT["root"])
    changed = root / "prismatic" / "verification" / "receipt_validator.py"
    changed.write_text("base\ncandidate\nsuccessor\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "successor")
    successor_receipt["candidate_sha"] = _git(root, "rev-parse", "HEAD")
    successor_receipt["tree_sha"] = _git(root, "rev-parse", "HEAD^{tree}")
    successor_receipt["clean_checkout_id"] = "checkout-4208-successor"
    successor_receipt["verifier_isolation"]["clean_room_id"] = "checkout-4208-successor"
    successor_receipt["supersedes"] = original.receipt_id
    resign_receipt(successor_receipt)
    successor_policy = valid_policy()
    successor_policy["bindings"]["expected_candidate_sha"] = successor_receipt[
        "candidate_sha"
    ]
    successor_policy["bindings"]["expected_tree_sha"] = successor_receipt["tree_sha"]

    successor = store.persist(successor_receipt, successor_policy)
    prior = store.get(original.receipt_id)

    assert successor.classification == "accepted"
    assert successor.merge_eligible is True
    assert successor.receipt["supersedes"] == original.receipt_id
    assert prior.classification == "superseded"
    assert prior.merge_eligible is False
    assert prior.superseded_by == successor.receipt_id
    assert prior.receipt == original.receipt
    assert store.counts()["superseded"] == 1
    assert store.counts()["accepted"] == 1
    with sqlite3.connect(store.db_path) as conn:
        stored_row = conn.execute(
            """
            SELECT classification, merge_eligible, superseded_by
            FROM provider_neutral_verification_receipts
            WHERE receipt_id = ?
            """,
            (original.receipt_id,),
        ).fetchone()
        lifecycle_count = conn.execute(
            """
            SELECT COUNT(*) FROM provider_neutral_receipt_lifecycle_events
            WHERE receipt_id = ? AND event_type = 'superseded'
            """,
            (original.receipt_id,),
        ).fetchone()[0]
    assert stored_row == ("accepted", 1, None)
    assert lifecycle_count == 1


def test_unknown_supersession_fails_before_storage(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    receipt = valid_receipt()
    receipt["supersedes"] = "pnvr-" + "0" * 64
    resign_receipt(receipt)

    with pytest.raises(ValueError, match="supersedes receipt does not exist"):
        store.persist(receipt, valid_policy())
    assert store.counts()["total"] == 0


def test_stale_and_revoked_receipts_are_persisted_blocked(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    stale = store.persist(valid_receipt(finished_offset=-4000), valid_policy())
    assert stale.classification == "stale"
    assert stale.merge_eligible is False

    revoked_receipt = valid_receipt()
    revoked_receipt["task_id"] = "GRO-4208-REVOKED"
    revoked_receipt["revocation_status"] = "revoked"
    revoked_receipt["decision"] = {"status": "blocked", "merge_eligible": False}
    resign_receipt(revoked_receipt)
    revoked = store.persist(revoked_receipt, valid_policy())
    assert revoked.classification == "revoked"
    assert revoked.merge_eligible is False


def test_hosted_signal_rejects_urls_and_unbounded_metadata(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    with pytest.raises(ValueError, match="unsupported fields"):
        store.persist(
            valid_receipt(),
            valid_policy(),
            hosted_signals=[
                {
                    "provider": "github_actions",
                    "status": "failure",
                    "url": "https://example.invalid/run?token=not-retained",
                }
            ],
        )
    assert store.counts()["total"] == 0


def test_secret_like_receipt_is_rejected_before_storage(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "receipts.sqlite3")
    receipt = valid_receipt()
    key_name = "ACCESS" + "_TOKEN"
    token = "opaque" + "-credential-value-1234567890"
    receipt["non_claims"] = [*receipt["non_claims"], f"{key_name}={token}"]
    resign_receipt(receipt)

    with pytest.raises(ValueError, match="secret-like content detected"):
        store.persist(receipt, valid_policy())
    assert store.counts()["total"] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("base_tree", "base_tree_sha"),
        ("prefix_collision", "outside allowed roots"),
        ("independence", "verifier must be independent"),
        ("proof_link", "must link executed command IDs"),
    ],
)
def test_native_binding_failures_are_rejected_before_storage(
    tmp_path, mutation: str, message: str
) -> None:
    store = VerificationReceiptStore(tmp_path / f"{mutation}.sqlite3")
    receipt = valid_receipt()
    if mutation == "base_tree":
        receipt["base_tree_sha"] = "f" * 40
    elif mutation == "prefix_collision":
        receipt["changed_path_containment"]["allowed_roots"] = ["prism"]
    elif mutation == "independence":
        receipt["verifier_isolation"]["independent"] = False
    else:
        receipt["proof_scope_status"]["canonical"]["evidence_ids"] = ["not-executed"]
    resign_receipt(receipt)

    with pytest.raises(ValueError, match=message):
        store.persist(receipt, valid_policy())
    assert store.counts()["total"] == 0


def test_native_binding_rejects_symlink_repository_root(tmp_path) -> None:
    store = VerificationReceiptStore(tmp_path / "symlink.sqlite3")
    alias = tmp_path / "checkout-alias"
    alias.symlink_to(Path(_NATIVE_GIT["root"]), target_is_directory=True)
    receipt = valid_receipt()
    receipt["canonical_repository_root"] = str(alias)
    resign_receipt(receipt)

    with pytest.raises(ValueError, match="nonsymlink and canonical"):
        store.persist(receipt, valid_policy())


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_native_binding_rejects_dirty_checkout(tmp_path, dirty_kind: str) -> None:
    store = VerificationReceiptStore(tmp_path / f"dirty-{dirty_kind}.sqlite3")
    receipt = valid_receipt()
    root = Path(_NATIVE_GIT["root"])
    if dirty_kind == "tracked":
        (root / "prismatic/verification/receipt_validator.py").write_text(
            "dirty after signed candidate\n", encoding="utf-8"
        )
    else:
        (root / "untracked-proof-input.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checkout is not clean"):
        store.persist(receipt, valid_policy())
    assert store.counts()["total"] == 0


def test_external_and_append_only_revocation_remove_authorization(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "revoked.sqlite3"
    monkeypatch.setenv("PRISMATIC_VERIFICATION_RECEIPT_DB", str(db_path))
    store = VerificationReceiptStore(db_path)
    receipt = valid_receipt()
    stored = store.persist(receipt, valid_policy())
    row_payload = {
        "packet": {"issue_identifier": receipt["task_id"]},
        "evidence_retention": {"source_commit_sha": receipt["candidate_sha"]},
    }
    assert _native_acceptance_for(row_payload)["merge_authorized"] is True

    store.revocation_store_path.unlink()
    missing_store = store.get(stored.receipt_id)
    assert missing_store.merge_eligible is False
    assert missing_store.decision_reason == "revocation_store_missing"
    store.revocation_store_path.write_text("not-json", encoding="utf-8")
    malformed_store = store.get(stored.receipt_id)
    assert malformed_store.merge_eligible is False
    assert malformed_store.decision_reason == "malformed_revocation_store"

    store.revocation_store_path.write_text(
        json.dumps([receipt["task_id"]]), encoding="utf-8"
    )
    externally_revoked = store.get(stored.receipt_id)
    assert externally_revoked.classification == "revoked"
    assert externally_revoked.merge_eligible is False
    assert "revoked" in str(externally_revoked.decision_reason)
    assert _native_acceptance_for(row_payload)["merge_authorized"] is False

    store.revocation_store_path.write_text("[]\n", encoding="utf-8")
    lifecycle_revoked = store.revoke(
        stored.receipt_id, reason="compromised verifier", revoked_by="security-operator"
    )
    assert lifecycle_revoked.classification == "revoked"
    assert lifecycle_revoked.merge_eligible is False
    assert lifecycle_revoked.superseded_by is None
    replay = store.revoke(
        stored.receipt_id, reason="compromised verifier", revoked_by="security-operator"
    )
    assert replay.classification == "revoked"
    with pytest.raises(ValueError, match="conflicting immutable revocation"):
        store.revoke(
            stored.receipt_id, reason="different", revoked_by="security-operator"
        )
    assert _native_acceptance_for(row_payload)["merge_authorized"] is False


def test_promotion_authorization_requires_matching_effective_native_receipt(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "promotion-receipts.sqlite3"
    monkeypatch.setenv("PRISMATIC_VERIFICATION_RECEIPT_DB", str(db_path))
    store = VerificationReceiptStore(db_path)
    receipt = valid_receipt()
    stored = store.persist(
        receipt,
        valid_policy(),
        hosted_signals=[
            {
                "provider": "github_actions",
                "status": "failure",
                "reason_code": "billing_spending_limit",
            }
        ],
    )
    row_payload = {
        "packet": {"issue_identifier": receipt["task_id"]},
        "evidence_retention": {"source_commit_sha": receipt["candidate_sha"]},
    }

    decision = _native_acceptance_for(row_payload)
    assert decision["status"] == "accepted"
    assert decision["receipt_id"] == stored.receipt_id
    assert decision["merge_authorized"] is True
    assert decision["deploy_authorized"] is True
    assert decision["hosted_signals_required"] is False


def test_promotion_authorization_fails_closed_without_or_with_stale_receipt(
    tmp_path, monkeypatch
) -> None:
    missing_path = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("PRISMATIC_VERIFICATION_RECEIPT_DB", str(missing_path))
    row_payload = {
        "packet": {"issue_identifier": "GRO-4208"},
        "evidence_retention": {"source_commit_sha": _NATIVE_GIT["candidate_sha"]},
    }
    missing = _native_acceptance_for(row_payload)
    assert missing["reason"] == "native_receipt_store_missing"
    assert missing["merge_authorized"] is False

    store = VerificationReceiptStore(missing_path)
    store.persist(valid_receipt(finished_offset=-4000), valid_policy())
    stale = _native_acceptance_for(row_payload)
    assert stale["status"] == "stale"
    assert stale["merge_authorized"] is False
    assert stale["deploy_authorized"] is False


def test_downstream_operator_gate_requires_exact_native_authorization() -> None:
    promotion = {
        "status": "decision_ready",
        "recommendation": "open_or_update_pr",
        "target_issue": "GRO-4208",
        "completed_work_id": "work-1",
        "evidence": {
            "authorization": {
                "acceptance_authority": "native_provider_neutral_receipt",
                "merge_authorized": False,
                "deploy_authorized": False,
                "hosted_signals_required": False,
            },
            "native_acceptance": {
                "status": "blocked",
                "authoritative": True,
                "merge_authorized": False,
                "deploy_authorized": False,
            },
        },
    }
    assert _policy_gate(promotion, "approve", "open_or_update_pr") == "manual_review"

    promotion["evidence"]["authorization"]["merge_authorized"] = True
    promotion["evidence"]["native_acceptance"] = {
        "status": "accepted",
        "authoritative": True,
        "receipt_id": "pnvr-" + "a" * 64,
        "receipt_sha256": "b" * 64,
        "repository_id": "repo-prismatic-engine",
        "task_id": "GRO-4208",
        "base_sha": "c" * 40,
        "base_tree_sha": "d" * 40,
        "candidate_sha": "e" * 40,
        "tree_sha": "f" * 40,
        "checkout_clean_state": {"status": "clean"},
        "merge_authorized": True,
        "deploy_authorized": True,
    }
    assert _policy_gate(promotion, "approve", "open_or_update_pr") == "pass"


def test_cached_operator_approval_is_held_after_native_revocation(monkeypatch) -> None:
    current_promotion = {
        "status": "decision_ready",
        "recommendation": "open_or_update_pr",
        "target_issue": "GRO-4208",
        "completed_work_id": "work-1",
        "evidence": {
            "authorization": {
                "acceptance_authority": "native_provider_neutral_receipt",
                "merge_authorized": False,
                "deploy_authorized": False,
                "hosted_signals_required": False,
            },
            "native_acceptance": {
                "status": "revoked",
                "authoritative": True,
                "merge_authorized": False,
                "deploy_authorized": False,
            },
        },
    }
    monkeypatch.setattr(
        "prismatic.agy_operator_action_approval.get_promotion_decision",
        lambda _decision_id: current_promotion,
    )
    stored = {
        "operator_action_approval_id": "approval-1",
        "promotion_decision_id": "promotion-1",
        "completed_work_id": "work-1",
        "requested_action": "open_or_update_pr",
        "operator_decision": "approve",
        "policy_gate": "pass",
    }

    current = _revalidated_approval_view(stored)
    assert current["stored_policy_gate"] == "pass"
    assert current["policy_gate"] == "manual_review"
    assert current["revalidation_status"] == "held_by_current_native_receipt"
    assert current["execution_preview"]["would_execute"] is False


def test_doctor_github_absence_is_optional_warn_not_error() -> None:
    providers = [
        ProviderReport(name="github", status="disconnected"),
        ProviderReport(name="linear", status="connected"),
    ]
    capabilities = [CapabilityReport(name="native_receipts", status="ok")]

    assert _compute_verdict(providers, capabilities) == "WARN"
    assert _compute_verdict(providers, capabilities, {"github"}) == "ERROR"


def test_receipt_api_and_dashboard_contract_work_without_github(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "PRISMATIC_VERIFICATION_RECEIPT_DB", str(tmp_path / "receipts.sqlite3")
    )
    with TestClient(app) as client:
        schema_response = client.get("/api/verification/receipts/schema")
        assert schema_response.status_code == 200
        schema = schema_response.json()
        assert schema["acceptance_authority"] == "native_provider_neutral_receipt"
        assert schema["hosted_signals_required"] is False
        assert schema["non_claims"]["github_actions_required"] is False

        response = client.post(
            "/api/verification/receipts",
            json={
                "receipt": valid_receipt(),
                "policy": valid_policy(),
                "hosted_signals": [
                    {
                        "provider": "github_actions",
                        "status": "failure",
                        "reason_code": "billing_spending_limit",
                    }
                ],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["receipt"]["merge_eligible"] is True
        assert body["side_effects"] == {
            "github": False,
            "github_actions": False,
            "linear": False,
            "merge": False,
            "deploy": False,
        }

        listing = client.get("/api/verification/receipts?limit=1").json()
        assert listing["count"] == 1
        assert listing["hosted_signals_required"] is False
        assert listing["receipts"][0]["classification"] == "accepted"

        receipt_id = body["receipt"]["receipt_id"]
        revoked = client.post(
            f"/api/verification/receipts/{receipt_id}/revoke",
            json={"reason": "operator revoked", "revoked_by": "test-operator"},
        )
        assert revoked.status_code == 200
        revoked_body = revoked.json()
        assert revoked_body["status"] == "revoked"
        assert revoked_body["receipt"]["classification"] == "revoked"
        assert revoked_body["receipt"]["merge_eligible"] is False
        detail = client.get(f"/api/verification/receipts/{receipt_id}").json()
        assert detail["receipt"]["classification"] == "revoked"

        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        text = dashboard.text
        assert "Provider-Neutral Verification" in text
        assert "PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_OK" in text
        assert "OPTIONAL ${item.provider}" in text
        assert "/verification/receipts?limit=1" in text
        assert "GitHub and hosted CI are optional transport signals" in text
        assert "verified PR dry-run gates before Fred merge review" not in text
