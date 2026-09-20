"""Adversarial and functional unit tests for the Merge Factory admission, lease, lock, and judge system."""

from __future__ import annotations

import os
import concurrent.futures
import pytest
from pathlib import Path

from prismatic.core.merge_factory import (
    MergeFactoryStore,
    Principal,
    get_authenticated_principal,
)


# Setup test tokens in environment for tests
os.environ["PRISMATIC_MERGE_FACTORY_KEYS"] = (
    "factory-admin-secret-xyz:operator:admin:merge-factory-admin;"
    "george-judge-secret-abc:operator:george:merge-judge;"
    "agy-agent-secret-123:agent:agy:agent;"
    "ordinary-user-secret-999:user:ordinary:ordinary"
)


@pytest.fixture
def temp_db(tmp_path) -> Path:
    return tmp_path / "test_merge_factory.sqlite3"


@pytest.fixture
def store(temp_db) -> MergeFactoryStore:
    return MergeFactoryStore(db_path=temp_db)


# Principals
admin_principal = Principal("operator:admin", ["merge-factory-admin"])
george_principal = Principal("operator:george", ["merge-judge"])
agy_principal = Principal("agent:agy", ["agent"])
ordinary_principal = Principal("user:ordinary", ["ordinary"])


# ─────────────────────────────────────────────────────────────────────────
# ── Authentication / Principal Tests
# ─────────────────────────────────────────────────────────────────────────


def test_token_auth_resolution():
    # Test valid defaults
    p_admin = get_authenticated_principal("factory-admin-secret-xyz")
    assert p_admin.identity == "operator:admin"
    assert p_admin.has_scope("merge-factory-admin")

    p_george = get_authenticated_principal("george-judge-secret-abc")
    assert p_george.identity == "operator:george"
    assert p_george.has_scope("merge-judge")

    p_agy = get_authenticated_principal("agy-agent-secret-123")
    assert p_agy.identity == "agent:agy"
    assert p_agy.has_scope("agent")

    p_ordinary = get_authenticated_principal("ordinary-user-secret-999")
    assert p_ordinary.identity == "user:ordinary"
    assert p_ordinary.has_scope("ordinary")

    # Invalid token fails closed
    with pytest.raises(PermissionError):
        get_authenticated_principal("invalid-token")

    with pytest.raises(PermissionError):
        get_authenticated_principal("")


def test_custom_env_tokens(monkeypatch):
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        "custom-key-1-longer:custom:ident1:scope1,scope2;custom-key-2-longer:custom:ident2:scope3",
    )
    p1 = get_authenticated_principal("custom-key-1-longer")
    assert p1.identity == "custom:ident1"
    assert p1.scopes == ["scope1", "scope2"]

    p2 = get_authenticated_principal("custom-key-2-longer")
    assert p2.identity == "custom:ident2"
    assert p2.scopes == ["scope3"]


# ─────────────────────────────────────────────────────────────────────────
# ── Policy & Cohort Mutations
# ─────────────────────────────────────────────────────────────────────────


def test_policy_mutations(store):
    # Retrieve default policy
    pol = store.get_policy()
    assert pol == {"stage_cap": 1, "cron_paused": True}

    # Admin key updates policy successfully
    updated = store.set_policy({"stage_cap": 2, "cron_paused": False}, admin_principal)
    assert updated == {"stage_cap": 2, "cron_paused": False}

    # Ordinary key cannot update policy (fails closed)
    with pytest.raises(PermissionError):
        store.set_policy({"stage_cap": 3}, ordinary_principal)

    # Invalid policy value (integer out of range) fails closed
    with pytest.raises(ValueError):
        store.set_policy({"stage_cap": 4}, admin_principal)

    # Invalid policy value (wrong type) fails closed
    with pytest.raises(ValueError):
        store.set_policy({"cron_paused": "not-a-bool"}, admin_principal)

    # Unknown key fails
    with pytest.raises(ValueError):
        store.set_policy({"unknown_key": "val"}, admin_principal)


def test_cohort_mutations(store):
    # Admin can add to cohort
    item = store.add_to_cohort(
        "GRO-1111", stage=1, sequence=10, principal=admin_principal
    )
    assert item["issue_id"] == "GRO-1111"
    assert item["stage"] == 1
    assert item["status"] == "PENDING"
    assert item["sequence"] == 10

    # List cohort
    cohort = store.get_cohort()
    assert len(cohort) == 1
    assert cohort[0]["issue_id"] == "GRO-1111"

    # Ordinary key cannot add to cohort
    with pytest.raises(PermissionError):
        store.add_to_cohort(
            "GRO-2222", stage=1, sequence=20, principal=ordinary_principal
        )

    # Update status (admin only)
    updated = store.update_cohort_status("GRO-1111", "ADMITTED", admin_principal)
    assert updated["status"] == "ADMITTED"

    # Ordinary key cannot update status
    with pytest.raises(PermissionError):
        store.update_cohort_status("GRO-1111", "COMPLETED", ordinary_principal)


# ─────────────────────────────────────────────────────────────────────────
# ── Concurrency Leases & Global Cap
# ─────────────────────────────────────────────────────────────────────────


