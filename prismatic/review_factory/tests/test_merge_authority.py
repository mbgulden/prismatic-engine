"""Tests for the auto-merge authority gate (chunk 1 of the post-shadow roadmap).

Every test asserts a safety property the authority claims:
- disabled means refused, before any side effect;
- gates fail closed;
- the mutex serializes merges and contention refuses;
- the executor is never invented — no executor configured means refuse;
- every decision is audited.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from prismatic.review_factory.merge_authority import (
    MergeAuthority,
    MergeDecision,
    MergeInput,
    RateLimiter,
    evaluate_gates,
    load_auto_policy,
)

HERE = Path(__file__).resolve()
SPEC_YAML = HERE.parent.parent / "spec" / "auto_merge_policy_v1.yaml"


# ── fakes ────────────────────────────────────────────────────────────


class FakeLock:
    """Stands in for the swarmlock lease context manager."""

    def __init__(self, fail=None):
        self.fail = fail
        self.entered = 0
        self.exited = 0
        self.acquire_calls = []

    def acquire(self, *, resource, holder, ttl_seconds):
        self.acquire_calls.append((resource, holder, ttl_seconds))
        if self.fail is not None:
            raise self.fail
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1
        return False


class FakeExecutor:
    def __init__(self, success=True, merge_sha="abc123", error="", blow_up=None):
        self.calls = []
        self.success = success
        self.merge_sha = merge_sha
        self.error = error
        self.blow_up = blow_up

    def execute(self, job_id):
        self.calls.append(job_id)
        if self.blow_up is not None:
            raise self.blow_up
        return SimpleNamespace(
            success=self.success, merge_sha=self.merge_sha, error=self.error
        )


# ── fixtures ─────────────────────────────────────────────────────────


def _write_policy(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _green_input(**overrides):
    kwargs = dict(
        job_id="job-1",
        repository="mbgulden/prismatic-engine",
        head_sha="f" * 40,
        tier=0,
        ci_green_self_hosted=True,
        ruff_clean=True,
        verdict="CLEAN",
        no_merge_conflicts=True,
        branch_protection_satisfied=True,
        verification_receipt_id="rcpt-1",
    )
    kwargs.update(overrides)
    return MergeInput(**kwargs)


class _PassingReceiptJudge:
    """Fake ReceiptJudge that approves every receipt (happy-path tests)."""

    def __init__(self):
        self.calls = []

    def validate(self, *, receipt_id, expected_candidate_sha,
                 expected_tree_sha=None):
        self.calls.append(
            {
                "receipt_id": receipt_id,
                "expected_candidate_sha": expected_candidate_sha,
                "expected_tree_sha": expected_tree_sha,
            }
        )
        return True, None


class _RefusingReceiptJudge:
    """Fake ReceiptJudge that refuses with a fixed reason."""

    def __init__(self, reason="receipt_not_found"):
        self.reason = reason
        self.calls = []

    def validate(self, *, receipt_id, expected_candidate_sha,
                 expected_tree_sha=None):
        self.calls.append(receipt_id)
        return False, self.reason


@pytest.fixture()
def passing_judge():
    return _PassingReceiptJudge()


@pytest.fixture()
def disabled_authority(tmp_path):
    lock = FakeLock()
    executor = FakeExecutor()
    auth = MergeAuthority(
        policy_path=SPEC_YAML,
        lock_client=lock,
        executor=executor,
        audit_log=tmp_path / "audit.jsonl",
    )
    return auth, lock, executor


@pytest.fixture()
def enabled_authority(tmp_path, passing_judge):
    policy = _write_policy(tmp_path, enabled=True)
    lock = FakeLock()
    executor = FakeExecutor()
    auth = MergeAuthority(
        policy_path=policy,
        lock_client=lock,
        executor=executor,
        audit_log=tmp_path / "audit.jsonl",
        receipt_judge=passing_judge,
    )
    return auth, lock, executor


def _read_audit(tmp_path):
    rows = []
    for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines():
        rows.append(json.loads(line))
    return rows


# ── the shipped policy is off ────────────────────────────────────────


def test_shipped_policy_is_disabled():
    policy = load_auto_policy(SPEC_YAML)
    assert policy.enabled is False
    assert policy.version == "auto-v1"


def test_default_policy_path_resolves_to_shipped_file():
    from prismatic.review_factory import merge_authority as mod

    assert mod.DEFAULT_POLICY_FILE.name == "auto_merge_policy_v1.yaml"


def test_disabled_refuses_everything_before_any_side_effect(
    disabled_authority, tmp_path
):
    auth, lock, executor = disabled_authority
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == "auto_merge_disabled"
    assert decision.allowed is False
    assert lock.acquire_calls == []  # mutex never touched
    assert executor.calls == []  # executor never called


def test_disabled_still_audits(disabled_authority, tmp_path):
    auth, _, _ = disabled_authority
    auth.request_merge(_green_input())
    rows = _read_audit(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "refused"
    assert row["reason"] == "auto_merge_disabled"
    assert row["policy_version"] == "auto-v1"
    assert row["jev_score"] is None
    assert row["job_id"] == "job-1"


def test_missing_policy_file_fails_closed_to_disabled(tmp_path):
    auth = MergeAuthority(
        policy_path=tmp_path / "does-not-exist.yaml",
        audit_log=tmp_path / "audit.jsonl",
    )
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == "auto_merge_disabled"


def test_malformed_policy_refuses_everything(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: [unclosed\n", encoding="utf-8")
    auth = MergeAuthority(policy_path=bad, audit_log=tmp_path / "audit.jsonl")
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason.startswith("policy_config_error")


def test_missing_enabled_key_defaults_off(tmp_path):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    del data["enabled"]
    path = tmp_path / "noenabled.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    auth = MergeAuthority(policy_path=path, audit_log=tmp_path / "audit.jsonl")
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == "auto_merge_disabled"


# ── gates ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    [
        "ci_green_self_hosted",
        "ruff_clean",
        "no_merge_conflicts",
        "branch_protection_satisfied",
    ],
)
def test_each_boolean_gate_failure_refuses(enabled_authority, field):
    auth, lock, executor = enabled_authority
    pr = _green_input(**{field: False})
    decision = auth.request_merge(pr)
    assert decision.decision == "refused"
    assert f"gate_failed:{field}" in decision.reason
    assert executor.calls == []


@pytest.mark.parametrize("verdict", ["REJECT", "REPAIR", "UNKNOWN"])
def test_non_mergeable_verdict_refuses(enabled_authority, verdict):
    auth, _, executor = enabled_authority
    decision = auth.request_merge(_green_input(verdict=verdict))
    assert decision.decision == "refused"
    assert "gate_failed:verdict_not_reject" in decision.reason
    assert executor.calls == []


def test_unknown_gate_id_fails_closed(tmp_path):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["gates"] = [{"id": "bogus_future_gate", "description": "not understood"}]
    path = tmp_path / "unknowngate.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    auth = MergeAuthority(
        policy_path=path,
        lock_client=FakeLock(),
        executor=FakeExecutor(),
        audit_log=tmp_path / "audit.jsonl",
        receipt_judge=_PassingReceiptJudge(),
    )
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert "gate_failed:bogus_future_gate" in decision.reason


def test_evaluate_gates_is_pure():
    policy = load_auto_policy(SPEC_YAML)
    pr = _green_input()
    first = evaluate_gates(policy, pr)
    second = evaluate_gates(policy, pr)
    assert first == second
    assert all(g.passed for g in first)


# ── tier, rate limits ────────────────────────────────────────────────


def test_tier_above_max_refused(enabled_authority):
    auth, _, executor = enabled_authority
    decision = auth.request_merge(_green_input(tier=1))
    assert decision.decision == "refused"
    assert decision.reason.startswith("tier_refused")
    assert executor.calls == []


def test_hourly_rate_limit_refuses_second_allow(tmp_path):
    policy = _write_policy(tmp_path, enabled=True)
    log = tmp_path / "audit.jsonl"
    now = 1_750_000_000.0
    # One allowed decision inside the last hour already.
    log.write_text(
        json.dumps({"ts": now - 100, "decision": "allowed", "job_id": "old"}) + "\n",
        encoding="utf-8",
    )
    auth = MergeAuthority(
        policy_path=policy,
        lock_client=FakeLock(),
        executor=FakeExecutor(),
        audit_log=log,
        now_fn=lambda: now,
    )
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason.startswith("rate_limit")


def test_daily_rate_limit_refuses(tmp_path):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["rate_limits"] = {"max_per_hour": 100, "max_per_day": 2}
    path = tmp_path / "daily.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    log = tmp_path / "audit.jsonl"
    now = 1_750_000_000.0
    lines = "".join(
        json.dumps({"ts": now - 7200 * (i + 1), "decision": "allowed"}) + "\n"
        for i in range(2)
    )
    log.write_text(lines, encoding="utf-8")
    auth = MergeAuthority(
        policy_path=path,
        lock_client=FakeLock(),
        executor=FakeExecutor(),
        audit_log=log,
        now_fn=lambda: now,
    )
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert "last day" in decision.reason


def test_refused_rows_do_not_consume_budget(tmp_path):
    policy = _write_policy(tmp_path, enabled=True)
    log = tmp_path / "audit.jsonl"
    now = 1_750_000_000.0
    log.write_text(
        json.dumps({"ts": now - 100, "decision": "refused", "job_id": "old"}) + "\n",
        encoding="utf-8",
    )
    limiter = RateLimiter(load_auto_policy(policy).rate_limits, log)
    assert limiter.would_exceed(now) is None


def test_corrupt_log_lines_do_not_crash_the_gate(tmp_path):
    policy = _write_policy(tmp_path, enabled=True)
    log = tmp_path / "audit.jsonl"
    log.write_text("not json at all\n", encoding="utf-8")
    limiter = RateLimiter(load_auto_policy(policy).rate_limits, log)
    assert limiter.would_exceed() is None


# ── mutex ────────────────────────────────────────────────────────────


def test_lock_contention_refuses_and_never_calls_executor(enabled_authority):
    class LockContended(Exception):
        """Stands in for swarmlock's LockConflictError (dependency-agnostic)."""

    auth, lock, executor = enabled_authority
    lock.fail = LockContended("held by someone else")
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason.startswith("merge_mutex_unavailable")
    assert executor.calls == []


