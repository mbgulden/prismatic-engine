"""T1 auto-merge path repairs: evidence-based classification, novelty
wiring, ledger idempotency, promotion-freeze enforcement.

Covers the gaps found in the 2026-09-28 trust-ledger/autonomy stress test:
- classify_change_class derived from changed paths (was dead: read a
  nonexistent job.change_class attribute, so T1 could never allow).
- novelty screen trips flow into the autonomy consult (was unreachable).
- ledger outcome events are idempotent on (event_type, artifact_id).
- tier promotion is blocked during an active promotion freeze unless
  explicitly overridden.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismatic.merge_candidate_manifest import PromotionState
from prismatic.review_factory import autonomy
from prismatic.review_factory import merge_executor as me_mod
from prismatic.review_factory.autonomy import can_auto_merge
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.merge_executor import (
    MergeExecutor,
    MergeReceiptMissingError,
    classify_change_class,
)
from prismatic.review_factory.models import ReviewJobState
from prismatic.review_factory.trust import TrustLedger


def _job(paths):
    return SimpleNamespace(
        review_job_id="job-1",
        changed_paths_json=__import__("json").dumps(paths),
        deterministic_verdict="CLEAN",
        risk_tier=1,
    )


def _ledger(tmp_path: Path) -> TrustLedger:
    db = ReviewFactoryDB(db_path=tmp_path / "t.db")
    return TrustLedger(db=db, audit_dir=tmp_path / "audit")


# ── classify_change_class: evidence-based, fail-closed ─────────────────


def test_classifier_docs_paths():
    assert classify_change_class(_job(["docs/guide.md"])) == "docs"
    assert classify_change_class(_job(["journals/muse/inbox/2026-09-28.md"])) == "docs"
    assert classify_change_class(_job(["README.md", "docs/a.rst"])) == "docs"


def test_classifier_dep_bump_lockfiles():
    assert classify_change_class(_job(["poetry.lock"])) == "dep_bump"
    assert classify_change_class(_job(["requirements.txt"])) == "dep_bump"
    assert classify_change_class(_job(["frontend/package-lock.json"])) == "dep_bump"


def test_classifier_chore_ci_paths():
    assert classify_change_class(_job([".github/workflows/test.yml"])) == "chore"


def test_classifier_sensitive_paths_win():
    # Sensitive hints beat tier classes even when mixed with docs.
    assert classify_change_class(_job(["prismatic/gateway/auth.py"])) == "sensitive"
    assert classify_change_class(_job(["docs/guide.md", "config/secrets.yaml"])) == "sensitive"
    assert classify_change_class(_job(["certs/api.pem"])) == "sensitive"
    assert classify_change_class(_job([".env"])) == "sensitive"
    assert classify_change_class(_job(["prismatic/billing/stripe.py"])) == "sensitive"


def test_classifier_production_paths():
    assert classify_change_class(_job(["pe/deploy/receiver.py"])) == "production"


def test_classifier_mixed_and_empty_fail_closed():
    assert classify_change_class(_job(["docs/a.md", "prismatic/x.py"])) == "sensitive"
    assert classify_change_class(_job([])) == "sensitive"
    assert classify_change_class(SimpleNamespace()) == "sensitive"
    # A build manifest that can change behavior is not a dep bump.
    assert classify_change_class(_job(["pyproject.toml"])) == "sensitive"
    assert classify_change_class(_job(["package.json"])) == "sensitive"


def test_classifier_ignores_self_attested_change_class():
    # A caller-supplied change_class attribute must NOT be trusted: the
    # class is derived from changed paths, never self-attestation.
    job = _job(["prismatic/gateway/auth.py"])
    job.change_class = "docs"
    assert classify_change_class(job) == "sensitive"


def test_classifier_accepts_changed_paths_list():
    job = SimpleNamespace(changed_paths=["docs/a.md"])
    assert classify_change_class(job) == "docs"


# ── can_auto_merge: T1 docs now reachable; novelty enforced ────────────


def test_t1_docs_pr_allowed_end_to_end():
    decision = can_auto_merge(
        tier=1,
        change_class=classify_change_class(_job(["docs/guide.md"])),
        deterministic_verdict="CLEAN",
        judgment=None,
        brake_engaged=False,
        novelty_flags=(),
        novelty_inert=True,  # shipped novelty policy is disabled
    )
    assert decision.allowed is True
    assert decision.reason == "auto_merge_allowed"
    assert "novelty_inert" in decision.notes
    assert "judgment_skipped" in decision.notes


def test_novelty_flags_refuse_when_tripped():
    decision = can_auto_merge(
        tier=1,
        change_class="docs",
        deterministic_verdict="CLEAN",
        brake_engaged=False,
        novelty_flags=("novel_input",),
    )
    assert decision.allowed is False
    assert decision.reason == "novelty_flagged"


def test_sensitive_never_auto_merges_at_t1():
    decision = can_auto_merge(
        tier=1,
        change_class=classify_change_class(_job(["prismatic/gateway/auth.py"])),
        deterministic_verdict="CLEAN",
        brake_engaged=False,
    )
    assert decision.allowed is False
    assert decision.reason == "class_not_in_tier"


# ── merge-stage consult: novelty flags actually arrive ─────────────────


def test_consult_passes_novelty_flags(monkeypatch, tmp_path):
    from prismatic.review_factory import merge_stage as ms_mod
    from prismatic.review_factory import trust as trust_mod

    seen = {}

    class FakeLedger:
        def tier_status(self):
            return {"current_tier": 1}

    monkeypatch.setattr(trust_mod, "TrustLedger", lambda *a, **k: FakeLedger())

    def fake_can_auto_merge(**kwargs):
        seen.update(kwargs)
        return autonomy.AutonomyDecision(
            allowed=False, reason="test", tier=1, change_class="docs"
        )

    monkeypatch.setattr(autonomy, "can_auto_merge", fake_can_auto_merge)

    stage = ms_mod.MergeStage.__new__(ms_mod.MergeStage)
    stage._novelty_detector = None  # not used; consult is direct
    allowed, reason = ms_mod.MergeStage._autonomy_consult(
        stage,
        _job(["docs/guide.md"]),
        novelty_flags=("novel_input",),
        novelty_inert=False,
    )
    assert allowed is False and reason == "test"
    assert seen["novelty_flags"] == ("novel_input",)
    assert seen["novelty_inert"] is False
    assert seen["change_class"] == "docs"


def test_novelty_screen_returns_flags_and_inert(tmp_path):
    from prismatic.review_factory import merge_stage as ms_mod
    from prismatic.review_factory.merge_stage import MergeStageConfig

    stage = ms_mod.MergeStage(queue=object(), config=MergeStageConfig(),
                              novelty_detector=None)
    flags, inert = stage._novelty_screen(_job(["docs/a.md"]), "job-1")
    assert flags == ()
    assert inert is True


# ── trust ledger: idempotency ──────────────────────────────────────────


def test_merge_outcome_idempotent_on_artifact(tmp_path):
    ledger = _ledger(tmp_path)
    first = ledger.record_merge_outcome(
        artifact_id="job-1", change_class="docs", merged_by="auto"
    )
    second = ledger.record_merge_outcome(
        artifact_id="job-1", change_class="docs", merged_by="auto"
    )
    assert second["event_id"] == first["event_id"]
    assert second["duplicate_skipped"] is True
    rows = [e for e in ledger.events() if e["event_type"] == "merge_completed"]
    assert len(rows) == 1


def test_rollback_idempotent_single_revoke(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_tier_promoted(to_tier=1, approver="Michael",
                                override_freeze=True)
    ledger.record_rollback(artifact_id="job-9", change_class="docs",
                           notes="executor rollback")
    # A duplicate rollback record must not revoke the tier twice.
    ledger.record_rollback(artifact_id="job-9", change_class="docs",
                           notes="executor rollback")
    revokes = [e for e in ledger.events() if e["event_type"] == "tier_revoked"]
    assert len(revokes) == 1
    assert ledger.tier_status()["current_tier"] == 0


# ── trust ledger: promotion freeze enforced ───────────────────────────


def test_promotion_blocked_during_freeze(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_rollback(artifact_id="job-1", change_class="docs",
                           notes="x")
    assert ledger.tier_status()["promotion_freeze_until"] is not None
    with pytest.raises(ValueError, match="promotion freeze"):
        ledger.record_tier_promoted(to_tier=1, approver="Michael")


def test_promotion_override_freeze_records_override(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_rollback(artifact_id="job-1", change_class="docs",
                           notes="x")
    event = ledger.record_tier_promoted(
        to_tier=1, approver="Michael", override_freeze=True
    )
    assert event["judgment"]["override_freeze"] is True
    assert "freeze overridden" in event["notes"]
    assert ledger.tier_status()["current_tier"] == 1


BASE = "b" * 40
CAND = "c" * 40
MERGE_SHA = "m" * 40


def _rjob():
    return SimpleNamespace(
        review_job_id="job-receipt-1",
        task_id="task-1",
        repository="proof/repo",
        base_commit=BASE,
        candidate_commit=CAND,
        candidate_tree=CAND,
        changed_paths_json=json.dumps(["docs/guide.md"]),
        risk_tier=0,
        policy_version="v1",
        result_packet_path="",
    )


class _FakeManifest:
    def __init__(self, job):
        self.state = PromotionState.CLEAN
        self.issue_id = job.task_id
        self.task_id = job.task_id
        self.repository = job.repository
        self.base_sha = job.base_commit
        self.candidate_sha = job.candidate_commit
        self.changed_paths = tuple(sorted(json.loads(job.changed_paths_json or "[]")))
        self.risk_tier = SimpleNamespace(value="A")
        self.proof_policy_version = 1
        self.target = "main"

    def record_ci(self, checks):
        assert self.state == PromotionState.CLEAN
        self.state = PromotionState.CI_GREEN
        return self

    def mark_merge_eligible(self):
        assert self.state == PromotionState.CI_GREEN
        self.state = PromotionState.MERGE_ELIGIBLE
        return self

    def mark_merged(self, **kwargs):
        return self

    def digest(self):
        return "digest-1"


def _rauth():
    return SimpleNamespace(
        authorization_id="auth-1",
        actor="standing-policy: tier-1",
        scope="tier-1-auto",
        repository="proof/repo",
        pr_head_commit=CAND,
        pr_base_commit=BASE,
        candidate_tree=CAND,
        expected_merge_tree=CAND,
        is_expired=False,
        is_consumed=False,
    )


class _FakeLedger:
    def __init__(self):
        self.calls = []

    def record_rollback(self, **kwargs):
        self.calls.append(("rollback", kwargs))

    def record_merge_outcome(self, **kwargs):
        self.calls.append(("merge_outcome", kwargs))

    def record_merge_receipt_missing(self, **kwargs):
        self.calls.append(("receipt_missing", kwargs))


class _FakeDB:
    def __init__(self, job, auth):
        self.job = job
        self.auth = auth
        self.state_calls = []
        self.claim_calls = []

    def get_review_job(self, job_id):
        return self.job

    def get_authorization_for_job(self, job_id):
        return self.auth

    def claim_authorization_for_merge(self, authorization_id, **kwargs):
        self.claim_calls.append(authorization_id)
        return self.auth

    def update_review_job_state(self, job_id, state):
        self.state_calls.append(state)
        return True


def _executor(monkeypatch, tmp_path, db, ledger, rev_parse_script, receipt_id=""):
    mf = SimpleNamespace(
        submit_attestation=lambda **k: {"attestation_id": "att-1"},
        acquire_lock=lambda **k: {"acquisition_token": "tok"},
        release_lock=lambda **k: None,
    )
    ex = MergeExecutor(
        queue=SimpleNamespace(db=db),
        dry_run=False,
        repo_path=tmp_path,
        mf_store=mf,
        trust_ledger=ledger,
        merge_receipts_path=tmp_path / "receipts.jsonl",
    )
    monkeypatch.setattr(ex, "_build_ci_checks", lambda job, manifest: ())
    calls = {"n": 0}

    def _rev_parse(ref):
        calls["n"] += 1
        return rev_parse_script(calls["n"], ref)

    monkeypatch.setattr(ex, "_git_rev_parse", _rev_parse)
    monkeypatch.setattr(ex, "_emit_merge_receipt", lambda *a, **k: receipt_id)
    monkeypatch.setattr(
        me_mod,
        "integrate_pipeline_run",
        lambda **k: SimpleNamespace(
            merge_sha=MERGE_SHA,
            is_success=lambda: True,
            test_results={},
            error_message="",
        ),
    )
    monkeypatch.setattr(me_mod, "_record_learn_loop_rollback_outcome", lambda jid: None)
    return ex


def _ok_script(n, ref):
    # 1: target_head_before, 2: locked_target_head -> base;
    # 3: merge tree -> expected; 4+: current head -> our merge commit.
    if ref == f"{MERGE_SHA}^{{tree}}":
        return CAND
    if n >= 4:
        return MERGE_SHA
    return BASE


def test_record_merge_receipt_missing_idempotent_and_not_a_clean_merge(tmp_path):
    """The receipt-missing event is visible but never counts as a merge."""
    ledger = _ledger(tmp_path)
    first = ledger.record_merge_receipt_missing(
        artifact_id="job-7", change_class="docs", merge_sha="m" * 40,
        notes="rollback refused",
    )
    second = ledger.record_merge_receipt_missing(
        artifact_id="job-7", change_class="docs", merge_sha="m" * 40,
        notes="rollback refused",
    )
    assert second["event_id"] == first["event_id"]
    assert second["duplicate_skipped"] is True
    assert first["event_type"] == "merge_receipt_missing"
    assert "merge_sha=" + "m" * 40 in first["notes"]
    # Never counted as a clean merge: graduation streaks only read
    # merge_completed events (see check_graduation).
    evts = ledger.events()
    assert [e for e in evts if e["event_type"] == "merge_completed"] == []
    assert any(e["event_type"] == "merge_receipt_missing" for e in evts)


def test_missing_receipt_raises_before_merged_and_rolls_back(monkeypatch, tmp_path):
    """Empty receipt id -> MergeReceiptMissingError before MERGED is persisted.

    The CAS guard sees exactly our merge commit, rolls it back, and the
    trust ledger records the rollback. The job is never marked MERGED.
    """
    job, auth, ledger = _rjob(), _rauth(), _FakeLedger()
    db = _FakeDB(job, auth)
    rolled_back = []
    ex = _executor(monkeypatch, tmp_path, db, ledger, _ok_script, receipt_id="")
    monkeypatch.setattr(ex, "_is_exact_merge_commit", lambda **k: True)
    monkeypatch.setattr(
        ex, "_rollback_target", lambda **k: rolled_back.append(k)
    )

    with pytest.raises(MergeReceiptMissingError, match="no signed merge receipt"):
        ex._execute_merge(job, _FakeManifest(job), auth)

    assert rolled_back, "CAS rollback must run for our own merge commit"
    kinds = [c[0] for c in ledger.calls]
    assert kinds == ["rollback"]
    assert ReviewJobState.MERGED not in db.state_calls
    assert ReviewJobState.MERGE_VERIFICATION_FAILED in db.state_calls


def test_missing_receipt_with_refused_rollback_records_ledger(monkeypatch, tmp_path):
    """Rollback refused (branch moved) -> merge stays live, ledger shows it.

    The receipt-missing merge is recorded as its own event type: visible
    in the ledger, never counted as a clean merge.
    """
    job, auth, ledger = _rjob(), _rauth(), _FakeLedger()
    db = _FakeDB(job, auth)

    def _moved_script(n, ref):
        if ref == f"{MERGE_SHA}^{{tree}}":
            return CAND
        if n >= 4:
            return "z" * 40  # someone else moved the branch: CAS refuses
        return BASE

    ex = _executor(monkeypatch, tmp_path, db, ledger, _moved_script, receipt_id="")
    monkeypatch.setattr(ex, "_is_exact_merge_commit", lambda **k: False)

    with pytest.raises(RuntimeError, match="rollback was refused"):
        ex._execute_merge(job, _FakeManifest(job), auth)

    kinds = [c[0] for c in ledger.calls]
    assert kinds == ["receipt_missing"]
    _, kwargs = ledger.calls[0]
    assert kwargs["artifact_id"] == job.review_job_id
    assert kwargs["merge_sha"] == MERGE_SHA
    assert "merge_outcome" not in kinds
    assert ReviewJobState.MERGED not in db.state_calls


def test_execute_reports_failure_when_receipt_missing(monkeypatch, tmp_path):
    """execute() surfaces the receipt postcondition as a failed result."""
    job, auth, ledger = _rjob(), _rauth(), _FakeLedger()
    db = _FakeDB(job, auth)
    ex = _executor(monkeypatch, tmp_path, db, ledger, _ok_script, receipt_id="")
    monkeypatch.setattr(ex, "_is_exact_merge_commit", lambda **k: True)
    monkeypatch.setattr(ex, "_rollback_target", lambda **k: None)

    result = ex.execute(job.review_job_id, manifest=_FakeManifest(job))

    assert result.success is False
    assert "no signed merge receipt" in result.error
    kinds = [c[0] for c in ledger.calls]
    assert "merge_outcome" not in kinds  # never counts as a clean merge


def test_receipt_present_finalizes_merged(monkeypatch, tmp_path):
    """Sanity: a real receipt id still finalizes the merge as success."""
    job, auth, ledger = _rjob(), _rauth(), _FakeLedger()
    db = _FakeDB(job, auth)
    ex = _executor(monkeypatch, tmp_path, db, ledger, _ok_script, receipt_id="rcpt-1")
    from prismatic.review_factory import events as events_mod

    monkeypatch.setattr(events_mod, "emit_rf_event", lambda *a, **k: None)

    result = ex._execute_merge(job, _FakeManifest(job), auth)

    assert result.success is True
    assert result.merge_receipt_id == "rcpt-1"
    assert ReviewJobState.MERGED in db.state_calls