def test_lease_global_cap_and_concurrency(store):
    # Populate cohort for paused cron checks (cron is paused by default)
    store.add_to_cohort("GRO-1111", stage=1, sequence=1, principal=admin_principal)
    store.add_to_cohort("GRO-2222", stage=2, sequence=2, principal=admin_principal)

    # 1. Acquire first lease
    lease1 = store.acquire_lease(
        "GRO-1111", stage=1, ttl_seconds=60, principal=agy_principal
    )
    assert lease1["issue_id"] == "GRO-1111"
    assert lease1["owner_id"] == "agent:agy"

    # 2. Try to acquire second lease when cap is 1 (global cap enforcement)
    # Even though GRO-2222 is in a different stage (2), count should exceed global cap (1)
    with pytest.raises(BlockingIOError):
        store.acquire_lease(
            "GRO-2222", stage=2, ttl_seconds=60, principal=agy_principal
        )

    # 3. Renew own lease via acquire is NOT allowed (must fail closed)
    with pytest.raises(BlockingIOError):
        store.acquire_lease(
            "GRO-1111", stage=1, ttl_seconds=120, principal=agy_principal
        )

    # Heartbeat lease (must use heartbeat_lease with exact lease_id)
    hb = store.heartbeat_lease("GRO-1111", lease1["lease_id"], agy_principal)
    assert hb["issue_id"] == "GRO-1111"

    # 4. Ordinary user or other owner cannot steal active lease
    other_principal = Principal("agent:other", ["agent"])
    with pytest.raises(BlockingIOError):
        store.acquire_lease(
            "GRO-1111", stage=1, ttl_seconds=60, principal=other_principal
        )

    # 5. Increase cap to 2 -> now we can acquire the second lease
    store.set_policy({"stage_cap": 2}, admin_principal)
    lease2 = store.acquire_lease(
        "GRO-2222", stage=2, ttl_seconds=60, principal=other_principal
    )
    assert lease2["issue_id"] == "GRO-2222"
    assert lease2["owner_id"] == "agent:other"


def test_lease_stale_takeover_and_expiry(store):
    store.add_to_cohort("GRO-1111", stage=1, sequence=1, principal=admin_principal)

    # Acquire lease with 1 second TTL
    lease = store.acquire_lease(
        "GRO-1111", stage=1, ttl_seconds=1, principal=agy_principal
    )

    # Simulate expiration by waiting/manipulating database expires_at
    with store._connect() as conn:
        conn.execute("UPDATE concurrency_lease SET expires_at = '2000-01-01T00:00:00'")

    # Try to heartbeat expired lease: should fail
    with pytest.raises(KeyError):
        store.heartbeat_lease("GRO-1111", lease["lease_id"], agy_principal)

    # Another owner can take over because it is expired (pruned automatically during acquire)
    other_principal = Principal("agent:other", ["agent"])
    takeover = store.acquire_lease(
        "GRO-1111", stage=1, ttl_seconds=60, principal=other_principal
    )
    assert takeover["owner_id"] == "agent:other"


def test_old_backlog_exclusion_when_paused(store):
    # Ensure cron is paused
    store.set_policy({"cron_paused": True}, admin_principal)

    # Issue NOT in cohort (old backlog / unauthorized)
    # Trying to acquire lease should fail closed
    with pytest.raises(PermissionError):
        store.acquire_lease(
            "GRO-9999", stage=1, ttl_seconds=60, principal=agy_principal
        )

    # Add to cohort, but set status to EXCLUDED
    store.add_to_cohort("GRO-9999", stage=1, sequence=5, principal=admin_principal)
    store.update_cohort_status("GRO-9999", "EXCLUDED", admin_principal)

    with pytest.raises(PermissionError):
        store.acquire_lease(
            "GRO-9999", stage=1, ttl_seconds=60, principal=agy_principal
        )


# ─────────────────────────────────────────────────────────────────────────
# ── George Merge-Judge
# ─────────────────────────────────────────────────────────────────────────


def test_judge_attestations_and_transitions(store):
    issue = "GRO-1111"
    base = "base123"
    cand = "cand456"
    manifest = "m_digest"
    evidence = "e_digest"
    repo = "prismatic-engine"
    target = "main"

    # Ordinary key/principal cannot attest
    with pytest.raises(PermissionError):
        store.submit_attestation(
            issue_id=issue,
            decision="APPROVE_MERGE",
            base_sha=base,
            candidate_sha=cand,
            manifest_digest=manifest,
            evidence_digest=evidence,
            repository=repo,
            target=target,
            principal=agy_principal,
        )

    # George (merge-judge) can attest
    attest1 = store.submit_attestation(
        issue_id=issue,
        decision="APPROVE_MERGE",
        base_sha=base,
        candidate_sha=cand,
        manifest_digest=manifest,
        evidence_digest=evidence,
        repository=repo,
        target=target,
        principal=george_principal,
    )
    assert attest1["decision"] == "APPROVE_MERGE"
    assert attest1["reviewer"] == "operator:george"

    # Read-only validation is pure and passes for exact match
    val = store.validate_approval(issue, base, cand, manifest, evidence, repo, target)
    assert val["valid"] is True
    assert val["status"] == "VALID"
    assert val["attestation_id"] == attest1["attestation_id"]

    # Invalidation checks (mismatch in bindings)
    # 1. Base SHA changed (rebase/conflict)
    val_base_changed = store.validate_approval(
        issue, "newbase", cand, manifest, evidence, repo, target
    )
    assert val_base_changed["valid"] is False
    assert val_base_changed["status"] == "MISMATCH"

    # 2. Candidate SHA changed
    val_cand_changed = store.validate_approval(
        issue, base, "newcand", manifest, evidence, repo, target
    )
    assert val_cand_changed["valid"] is False
    assert val_cand_changed["status"] == "MISMATCH"

    # State transition: George marks for REPAIR
    attest2 = store.submit_attestation(
        issue_id=issue,
        decision="REPAIR",
        base_sha=base,
        candidate_sha=cand,
        manifest_digest=manifest,
        evidence_digest=evidence,
        repository=repo,
        target=target,
        principal=george_principal,
    )
    assert attest2["decision"] == "REPAIR"

    # Now validation returns invalid because latest decision is REPAIR
    val_post_repair = store.validate_approval(
        issue, base, cand, manifest, evidence, repo, target
    )
    assert val_post_repair["valid"] is False
    assert val_post_repair["status"] == "REPAIR"