def test_no_executor_configured_refuses_by_design(enabled_authority):
    auth, lock, _ = enabled_authority
    auth.executor = None  # never silently invent a caller
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == "no_executor_wired"
    # The mutex is never even acquired for a request that cannot merge —
    # refusal checks run before the global serialization point.
    assert lock.acquire_calls == []


def test_executor_failure_refuses_and_releases_lock(enabled_authority):
    auth, lock, executor = enabled_authority
    executor.success = False
    executor.error = "integration failed"
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == "executor_refused: integration failed"
    assert lock.exited == 1


def test_executor_exception_refuses_and_releases_lock(enabled_authority):
    auth, lock, executor = enabled_authority
    executor.blow_up = RuntimeError("boom")
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason.startswith("executor_error")
    assert lock.exited == 1


def test_real_swarmlock_mutex_serializes(tmp_path):
    pytest.importorskip("swarmlock")
    from swarmlock import AcquireRequest, SyncSwarmlock
    from swarmlock.backends.in_process import InProcessBackend

    class RealLockAdapter:
        def __init__(self, client):
            self.client = client

        def acquire(self, *, resource, holder, ttl_seconds):
            return self.client.lease(
                AcquireRequest(
                    resource=resource,
                    holder=holder,
                    ttl_seconds=ttl_seconds,
                    reentrant=False,
                ),
                heartbeat=False,
            )

    backend = InProcessBackend()
    contender = SyncSwarmlock(backend=backend)
    policy = _write_policy(tmp_path, enabled=True)

    # Another holder owns the mutex: the authority must refuse, not queue.
    with contender.lease(
        AcquireRequest(
            resource="prismatic/merge-mutex", holder="someone-else", ttl_seconds=60
        ),
        heartbeat=False,
    ):
        held = RealLockAdapter(SyncSwarmlock(backend=backend))
        executor = FakeExecutor()
        auth = MergeAuthority(
            policy_path=policy,
            lock_client=held,
            executor=executor,
            audit_log=tmp_path / "audit.jsonl",
            receipt_judge=_PassingReceiptJudge(),
        )
        decision = auth.request_merge(_green_input())
        assert decision.decision == "refused"
        # Pinned swarmlock lease() is lazy: contention raises at __enter__,
        # so the authority reports it via the executor-error path.
        assert decision.reason.startswith("executor_error")
        assert executor.calls == []

    # Mutex free: the same request is now allowed through the real mutex.
    free = RealLockAdapter(SyncSwarmlock(backend=backend))
    executor = FakeExecutor()
    auth = MergeAuthority(
        policy_path=policy,
        lock_client=free,
        executor=executor,
        audit_log=tmp_path / "audit2.jsonl",
        receipt_judge=_PassingReceiptJudge(),
    )
    decision = auth.request_merge(_green_input())
    assert decision.decision == "allowed"
    assert executor.calls == ["job-1"]


