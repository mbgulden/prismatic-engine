"""Tests for earned-autonomy Phase 3: merge-path wiring.

Covers the lazy phase-1/2 boundary in ``merge_stage``, ``merge_executor``,
and ``learn_loop``:

- every consult / recording / proposal path stays green WITHOUT phases
  1/2 merged (fail-closed), and
- the same paths wire up correctly once stub phase-1/2 modules exist.

All phase-1/2 stubs live in this file (``monkeypatch.setitem`` on
``sys.modules``); the real phase-1/2 files are never imported here, so
these tests stay green on main with PRs #550/#551 unmerged.
"""

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import prismatic.review_factory as rf_pkg
from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review_factory import merge_executor
from prismatic.review_factory.learn_loop import LearnLoop, TierPromotionProposal
from prismatic.review_factory.merge_executor import (
    MergeExecutor,
    MergeResult,
    classify_change_class,
)
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

_AUTONOMY_MODULE = "prismatic.review_factory.autonomy"
_TRUST_MODULE = "prismatic.review_factory.trust"


# ── phase-1/2 stubs ──────────────────────────────────────────────────


class _FakeDecision:
    def __init__(self, allowed, reason):
        self.allowed = allowed
        self.reason = reason


class _FakeTrustLedger:
    """Recording fake standing in for the phase-1 TrustLedger.

    ``tier_status()`` returns the REAL contract shape (a dict with a
    ``"current_tier"`` key -- see ``trust.TrustLedger.tier_status``).
    An earlier version of this fake returned a bare int, which hid the
    R-2 swallowed-TypeError bug (``int(ledger.tier_status())``).
    """

    def __init__(self, tier=1, graduation=None):
        self.tier = tier
        self.graduation = graduation
        self.merge_outcomes = []
        self.rollbacks = []

    def tier_status(self):
        return {"current_tier": self.tier}

    def check_graduation(self):
        return self.graduation

    def record_merge_outcome(self, **kwargs):
        self.merge_outcomes.append(kwargs)

    def record_rollback(self, **kwargs):
        self.rollbacks.append(kwargs)


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _install_phase12(
    monkeypatch,
    *,
    autonomy_allowed=True,
    autonomy_reason="auto_merge_allowed",
    brake_engaged=False,
    tier=1,
    graduation=None,
):
    """Stub the phase-1 (trust) and phase-2 (autonomy) modules.

    Returns (autonomy_mod, trust_mod, ledger, seen_kwargs) where
    ``seen_kwargs`` captures the kwargs of the last ``can_auto_merge``
    call.
    """
    seen = {}

    def fake_can_auto_merge(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return _FakeDecision(autonomy_allowed, autonomy_reason)

    autonomy_mod = _stub_module(
        _AUTONOMY_MODULE,
        brake_status=lambda: {"engaged": brake_engaged},
        can_auto_merge=fake_can_auto_merge,
    )
    ledger = _FakeTrustLedger(tier=tier, graduation=graduation)
    trust_mod = _stub_module(
        _TRUST_MODULE,
        TrustLedger=lambda *args, **kwargs: ledger,
    )
    for name, mod in ((_AUTONOMY_MODULE, autonomy_mod), (_TRUST_MODULE, trust_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(rf_pkg, name.rsplit(".", 1)[1], mod, raising=False)
    return autonomy_mod, trust_mod, ledger, seen


def _remove_phase12(monkeypatch):
    """Ensure the phase-1/2 modules are absent (fail-closed posture)."""
    for name in (_AUTONOMY_MODULE, _TRUST_MODULE):
        monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.delattr(rf_pkg, name.rsplit(".", 1)[1], raising=False)


# ── merge-stage fakes ────────────────────────────────────────────────


class _FakeStageDB:
    def __init__(self):
        self.audit_rows = []

    def insert_audit_entry(self, *, actor, action, review_job_id, details):
        self.audit_rows.append(
            {
                "actor": actor,
                "action": action,
                "review_job_id": review_job_id,
                "details": details,
            }
        )

    def find_audit_entry(self, job_id, action):
        return None

    def update_review_job_state(self, job_id, state):
        return True

    def consume_authorization(self, auth_id):
        return None


class _FakeStageQueue:
    def __init__(self):
        self.db = _FakeStageDB()
        self.authorize_calls = []

    def authorize_merge(self, job_id, actor=None):
        self.authorize_calls.append((job_id, actor))
        return "auth-1"


class _FakeStageExecutor:
    """Stands in for MergeExecutor in the consult tests (dry-run)."""

    def __init__(self, queue=None, dry_run=False, repo_path=None):
        self.calls = []

    def execute(self, job_id, manifest=None):
        self.calls.append(job_id)
        return SimpleNamespace(success=True, merge_sha="dry-sha", error="")


def _stage_job(**overrides):
    job = SimpleNamespace(
        review_job_id="job-1",
        risk_tier=0,
        change_class="docs",
        deterministic_verdict="CLEAN",
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _enabled_dry_run_stage(queue):
    cfg = MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({0}))
    return MergeStage(queue, cfg, novelty_detector=None)


def _isolated_stage(monkeypatch, queue):
    """MergeStage with the L2 judgment layer pinned off and a fake executor."""
    monkeypatch.setattr(
        MergeStage,
        "_judgment_holds_for_human",
        lambda self, job, job_id, tier: False,
    )
    monkeypatch.setattr(merge_executor, "MergeExecutor", _FakeStageExecutor)
    return _enabled_dry_run_stage(queue)


# ── classify_change_class ────────────────────────────────────────────


def test_classify_change_class_passthrough():
    assert classify_change_class(SimpleNamespace(change_class="docs")) == "docs"


def test_classify_change_class_missing_attr_is_sensitive():
    assert classify_change_class(SimpleNamespace()) == "sensitive"


@pytest.mark.parametrize("value", [None, "", "   ", 123, ["docs"]])
def test_classify_change_class_fail_closed(value):
    job = SimpleNamespace()
    job.change_class = value
    assert classify_change_class(job) == "sensitive"


# ── merge-stage autonomy consult ─────────────────────────────────────


def test_consult_allowed_proceeds_to_authorize(monkeypatch):
    _install_phase12(monkeypatch, autonomy_allowed=True)
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    result = stage.process(_stage_job())
    assert result.action == "dry_run_ok"
    assert queue.authorize_calls == [("job-1", "standing-policy: tier-0")]


def test_consult_passes_tier_change_class_and_verdict(monkeypatch):
    _, _, _, seen = _install_phase12(monkeypatch, autonomy_allowed=True)
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    stage.process(_stage_job())
    assert seen["tier"] == 1
    assert seen["change_class"] == "docs"
    assert seen["deterministic_verdict"] == "CLEAN"
    assert seen["brake_engaged"] is False


def test_consult_refused_withholds_authorization(monkeypatch):
    _install_phase12(
        monkeypatch, autonomy_allowed=False, autonomy_reason="class_not_in_tier"
    )
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    result = stage.process(_stage_job())
    assert result.action == "refused_autonomy"
    assert result.error == "class_not_in_tier"
    assert queue.authorize_calls == []
    assert any(
        row["action"] == "auto_merge_refused_autonomy" for row in queue.db.audit_rows
    )


def test_consult_absent_modules_fail_closed(monkeypatch):
    _remove_phase12(monkeypatch)
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    result = stage.process(_stage_job())
    assert result.action == "refused_autonomy"
    assert result.error == "autonomy_module_absent"
    assert queue.authorize_calls == []


def test_consult_trust_failure_degrades_to_tier_zero_refusal(monkeypatch):
    """A broken trust ledger reads as tier 0, which never auto-merges."""
    import prismatic.review_factory as pkg

    class BrokenLedger:
        def tier_status(self):
            raise RuntimeError("ledger down")

    trust_mod = _stub_module(_TRUST_MODULE, TrustLedger=BrokenLedger)
    autonomy_mod = _stub_module(
        _AUTONOMY_MODULE,
        brake_status=lambda: {"engaged": False},
        # tier 0 is PRs-only: the consult must refuse.
        can_auto_merge=lambda **kwargs: _FakeDecision(
            kwargs["tier"] != 0, "t0_no_auto_merge" if kwargs["tier"] == 0 else "ok"
        ),
    )
    for name, mod in ((_AUTONOMY_MODULE, autonomy_mod), (_TRUST_MODULE, trust_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(pkg, name.rsplit(".", 1)[1], mod, raising=False)
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    result = stage.process(_stage_job())
    assert result.action == "refused_autonomy"
    assert queue.authorize_calls == []


def test_consult_brake_engaged_refuses(monkeypatch):
    _install_phase12(
        monkeypatch,
        autonomy_allowed=False,
        autonomy_reason="brake_engaged",
        brake_engaged=True,
    )
    queue = _FakeStageQueue()
    stage = _isolated_stage(monkeypatch, queue)
    result = stage.process(_stage_job())
    assert result.action == "refused_autonomy"
    assert result.error == "brake_engaged"
    assert queue.authorize_calls == []


# ── merge-executor trust recording ───────────────────────────────────


def _exec_job(**overrides):
    job = SimpleNamespace(
        review_job_id="job-1",
        repository="mbgulden/prismatic-engine",
        base_commit="b" * 40,
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
        result_packet_path=None,
        change_class="docs",
        task_id="GRO-TEST-MERGE",
        risk_tier=0,
        changed_paths_json='["docs/readme.md"]',
        policy_version="v1",
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _exec_auth(**overrides):
    auth = SimpleNamespace(
        is_expired=False,
        is_consumed=False,
        repository="mbgulden/prismatic-engine",
        pr_head_commit="c" * 40,
        pr_base_commit="b" * 40,
        candidate_tree="c" * 40,
        expected_merge_tree=None,
        actor="standing-policy: tier-0",
        scope="tier-0-auto",
        authorization_id="auth-1",
    )
    for key, value in overrides.items():
        setattr(auth, key, value)
    return auth


class _FakeExecDB:
    def __init__(self, job, auth):
        self.job = job
        self.auth = auth
        self.states = []

    def get_review_job(self, job_id):
        return self.job

    def get_authorization_for_job(self, job_id):
        return self.auth

    def claim_authorization_for_merge(self, *args, **kwargs):
        return SimpleNamespace(
            actor="standing-policy: tier-0",
            scope="tier-0-auto",
            authorization_id="auth-1",
        )

    def update_review_job_state(self, job_id, state):
        self.states.append(state)
        return True


def _succeeding_executor(monkeypatch, job, auth, **kwargs):
    queue = SimpleNamespace(db=_FakeExecDB(job, auth))
    executor = MergeExecutor(queue=queue, dry_run=False, mf_store=MagicMock(), **kwargs)

    def fake_execute_merge(job, manifest, auth):
        return MergeResult(job_id=job.review_job_id, success=True, merge_sha="abc123")

    monkeypatch.setattr(executor, "_execute_merge", fake_execute_merge)
    return executor


def test_execute_success_records_trust_outcome(monkeypatch):
    _, _, ledger, _ = _install_phase12(monkeypatch)
    executor = _succeeding_executor(monkeypatch, _exec_job(), _exec_auth())
    result = executor.execute("job-1", manifest=SimpleNamespace())
    assert result.success
    assert result.merge_sha == "abc123"
    assert len(ledger.merge_outcomes) == 1
    row = ledger.merge_outcomes[0]
    assert row["artifact_id"] == "job-1"
    assert row["change_class"] == "docs"
    assert row["merged_by"] == "auto"
    assert row["deterministic_verdict"] == "CLEAN"
    assert row["notes"] == "merge_sha=abc123"


def test_execute_success_without_trust_module_still_succeeds(monkeypatch):
    _remove_phase12(monkeypatch)
    executor = _succeeding_executor(monkeypatch, _exec_job(), _exec_auth())
    result = executor.execute("job-1", manifest=SimpleNamespace())
    assert result.success
    assert result.merge_sha == "abc123"


def test_execute_uses_injected_trust_ledger(monkeypatch):
    autonomy_mod, trust_mod, module_ledger, _ = _install_phase12(monkeypatch)
    injected = _FakeTrustLedger()
    executor = _succeeding_executor(
        monkeypatch, _exec_job(), _exec_auth(), trust_ledger=injected
    )
    result = executor.execute("job-1", manifest=SimpleNamespace())
    assert result.success
    assert len(injected.merge_outcomes) == 1
    assert injected.merge_outcomes[0]["artifact_id"] == "job-1"
    assert module_ledger.merge_outcomes == []


def test_execute_failure_records_no_trust_outcome(monkeypatch):
    """The success+sha guard: a failed merge must not record an outcome."""
    _, _, ledger, _ = _install_phase12(monkeypatch)
    executor = _succeeding_executor(monkeypatch, _exec_job(), _exec_auth())

    def fake_execute_merge(job, manifest, auth):
        return MergeResult(job_id=job.review_job_id, success=False, error="boom")

    monkeypatch.setattr(executor, "_execute_merge", fake_execute_merge)
    result = executor.execute("job-1", manifest=SimpleNamespace())
    assert not result.success
    assert ledger.merge_outcomes == []


def test_execute_unknown_change_class_records_sensitive(monkeypatch):
    _, _, ledger, _ = _install_phase12(monkeypatch)
    executor = _succeeding_executor(monkeypatch, _exec_job(), _exec_auth())
    executor.queue.db.job = _exec_job(change_class=None)
    result = executor.execute("job-1", manifest=SimpleNamespace())
    assert result.success
    assert ledger.merge_outcomes[0]["change_class"] == "sensitive"


# ── rollback path drives the real _execute_merge ─────────────────────


def _merge_ready_manifest():
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-TEST-MERGE",
        task_id="GRO-TEST-MERGE",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="b" * 40,
        candidate_sha="c" * 40,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/test-merge",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )
    evidence = [
        VerificationEvidence(
            proof_class="focused",
            command="pytest tests/ -x -q",
            summary="5 tests passed",
            result="PASS",
            log_path="/tmp/focused.log",
            log_sha256="f" * 64,
        )
    ]
    manifest = manifest.request_review(evidence)
    review = IndependentReview(
        reviewer="rf-test",
        review_id="review-test-1",
        verdict="CLEAN",
        reviewed_sha="c" * 40,
        reviewed_manifest_digest=manifest._digest_without_review(),
        scope_clean=True,
        conflict_free=True,
    )
    manifest = manifest.record_review(review)
    ci_checks = [
        CICheck(
            name="rf-v1-verification",
            run_id=1000,
            conclusion="SUCCESS",
            head_sha="c" * 40,
            details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
        )
    ]
    manifest = manifest.record_ci(ci_checks)
    return manifest.mark_merge_eligible()


def test_execute_rollback_records_trust_rollback(monkeypatch, tmp_path):
    """Drive the real rollback path: a result-tree mismatch after the merge
    commit is created forces the CAS rollback branch, which must record a
    trust-ledger rollback (best-effort, never breaking the result)."""
    from prismatic.core.merge_factory import MergeFactoryStore

    _, _, ledger, _ = _install_phase12(monkeypatch)
    # Silence the rollback path's other side effects; the trust record is
    # the only one under test here.
    monkeypatch.setattr(
        merge_executor, "_record_learn_loop_rollback_outcome", lambda job_id: None
    )
    monkeypatch.setattr(
        "prismatic.review_factory.metrics_feed.record_rollback",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        merge_executor,
        "integrate_pipeline_run",
        lambda **kwargs: SimpleNamespace(
            merge_sha="m" * 40,
            error_message="",
            test_results={},
            is_success=lambda: True,
        ),
    )

    job = _exec_job()
    auth = _exec_auth(expected_merge_tree="c" * 40)
    queue = SimpleNamespace(db=_FakeExecDB(job, auth))
    executor = MergeExecutor(
        queue=queue,
        dry_run=False,
        repo_path=tmp_path / "repo",
        mf_store=MergeFactoryStore(db_path=tmp_path / "mf.db"),
    )
    # CI checks are pre-green on the manifest (MERGE_ELIGIBLE); the build
    # step is irrelevant to the rollback wiring.
    monkeypatch.setattr(executor, "_build_ci_checks", lambda job, manifest: ())

    plain_calls = {"n": 0}

    def fake_rev_parse(ref):
        if ref.endswith("^{tree}"):
            return "other-tree"  # != expected_merge_tree -> PermissionError
        plain_calls["n"] += 1
        if plain_calls["n"] <= 2:
            return "b" * 40  # target head before the merge, and under lock
        return "m" * 40  # current head after the failed merge

    monkeypatch.setattr(executor, "_git_rev_parse", fake_rev_parse)
    monkeypatch.setattr(executor, "_is_exact_merge_commit", lambda **kwargs: True)
    monkeypatch.setattr(executor, "_rollback_target", lambda **kwargs: None)

    result = executor.execute("job-1", manifest=_merge_ready_manifest())

    assert not result.success
    assert "tree" in result.error.lower()
    assert len(ledger.rollbacks) == 1
    row = ledger.rollbacks[0]
    assert row["artifact_id"] == "job-1"
    assert row["change_class"] == "docs"
    assert row["auto_merged"] is True
    assert row["notes"] == "executor rollback"
    assert ledger.merge_outcomes == []


def test_execute_rollback_without_trust_module_still_reports_failure(
    monkeypatch, tmp_path
):
    """The rollback result must not depend on the trust module existing."""
    _remove_phase12(monkeypatch)
    from prismatic.core.merge_factory import MergeFactoryStore

    monkeypatch.setattr(
        merge_executor, "_record_learn_loop_rollback_outcome", lambda job_id: None
    )
    monkeypatch.setattr(
        "prismatic.review_factory.metrics_feed.record_rollback",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        merge_executor,
        "integrate_pipeline_run",
        lambda **kwargs: SimpleNamespace(
            merge_sha="m" * 40,
            error_message="",
            test_results={},
            is_success=lambda: True,
        ),
    )
    job = _exec_job()
    auth = _exec_auth(expected_merge_tree="c" * 40)
    queue = SimpleNamespace(db=_FakeExecDB(job, auth))
    executor = MergeExecutor(
        queue=queue,
        dry_run=False,
        repo_path=tmp_path / "repo",
        mf_store=MergeFactoryStore(db_path=tmp_path / "mf.db"),
    )
    monkeypatch.setattr(executor, "_build_ci_checks", lambda job, manifest: ())
    plain_calls = {"n": 0}

    def fake_rev_parse(ref):
        if ref.endswith("^{tree}"):
            return "other-tree"
        plain_calls["n"] += 1
        if plain_calls["n"] <= 2:
            return "b" * 40
        return "m" * 40

    monkeypatch.setattr(executor, "_git_rev_parse", fake_rev_parse)
    monkeypatch.setattr(executor, "_is_exact_merge_commit", lambda **kwargs: True)
    monkeypatch.setattr(executor, "_rollback_target", lambda **kwargs: None)

    result = executor.execute("job-1", manifest=_merge_ready_manifest())
    assert not result.success
    assert "tree" in result.error.lower()


# ── learn-loop tier promotion proposals ──────────────────────────────


def _learn_loop(tmp_path):
    return LearnLoop(
        policy_path=tmp_path / "no-policy.yaml",  # missing -> disabled
        bands_path=tmp_path / "no-bands.yaml",  # missing -> inert placeholder
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "audit.jsonl",
    )


def _graduation_dict():
    return {
        "from_tier": 1,
        "to_tier": 2,
        "evidence": {"clean_merges": 25, "rollbacks": 0},
        "rationale": "25 clean merges, zero rollbacks",
        "proposed_spec_text": "version: tier-spec-v2\n",
    }


def test_review_tier_promotions_proposes(monkeypatch, tmp_path):
    _, _, _, _ = _install_phase12(monkeypatch, graduation=_graduation_dict())
    loop = _learn_loop(tmp_path)
    assert loop.policy.enabled is False  # propose-only works while disabled
    proposals = loop.review_tier_promotions()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert isinstance(proposal, TierPromotionProposal)
    assert (proposal.from_tier, proposal.to_tier) == (1, 2)
    assert proposal.requires_michael is True
    assert proposal.evidence == {"clean_merges": 25, "rollbacks": 0}
    assert proposal.rationale == "25 clean merges, zero rollbacks"
    assert proposal.proposed_spec_text == "version: tier-spec-v2\n"


def test_review_tier_promotions_prefers_injected_ledger(monkeypatch, tmp_path):
    def _boom(*args, **kwargs):
        raise AssertionError("module ledger must not be constructed")

    trust_mod = _stub_module(_TRUST_MODULE, TrustLedger=_boom)
    monkeypatch.setitem(sys.modules, _TRUST_MODULE, trust_mod)
    monkeypatch.setattr(rf_pkg, "trust", trust_mod, raising=False)
    injected = _FakeTrustLedger(graduation={"from_tier": 0, "to_tier": 1})
    proposals = _learn_loop(tmp_path).review_tier_promotions(ledger=injected)
    assert len(proposals) == 1
    assert (proposals[0].from_tier, proposals[0].to_tier) == (0, 1)


def test_review_tier_promotions_uses_injected_ledger_when_trust_absent(
    monkeypatch, tmp_path
):
    """An injected ledger must work even with the phase-1 module absent —
    the trust import happens only when no ledger is supplied."""
    _remove_phase12(monkeypatch)
    injected = _FakeTrustLedger(graduation={"from_tier": 2, "to_tier": 3})
    proposals = _learn_loop(tmp_path).review_tier_promotions(ledger=injected)
    assert len(proposals) == 1
    assert (proposals[0].from_tier, proposals[0].to_tier) == (2, 3)


def test_review_tier_promotions_empty_when_no_graduation(monkeypatch, tmp_path):
    _install_phase12(monkeypatch, graduation=None)
    assert _learn_loop(tmp_path).review_tier_promotions() == ()


def test_review_tier_promotions_empty_when_trust_absent(monkeypatch, tmp_path):
    _remove_phase12(monkeypatch)
    assert _learn_loop(tmp_path).review_tier_promotions() == ()


def test_review_tier_promotions_empty_when_ledger_raises(monkeypatch, tmp_path):
    class BadLedger:
        def check_graduation(self):
            raise RuntimeError("ledger down")

    trust_mod = _stub_module(_TRUST_MODULE, TrustLedger=BadLedger)
    monkeypatch.setitem(sys.modules, _TRUST_MODULE, trust_mod)
    monkeypatch.setattr(rf_pkg, "trust", trust_mod, raising=False)
    assert _learn_loop(tmp_path).review_tier_promotions() == ()


def test_review_tier_promotions_writes_nothing(monkeypatch, tmp_path):
    """Propose-only: no files written — not even in tmp, and the real
    default log paths are byte-identical before and after."""
    _install_phase12(monkeypatch, graduation=_graduation_dict())
    real_paths = [
        Path(os.path.expanduser("~/.prismatic/audit/learn-loop-decisions.jsonl")),
        Path(os.path.expanduser("~/.prismatic/audit/learn-outcomes.jsonl")),
        Path(os.path.expanduser("~/.prismatic/audit/auto-merge-decisions.jsonl")),
    ]
    before = {str(p): (p.read_bytes() if p.exists() else None) for p in real_paths}
    loop = _learn_loop(tmp_path)
    proposals = loop.review_tier_promotions()
    assert len(proposals) == 1
    for path in real_paths:
        current = path.read_bytes() if path.exists() else None
        assert current == before[str(path)], f"real path touched: {path}"
    assert list(tmp_path.iterdir()) == []