def test_claimed_rf_executor_has_narrow_attestation_scope(store):
    claimed = Principal(
        "rf-claim:auth-123:standing-policy: tier-0", ["rf-merge-executor"]
    )
    approved = store.submit_attestation(
        issue_id="GRO-RF-CLAIM",
        decision="APPROVE_MERGE",
        base_sha="base",
        candidate_sha="candidate",
        manifest_digest="manifest",
        evidence_digest="evidence",
        repository="repo",
        target="main",
        principal=claimed,
    )
    assert approved["reviewer"].startswith("rf-claim:auth-123:")

    with pytest.raises(PermissionError, match="only append APPROVE_MERGE"):
        store.submit_attestation(
            issue_id="GRO-RF-CLAIM",
            decision="REPAIR",
            base_sha="base",
            candidate_sha="candidate",
            manifest_digest="manifest",
            evidence_digest="evidence",
            repository="repo",
            target="main",
            principal=claimed,
        )
    with pytest.raises(PermissionError):
        store.submit_attestation(
            issue_id="GRO-RF-FORGED",
            decision="APPROVE_MERGE",
            base_sha="base",
            candidate_sha="candidate",
            manifest_digest="manifest",
            evidence_digest="evidence",
            repository="repo",
            target="main",
            principal=Principal("not-a-claim", ["rf-merge-executor"]),
        )


# ─────────────────────────────────────────────────────────────────────────
# ── Merge Locks
# ─────────────────────────────────────────────────────────────────────────


def test_merge_locks_and_bindings(store):
    issue = "GRO-1111"
    base = "base123"
    cand = "cand456"
    manifest = "m_digest"
    evidence = "e_digest"
    repo = "prismatic-engine"
    target = "main"

    # Create approved attestation
    attest = store.submit_attestation(
        issue_id=issue,
        decision="APPROVE_MERGE",
        base_sha=base,
        candidate_sha=cand,
        manifest_digest=manifest,
        evidence_digest=evidence,
        repository=repo,
        target=target,
        principal=george_principal,
    )
    attest_id = attest["attestation_id"]

    # Cannot acquire lock with incorrect/unapproved bindings
    with pytest.raises(PermissionError):
        store.acquire_lock(
            repository=repo,
            target=target,
            issue_id=issue,
            base_sha="wrong-base",
            candidate_sha=cand,
            manifest_digest=manifest,
            evidence_digest=evidence,
            approval_attestation_id=attest_id,
            ttl_seconds=60,
            principal=agy_principal,
        )

    # Acquire lock with exact bindings
    lock = store.acquire_lock(
        repository=repo,
        target=target,
        issue_id=issue,
        base_sha=base,
        candidate_sha=cand,
        manifest_digest=manifest,
        evidence_digest=evidence,
        approval_attestation_id=attest_id,
        ttl_seconds=60,
        principal=agy_principal,
    )
    assert lock["lock_id"] == f"{repo}:{target}"
    assert lock["owner_principal"] == "agent:agy"
    acquisition_token = lock["acquisition_token"]
    assert acquisition_token

    # Shared principal/issue identity is not reentrant without the exact token.
    with pytest.raises(BlockingIOError):
        store.acquire_lock(
            repository=repo,
            target=target,
            issue_id=issue,
            base_sha=base,
            candidate_sha=cand,
            manifest_digest=manifest,
            evidence_digest=evidence,
            approval_attestation_id=attest_id,
            ttl_seconds=60,
            principal=agy_principal,
        )
    with pytest.raises(PermissionError):
        store.release_lock(
            repository=repo,
            target=target,
            issue_id=issue,
            principal=agy_principal,
            acquisition_token="wrong-token",
        )
    assert len(store.get_active_locks()) == 1

    # Heartbeat with exact token but changed bindings invalidates the lock.
    with pytest.raises(ValueError):
        store.heartbeat_lock(
            repository=repo,
            target=target,
            issue_id=issue,
            base_sha="changed-base",
            candidate_sha=cand,
            manifest_digest=manifest,
            evidence_digest=evidence,
            approval_attestation_id=attest_id,
            principal=agy_principal,
            acquisition_token=acquisition_token,
        )

    # Check lock deleted
    active = store.get_active_locks()
    assert len(active) == 0