# ── the happy path (enabled) ─────────────────────────────────────────


def test_enabled_all_green_allows_and_audits(enabled_authority, tmp_path):
    auth, lock, executor = enabled_authority
    decision = auth.request_merge(_green_input())
    assert isinstance(decision, MergeDecision)
    assert decision.decision == "allowed"
    assert decision.allowed is True
    assert decision.merge_sha == "abc123"
    assert executor.calls == ["job-1"]
    assert lock.entered == 1
    assert lock.exited == 1  # mutex always released
    rows = _read_audit(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "allowed"
    assert len(row["gates"]) == 6  # 5 deterministic + ADR-0002 receipt gate
    assert all(g["passed"] for g in row["gates"])
    assert row["tier"] == 0
    assert row["merge_sha"] == "abc123"
    assert row["verification_receipt_id"] == "rcpt-1"


# ── ADR-0002 receipt validation ──────────────────────────────────────


def _enabled_with_judge(tmp_path, judge):
    policy = _write_policy(tmp_path, enabled=True)
    auth = MergeAuthority(
        policy_path=policy,
        lock_client=FakeLock(),
        executor=FakeExecutor(),
        audit_log=tmp_path / "audit.jsonl",
        receipt_judge=judge,
    )
    return auth


def test_missing_receipt_id_refuses_before_executor(tmp_path):
    lock = FakeLock()
    executor = FakeExecutor()
    policy = _write_policy(tmp_path, enabled=True)
    auth = MergeAuthority(
        policy_path=policy,
        lock_client=lock,
        executor=executor,
        audit_log=tmp_path / "audit.jsonl",
        receipt_judge=_RefusingReceiptJudge("receipt_missing"),
    )
    decision = auth.request_merge(_green_input(verification_receipt_id=""))
    assert decision.decision == "refused"
    assert decision.reason == "receipt_invalid:receipt_missing"
    assert executor.calls == []  # never authorized
    assert lock.acquire_calls == []  # mutex never touched


@pytest.mark.parametrize(
    "reason",
    [
        "receipt_missing",
        "receipt_not_found",
        "candidate_sha_mismatch",
        "producer_verifier_separation_failed",
        "freshness_failed: receipt_stale",
        "revocation_failed: receipt_revoked_by_id_rcpt-1",
        "decision_not_merge_eligible",
    ],
)
def test_receipt_judge_refusals_are_fail_closed(tmp_path, reason):
    auth = _enabled_with_judge(tmp_path, _RefusingReceiptJudge(reason))
    decision = auth.request_merge(_green_input())
    assert decision.decision == "refused"
    assert decision.reason == f"receipt_invalid:{reason}"
    assert decision.allowed is False


def test_judge_called_with_receipt_id_and_head_sha(
    enabled_authority, passing_judge
):
    auth, _, _ = enabled_authority
    auth.request_merge(_green_input())
    assert passing_judge.calls == [
        {
            "receipt_id": "rcpt-1",
            "expected_candidate_sha": "f" * 40,
            "expected_tree_sha": None,
        }
    ]


def test_receipt_refusal_is_audited_with_receipt_gate(tmp_path):
    auth = _enabled_with_judge(
        tmp_path, _RefusingReceiptJudge("receipt_not_found")
    )
    auth.request_merge(_green_input())
    rows = _read_audit(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "refused"
    assert row["reason"] == "receipt_invalid:receipt_not_found"
    assert row["verification_receipt_id"] == "rcpt-1"
    gates = {g["id"]: g for g in row["gates"]}
    assert gates["receipt_validated"]["passed"] is False


def test_disabled_never_builds_the_default_receipt_judge(disabled_authority):
    auth, _, _ = disabled_authority
    auth.request_merge(_green_input())
    # The lazy default judge opens the real receipt store (state-dir side
    # effects); the disabled path must never reach it.
    assert auth._receipt_judge is None


# ── Jev posture ──────────────────────────────────────────────────────


def test_jev_flag_is_dead_until_built(tmp_path):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["jev"] = {"enabled": True}
    path = tmp_path / "jevon.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    auth = MergeAuthority(
        policy_path=path,
        lock_client=FakeLock(),
        executor=FakeExecutor(),
        audit_log=tmp_path / "audit.jsonl",
        receipt_judge=_PassingReceiptJudge(),
    )
    decision = auth.request_merge(_green_input())
    # Jev enabled in config but not built: fail closed. And when Jev exists,
    # this branch is where the pause-button logic lives — it can only ever
    # refuse here, never allow.
    assert decision.decision == "refused"
    assert decision.reason == "jev_enabled_but_not_built"
    assert decision.jev_score is None


# ── plumbing ─────────────────────────────────────────────────────────


def test_audit_log_parent_dirs_are_created(tmp_path):
    deep = tmp_path / "a" / "b" / "audit.jsonl"
    auth = MergeAuthority(policy_path=SPEC_YAML, audit_log=deep)
    auth.request_merge(_green_input())
    assert deep.exists()


def test_shipped_policy_gates_match_shadow_gate_set():
    policy = load_auto_policy(SPEC_YAML)
    assert set(policy.gates) == {
        "ci_green_self_hosted",
        "ruff_clean",
        "verdict_not_reject",
        "no_merge_conflicts",
        "branch_protection_satisfied",
    }
    assert policy.verdicts_mergeable == frozenset({"CLEAN"})