def test_stale_lock_acquisition_token_cannot_control_replacement(store):
    from datetime import datetime, timedelta, timezone

    repo = "repo-stale-lock"
    target = "main"
    issue = "GRO-STALE-LOCK"
    base = "base"
    candidate = "candidate"
    manifest = "manifest"
    evidence = "evidence"
    attestation = store.submit_attestation(
        issue_id=issue,
        decision="APPROVE_MERGE",
        base_sha=base,
        candidate_sha=candidate,
        manifest_digest=manifest,
        evidence_digest=evidence,
        repository=repo,
        target=target,
        principal=george_principal,
    )
    common = dict(
        repository=repo,
        target=target,
        issue_id=issue,
        base_sha=base,
        candidate_sha=candidate,
        manifest_digest=manifest,
        evidence_digest=evidence,
        approval_attestation_id=attestation["attestation_id"],
        principal=agy_principal,
    )
    lock_a = store.acquire_lock(ttl_seconds=60, **common)
    with store._connect() as conn:
        conn.execute(
            "UPDATE merge_lock SET expires_at = ? WHERE lock_id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                lock_a["lock_id"],
            ),
        )
    lock_b = store.acquire_lock(ttl_seconds=60, **common)
    assert lock_b["acquisition_token"] != lock_a["acquisition_token"]

    with pytest.raises(PermissionError):
        store.heartbeat_lock(acquisition_token=lock_a["acquisition_token"], **common)
    with pytest.raises(PermissionError):
        store.release_lock(
            repository=repo,
            target=target,
            issue_id=issue,
            principal=agy_principal,
            acquisition_token=lock_a["acquisition_token"],
        )
    active = store.get_active_locks()
    assert len(active) == 1
    assert "acquisition_token" not in active[0]

    store.release_lock(
        repository=repo,
        target=target,
        issue_id=issue,
        principal=agy_principal,
        acquisition_token=lock_b["acquisition_token"],
    )
    assert store.get_active_locks() == []


def test_lock_api_forwards_exact_capability_and_rejects_wrong_release(
    store, monkeypatch
):
    import asyncio

    from fastapi import HTTPException
    from prismatic.api.routers import merge_factory as api

    repo = "repo-api"
    target = "main"
    issue = "GRO-LOCK-API"
    base = "base-api"
    candidate = "candidate-api"
    manifest = "manifest-api"
    evidence = "evidence-api"
    attestation = store.submit_attestation(
        issue_id=issue,
        decision="APPROVE_MERGE",
        base_sha=base,
        candidate_sha=candidate,
        manifest_digest=manifest,
        evidence_digest=evidence,
        repository=repo,
        target=target,
        principal=george_principal,
    )
    monkeypatch.setattr(api, "MergeFactoryStore", lambda: store)

    acquired = asyncio.run(
        api.acquire_lock(
            api.LockAcquireRequest(
                repository=repo,
                target=target,
                issue_id=issue,
                base_sha=base,
                candidate_sha=candidate,
                manifest_digest=manifest,
                evidence_digest=evidence,
                approval_attestation_id=attestation["attestation_id"],
                ttl_seconds=60,
            ),
            agy_principal,
        )
    )
    token = acquired["acquisition_token"]
    assert len(token) >= 32

    with pytest.raises(HTTPException) as wrong_release:
        asyncio.run(
            api.release_lock(
                api.LockReleaseRequest(
                    repository=repo,
                    target=target,
                    issue_id=issue,
                    acquisition_token="0" * 32,
                ),
                agy_principal,
            )
        )
    assert wrong_release.value.status_code == 403
    assert len(store.get_active_locks()) == 1

    heartbeat = asyncio.run(
        api.heartbeat_lock(
            api.LockHeartbeatRequest(
                repository=repo,
                target=target,
                issue_id=issue,
                base_sha=base,
                candidate_sha=candidate,
                manifest_digest=manifest,
                evidence_digest=evidence,
                approval_attestation_id=attestation["attestation_id"],
                acquisition_token=token,
            ),
            agy_principal,
        )
    )
    assert heartbeat["acquisition_token"] == token

    released = asyncio.run(
        api.release_lock(
            api.LockReleaseRequest(
                repository=repo,
                target=target,
                issue_id=issue,
                acquisition_token=token,
            ),
            agy_principal,
        )
    )
    assert released["status"] == "released"
    assert store.get_active_locks() == []


# ─────────────────────────────────────────────────────────────────────────
# ── Event Idempotency
# ─────────────────────────────────────────────────────────────────────────


def test_event_idempotency(store):
    event_id = "evt_10001"
    assert not store.is_event_processed(event_id)

    store.record_event_processed(event_id)
    assert store.is_event_processed(event_id)

    # Duplicate delivery check (safe, no error)
    store.record_event_processed(event_id)
    assert store.is_event_processed(event_id)


# ─────────────────────────────────────────────────────────────────────────
# ── Adversarial Tests (GRO-4111)
# ─────────────────────────────────────────────────────────────────────────


def test_no_config_fail_closed_auth(monkeypatch):
    # If no keys are configured, all auth checks must fail closed
    monkeypatch.delenv("PRISMATIC_MERGE_FACTORY_KEYS", raising=False)
    with pytest.raises(PermissionError):
        get_authenticated_principal("factory-admin-secret-xyz")
    with pytest.raises(PermissionError):
        get_authenticated_principal("some-random-token")


def test_default_known_token_denial(monkeypatch):
    # Clear env keys
    monkeypatch.delenv("PRISMATIC_MERGE_FACTORY_KEYS", raising=False)
    # Ensure default known tokens are denied
    for token in [
        "factory-admin-secret-xyz",
        "george-judge-secret-abc",
        "agy-agent-secret-123",
        "ordinary-user-secret-999",
    ]:
        with pytest.raises(PermissionError):
            get_authenticated_principal(token)


def test_runtime_key_rotation_and_removal(monkeypatch):
    # Initial config (keys must be >= 16 chars)
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS", "secret11111111111:ident1:scope1"
    )
    p1 = get_authenticated_principal("secret11111111111")
    assert p1.identity == "ident1"

    # Remove key (rotation/revocation)
    monkeypatch.delenv("PRISMATIC_MERGE_FACTORY_KEYS", raising=False)
    with pytest.raises(PermissionError):
        get_authenticated_principal("secret11111111111")

    # Rotate to a new key
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS", "secret22222222222:ident2:scope2"
    )
    with pytest.raises(PermissionError):
        get_authenticated_principal("secret11111111111")
    p2 = get_authenticated_principal("secret22222222222")
    assert p2.identity == "ident2"
    assert p2.scopes == ["scope2"]


def test_malformed_and_duplicate_config(monkeypatch):
    # Duplicate keys: must raise error during parsing (PermissionError due to parse fail)
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        "key11111111111111:ident1:scope1;key11111111111111:ident2:scope2;key22222222222222:ident3:scope3",
    )
    with pytest.raises(PermissionError):
        get_authenticated_principal("key11111111111111")

    # Malformed config must fail closed
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        ";key11111111111111:ident1:scope1;;malformed;key22222222222222;key22222222222222:ident2;key33333333333333:ident3:scope3:extra;key44444444444444:ident4:scope4",
    )
    with pytest.raises(PermissionError):
        get_authenticated_principal("key11111111111111")


def test_stage_mismatch_and_invalid_stage(store):
    # Add issue to cohort with stage 1
    store.add_to_cohort("GRO-8888", stage=1, sequence=1, principal=admin_principal)

    # Attempting to acquire lease with stage 2 should fail with ValueError (stage mismatch)
    with pytest.raises(ValueError, match="Stage mismatch"):
        store.acquire_lease(
            "GRO-8888", stage=2, ttl_seconds=60, principal=agy_principal
        )

    # Directly inject an invalid cohort stage in the DB to test "invalid stage"
    with store._connect() as conn:
        conn.execute(
            "UPDATE admission_cohort SET stage = 5 WHERE issue_id = 'GRO-8888'"
        )

    # Attempting to acquire lease should fail with ValueError (invalid stage)
    with pytest.raises(ValueError, match="Cohort stage must be 1, 2, or 3"):
        store.acquire_lease(
            "GRO-8888", stage=5, ttl_seconds=60, principal=agy_principal
        )


def test_excessive_ttl(store):
    # Add issue to cohort first
    store.add_to_cohort("GRO-8888", stage=1, sequence=1, principal=admin_principal)

    # 1. Lease TTL excessive (> 3600s)
    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        store.acquire_lease(
            "GRO-8888", stage=1, ttl_seconds=3601, principal=agy_principal
        )

    # 2. Lease TTL zero or negative
    with pytest.raises(ValueError, match="must be positive"):
        store.acquire_lease("GRO-8888", stage=1, ttl_seconds=0, principal=agy_principal)
    with pytest.raises(ValueError, match="must be positive"):
        store.acquire_lease(
            "GRO-8888", stage=1, ttl_seconds=-10, principal=agy_principal
        )

    # Create approved attestation for lock testing
    store.submit_attestation(
        issue_id="GRO-8888",
        decision="APPROVE_MERGE",
        base_sha="base123",
        candidate_sha="cand456",
        manifest_digest="m_digest",
        evidence_digest="e_digest",
        repository="repo",
        target="main",
        principal=george_principal,
    )
    attestations = store.get_decision_history("GRO-8888")
    attest_id = attestations[0]["attestation_id"]

    # 3. Lock TTL excessive (> 3600s)
    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        store.acquire_lock(
            repository="repo",
            target="main",
            issue_id="GRO-8888",
            base_sha="base123",
            candidate_sha="cand456",
            manifest_digest="m_digest",
            evidence_digest="e_digest",
            approval_attestation_id=attest_id,
            ttl_seconds=3601,
            principal=agy_principal,
        )

    # 4. Lock TTL zero or negative
    with pytest.raises(ValueError, match="must be positive"):
        store.acquire_lock(
            repository="repo",
            target="main",
            issue_id="GRO-8888",
            base_sha="base123",
            candidate_sha="cand456",
            manifest_digest="m_digest",
            evidence_digest="e_digest",
            approval_attestation_id=attest_id,
            ttl_seconds=0,
            principal=agy_principal,
        )
    with pytest.raises(ValueError, match="must be positive"):
        store.acquire_lock(
            repository="repo",
            target="main",
            issue_id="GRO-8888",
            base_sha="base123",
            candidate_sha="cand456",
            manifest_digest="m_digest",
            evidence_digest="e_digest",
            approval_attestation_id=attest_id,
            ttl_seconds=-100,
            principal=agy_principal,
        )


def test_installed_package_api_import_and_runtime(monkeypatch, tmp_path):
    # Set the state dir to temporary for api tests
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        "factory-admin-secret-xyz:operator:admin:merge-factory-admin,ordinary;agy-agent-secret-123:agy:agent:agent,ordinary",
    )

    # Import the FastAPI test client and server app
    from fastapi.testclient import TestClient
    from prismatic.api.server import app

    client = TestClient(app)

    # Ensure get policy returns default values
    response = client.get(
        "/api/v1/merge-factory/policy",
        headers={"Authorization": "Bearer " + "factory-admin-secret-xyz"},
    )
    assert response.status_code == 200
    assert response.json()["stage_cap"] == 1

    # Attempt to modify policy without token -> 401
    response = client.post("/api/v1/merge-factory/policy", json={"stage_cap": 2})
    assert response.status_code == 401

    # Attempt with invalid token -> 401
    response = client.post(
        "/api/v1/merge-factory/policy",
        json={"stage_cap": 2},
        headers={"Authorization": "Bearer " + "invalid-token"},
    )
    assert response.status_code == 401

    # Modify policy with admin token -> 200
    response = client.post(
        "/api/v1/merge-factory/policy",
        json={"stage_cap": 2},
        headers={"Authorization": "Bearer " + "factory-admin-secret-xyz"},
    )
    assert response.status_code == 200
    assert response.json()["stage_cap"] == 2

    # Add to cohort
    response = client.post(
        "/api/v1/merge-factory/cohort",
        json={"issue_id": "GRO-7777", "stage": 1, "sequence": 5},
        headers={"Authorization": "Bearer " + "factory-admin-secret-xyz"},
    )
    assert response.status_code == 200
    assert response.json()["issue_id"] == "GRO-7777"

    # Acquire lease with excessive TTL -> 400
    response = client.post(
        "/api/v1/merge-factory/lease/acquire",
        json={"issue_id": "GRO-7777", "stage": 1, "ttl_seconds": 3601},
        headers={"Authorization": "Bearer " + "agy-agent-secret-123"},
    )
    assert response.status_code == 400
    assert "exceeds maximum allowed" in response.json()["detail"]

    # Acquire lease with stage mismatch -> 400
    response = client.post(
        "/api/v1/merge-factory/lease/acquire",
        json={"issue_id": "GRO-7777", "stage": 2, "ttl_seconds": 60},
        headers={"Authorization": "Bearer " + "agy-agent-secret-123"},
    )
    assert response.status_code == 400
    assert "Stage mismatch" in response.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────
# ── GRO-4111 Simultaneous Contention, Race Proof, & Fencing Tests
# ─────────────────────────────────────────────────────────────────────────


def test_simultaneous_contention(temp_db):
    import threading

    # Setup initial store
    store = MergeFactoryStore(db_path=temp_db)

    # Enable cron and set cap to 1
    store.set_policy({"stage_cap": 1, "cron_paused": False}, admin_principal)

    # Add three issues to cohort
    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)
    store.add_to_cohort("GRO-B", stage=1, sequence=2, principal=admin_principal)
    store.add_to_cohort("GRO-C", stage=1, sequence=3, principal=admin_principal)

    # Define a target task for threads
    def try_acquire(issue_id, barrier, results_dict):
        barrier.wait()
        # Independently opened connection per thread
        t_store = MergeFactoryStore(db_path=temp_db)
        try:
            t_store.acquire_lease(
                issue_id, stage=1, ttl_seconds=60, principal=agy_principal
            )
            results_dict[issue_id] = (True, None)
        except Exception as e:
            results_dict[issue_id] = (False, e)

    # 1. At cap 1: exactly one of three contenders succeeds (repeat 20 times to catch oversubscription)
    for _ in range(20):
        # Clear the leases table
        with store._connect() as conn:
            conn.execute("DELETE FROM concurrency_lease")

        barrier = threading.Barrier(3)
        results = {}

        threads = []
        for issue_id in ["GRO-A", "GRO-B", "GRO-C"]:
            t = threading.Thread(target=try_acquire, args=(issue_id, barrier, results))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        successes = [k for k, v in results.items() if v[0]]
        failures = [k for k, v in results.items() if not v[0]]

        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
        assert len(failures) == 2
        for issue_id in failures:
            assert isinstance(results[issue_id][1], BlockingIOError)

    # 2. At cap 2: exactly two of three contenders succeed (repeat 20 times to catch oversubscription)
    store.set_policy({"stage_cap": 2}, admin_principal)

    for _ in range(20):
        # Clear the leases table
        with store._connect() as conn:
            conn.execute("DELETE FROM concurrency_lease")

        barrier = threading.Barrier(3)
        results = {}

        threads = []
        for issue_id in ["GRO-A", "GRO-B", "GRO-C"]:
            t = threading.Thread(target=try_acquire, args=(issue_id, barrier, results))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        successes = [k for k, v in results.items() if v[0]]
        failures = [k for k, v in results.items() if not v[0]]

        assert len(successes) == 2, f"Expected 2 successes, got {len(successes)}"
        assert len(failures) == 1
        assert isinstance(results[failures[0]][1], BlockingIOError)


def test_cap_change_acquisition_race(temp_db):
    import threading

    store = MergeFactoryStore(db_path=temp_db)

    # Start with cap = 2
    store.set_policy({"stage_cap": 2, "cron_paused": False}, admin_principal)
    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)
    store.add_to_cohort("GRO-B", stage=1, sequence=2, principal=admin_principal)

    # Acquire one lease
    store.acquire_lease("GRO-A", stage=1, ttl_seconds=60, principal=agy_principal)

    # Concurrently acquire second lease and decrease cap to 1
    barrier = threading.Barrier(2)

    def do_acquire():
        t_store = MergeFactoryStore(db_path=temp_db)
        barrier.wait()
        try:
            t_store.acquire_lease(
                "GRO-B", stage=1, ttl_seconds=60, principal=agy_principal
            )
            return "acquired"
        except BlockingIOError:
            return "blocked"

    def do_cap_change():
        t_store = MergeFactoryStore(db_path=temp_db)
        barrier.wait()
        try:
            t_store.set_policy({"stage_cap": 1}, admin_principal)
            return "changed"
        except ValueError:
            return "rejected"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_acq = executor.submit(do_acquire)
        f_cap = executor.submit(do_cap_change)

        acq_res = f_acq.result()
        cap_res = f_cap.result()

    active_leases = store.get_active_leases()
    policy = store.get_policy()

    if cap_res == "changed":
        # Cap was successfully reduced to 1. Acquire must have been blocked.
        assert acq_res == "blocked"
        assert policy["stage_cap"] == 1
        assert len(active_leases) == 1
    elif acq_res == "acquired":
        # Lease B was acquired. Cap change must have been rejected.
        assert cap_res == "rejected"
        assert policy["stage_cap"] == 2
        assert len(active_leases) == 2
    else:
        raise AssertionError(
            f"Unexpected outcome combination: acq={acq_res}, cap={cap_res}"
        )

    assert len(active_leases) <= policy["stage_cap"]


def test_cohort_idempotency_and_active_lease_fence(store):
    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)

    # Same-value replay should be idempotent and preserve added_at
    c1 = store.get_cohort()[0]
    res = store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)
    assert res["added_at"] == c1["added_at"]

    # Try to modify with different values without allow_update -> should fail
    with pytest.raises(ValueError, match="different values"):
        store.add_to_cohort("GRO-A", stage=1, sequence=2, principal=admin_principal)

    # Try to modify with different values WITH allow_update -> should succeed
    res2 = store.add_to_cohort(
        "GRO-A", stage=1, sequence=2, principal=admin_principal, allow_update=True
    )
    assert res2["sequence"] == 2
    assert res2["added_at"] == c1["added_at"]

    # Acquire a lease on GRO-A
    store.acquire_lease("GRO-A", stage=1, ttl_seconds=60, principal=agy_principal)

    # Same-value replay when active lease exists must succeed (idempotency)
    res_same = store.add_to_cohort(
        "GRO-A", stage=1, sequence=2, principal=admin_principal
    )
    assert res_same["sequence"] == 2

    # Try to modify sequence with active lease -> must fail
    with pytest.raises(ValueError, match="active lease"):
        store.add_to_cohort(
            "GRO-A", stage=1, sequence=3, principal=admin_principal, allow_update=True
        )

    # Status reset / change when active lease exists -> must fail
    with pytest.raises(ValueError, match="active lease"):
        store.update_cohort_status("GRO-A", "ADMITTED", principal=admin_principal)


def test_lease_fencing_and_row_count(store):
    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)
    lease = store.acquire_lease(
        "GRO-A", stage=1, ttl_seconds=60, principal=agy_principal
    )
    lease_id = lease["lease_id"]

    # Heartbeat with wrong lease_id -> must fail
    with pytest.raises(KeyError):
        store.heartbeat_lease("GRO-A", "wrong-lease-id", agy_principal)

    # Heartbeat with correct lease_id -> must succeed
    hb = store.heartbeat_lease("GRO-A", lease_id, agy_principal)
    assert hb["lease_id"] == lease_id

    # Release with wrong lease_id -> must fail
    with pytest.raises(KeyError):
        store.release_lease("GRO-A", "wrong-lease-id", agy_principal)

    # Release with correct lease_id -> must succeed
    store.release_lease("GRO-A", lease_id, agy_principal)

    # Double release or heartbeat after release -> must fail
    with pytest.raises(KeyError):
        store.release_lease("GRO-A", lease_id, agy_principal)
    with pytest.raises(KeyError):
        store.heartbeat_lease("GRO-A", lease_id, agy_principal)


def test_stale_same_principal_fencing(store):
    from datetime import datetime, timezone, timedelta

    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)

    # 1. Principal acquires lease A
    lease_a = store.acquire_lease(
        "GRO-A", stage=1, ttl_seconds=1, principal=agy_principal
    )
    lease_a_id = lease_a["lease_id"]

    # 2. Let lease A expire by manually updating its expires_at in the DB to a past time
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE concurrency_lease SET expires_at = ? WHERE lease_id = ?",
            (past_time, lease_a_id),
        )

    # 3. The same principal reacquires the lease, producing lease B
    lease_b = store.acquire_lease(
        "GRO-A", stage=1, ttl_seconds=60, principal=agy_principal
    )
    lease_b_id = lease_b["lease_id"]
    assert lease_b_id != lease_a_id

    # 4. Old lease A's ID tries to heartbeat or release
    # Heartbeat with A's ID must fail
    with pytest.raises(KeyError):
        store.heartbeat_lease("GRO-A", lease_a_id, agy_principal)

    # Release with A's ID must fail
    with pytest.raises(KeyError):
        store.release_lease("GRO-A", lease_a_id, agy_principal)

    # 5. Lease B must remain active
    active = store.get_active_leases()
    assert len(active) == 1
    assert active[0]["lease_id"] == lease_b_id


def test_repeated_acquire_regression_proof(store):
    from datetime import datetime, timezone, timedelta

    store.add_to_cohort("GRO-A", stage=1, sequence=1, principal=admin_principal)

    # 1. Principal acquires lease A
    lease_a = store.acquire_lease(
        "GRO-A", stage=1, ttl_seconds=60, principal=agy_principal
    )
    lease_a_id = lease_a["lease_id"]
    lease_a_expires = lease_a["expires_at"]

    # 2. Repeated acquire by the same principal must fail and NOT alter the current lease
    with pytest.raises(BlockingIOError):
        store.acquire_lease("GRO-A", stage=1, ttl_seconds=120, principal=agy_principal)

    # Verify lease A is completely unaltered
    active = store.get_active_leases()
    assert len(active) == 1
    assert active[0]["lease_id"] == lease_a_id
    assert active[0]["expires_at"] == lease_a_expires

    # 3. Now let lease A expire manually
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE concurrency_lease SET expires_at = ? WHERE lease_id = ?",
            (past_time, lease_a_id),
        )

    # 4. Same principal obtains lease B (since A was expired and pruned)
    lease_b = store.acquire_lease(
        "GRO-A", stage=1, ttl_seconds=60, principal=agy_principal
    )
    lease_b_id = lease_b["lease_id"]
    assert lease_b_id != lease_a_id

    # 5. Stale actor A cannot call acquire to extend or mutate B
    # Even if they try to call acquire, it will fail closed and not mutate lease B
    with pytest.raises(BlockingIOError):
        store.acquire_lease("GRO-A", stage=1, ttl_seconds=120, principal=agy_principal)

    # Verify lease B is completely unaltered
    active2 = store.get_active_leases()
    assert len(active2) == 1
    assert active2[0]["lease_id"] == lease_b_id
    assert active2[0]["expires_at"] == lease_b["expires_at"]


def test_cli_token_authentication(monkeypatch, tmp_path):
    from prismatic.cli.merge_factory import main as cli_main
    import io
    from contextlib import redirect_stdout, redirect_stderr

    # 1. Help/argv tests must prove no raw-token option exists
    f = io.StringIO()
    with redirect_stdout(f), redirect_stderr(io.StringIO()):
        try:
            cli_main(["cohort", "add", "--help"])
        except SystemExit:
            pass
    help_text = f.getvalue()
    assert "--token" not in help_text

    # Verify passing --token fails parsing
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        with pytest.raises(SystemExit):
            cli_main(["cohort", "add", "GRO-TEST", "1", "10", "--token", "some-token"])

    # 2. Test authentication via narrow environment variable
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_BEARER_TOKEN", "factory-admin-secret-xyz")
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        "factory-admin-secret-xyz:operator:admin:merge-factory-admin",
    )

    rc = cli_main(["cohort", "add", "GRO-CLI-ENV", "1", "10"])
    assert rc == 0

    # 3. Test authentication via token file (mode 0600)
    monkeypatch.delenv("PRISMATIC_BEARER_TOKEN", raising=False)
    token_file = tmp_path / "my_token"
    token_file.write_text("factory-admin-secret-xyz", encoding="utf-8")

    os.chmod(token_file, 0o600)
    monkeypatch.setenv("PRISMATIC_TOKEN_FILE", str(token_file))

    rc = cli_main(["cohort", "add", "GRO-CLI-FILE", "1", "11"])
    assert rc == 0

    # 4. Test authentication fails if token file has open permissions (e.g. 0644) on POSIX
    if os.name != "nt":
        os.chmod(token_file, 0o644)
        f_err = io.StringIO()
        with redirect_stderr(f_err), redirect_stdout(io.StringIO()):
            rc = cli_main(["cohort", "add", "GRO-CLI-FILE-BAD", "1", "12"])
        assert rc != 0
        assert (
            "permissions are too open" in f_err.getvalue() or "Error:" in f_err.getvalue()
        )


def test_validate_candidate_receipt_integration(store, tmp_path):
    candidate_sha = "a" * 40
    # Before receipt exists -> should be invalid
    res_empty = store.validate_candidate_receipt(candidate_sha, db_path=tmp_path / "rcpts.sqlite3")
    assert res_empty["valid"] is False
    assert "No active merge-eligible verification receipt" in res_empty["reason"]
