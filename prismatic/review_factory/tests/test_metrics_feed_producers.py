"""Tests for watchdog metrics-feed producer wiring (Phase 0 observe-only).

The metrics feed (``prismatic/review_factory/metrics_feed.py``) defines
event recorders with zero production call sites until this wiring. These
tests pin the wired producer sites: each must append the right event row
without ever altering the hot-path decision.

Producers import ``record_*`` lazily at call time, so tests monkeypatch
the ``metrics_feed`` module attributes with spies. Call sites always use
the default production event log; the spies prove the payload, not the
sink.
"""

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from prismatic.core.merge_factory import MergeFactoryStore
from prismatic.jev import DecisionError
from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review_factory import metrics_feed
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.failure_diagnosis import (
    FailureDiagnoser,
    diagnose_ci_failure,
)
from prismatic.review_factory.failure_triage import (
    JEV_ADVISED,
    JEV_ERRORED,
    FailureInput,
    FailureTriage,
)
from prismatic.review_factory.merge_authority import MergeAuthority, MergeInput
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.queue import ReviewQueue

HERE = Path(__file__).resolve()
RF_SPEC = HERE.parent.parent / "spec"
MERGE_POLICY_YAML = RF_SPEC / "auto_merge_policy_v1.yaml"
DIAGNOSIS_SPEC = RF_SPEC / "diagnosis_signatures_v1.yaml"


# ── spy ──────────────────────────────────────────────────────────────


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"event": "spy", **kwargs}

    def one(self):
        assert len(self.calls) == 1, f"expected 1 call, got {len(self.calls)}"
        return self.calls[0]


# ── executor rollback: real git repo fixture ──────────────────────────


def _make_git_repo(tmp_path):
    repo = tmp_path / "merge-repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-b", "main")
    git("config", "user.email", "rf-test@example.com")
    git("config", "user.name", "RF Test")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("# fixture\n")
    git("add", ".")
    git("commit", "-m", "base")
    base_commit = git("rev-parse", "HEAD").stdout.strip()

    git("checkout", "-b", "candidate")
    (repo / "docs" / "readme.md").write_text("# fixture\n\ncandidate change\n")
    git("add", ".")
    git("commit", "-m", "candidate")
    candidate_commit = git("rev-parse", "HEAD").stdout.strip()
    candidate_tree = git("rev-parse", f"{candidate_commit}^{{tree}}").stdout.strip()

    git("checkout", "main")
    git("merge", "--no-ff", "candidate", "-m", "merge candidate")
    merge_sha = git("rev-parse", "HEAD").stdout.strip()
    git("reset", "--hard", base_commit)

    return {
        "repo": repo,
        "base_commit": base_commit,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "merge_sha": merge_sha,
    }


def _v1_policy_queue(db):
    policy_path = RF_SPEC / "policy_file_v1.yaml"
    return ReviewQueue(db=db, policy=PolicyEngine.from_yaml(policy_path))


def _enqueue_merge_ready_job(
    queue, tier=0, base_commit=None, candidate_commit=None, candidate_tree=None
):
    base_commit = base_commit or "a" * 40
    candidate_commit = candidate_commit or "b" * 40
    candidate_tree = candidate_tree or candidate_commit
    job_id = queue.enqueue_completed_work(
        completed_work_id=f"agy-cw-prod-{tier}-{id(queue)}",
        task_id="GRO-TEST-MERGE",
        repository="mbgulden/prismatic-engine",
        base_commit=base_commit,
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
        changed_paths=["docs/readme.md"],
    )
    _ = queue.lease_for_verification("verifier-1")
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
    )
    queue.complete_verification(job_id, receipt, worker_id="verifier-1")
    job_obj = queue.db.get_review_job(job_id)
    needed = job_obj.required_witnesses if job_obj.required_witnesses > 0 else 1
    for witness_n in range(needed):
        leased = queue.lease_for_review(f"reviewer-{witness_n}")
        if leased is not None:
            decision = ReviewDecision(
                review_job_id=job_id,
                reviewer_id=f"reviewer-{witness_n}",
                candidate_commit=candidate_commit,
                candidate_tree=candidate_tree,
                receipt_id=receipt.receipt_id,
                verdict=ReviewVerdict.CLEAN.value,
            )
            queue.submit_verdict(job_id, decision, reviewer_id=f"reviewer-{witness_n}")
    return job_id


def _eligible_manifest(base_sha, candidate_sha, head_sha):
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-TEST-MERGE",
        task_id="GRO-TEST-MERGE",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/test-merge",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )
    # CANDIDATE → REVIEW_REQUIRED → CLEAN
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
        reviewer="antigravity-rf-v1",
        review_id="review-test-1",
        verdict="CLEAN",
        reviewed_sha=candidate_sha,
        reviewed_manifest_digest=manifest._digest_without_review(),
        scope_clean=True,
        conflict_free=True,
    )
    manifest = manifest.record_review(review)

    manifest = manifest.record_ci(
        [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha=head_sha,
                details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
                provider_receipt_id="receipt-123",
                provider_receipt_sha256="a" * 64,
                provider_policy_sha256="b" * 64,
            )
        ]
    )
    return manifest.mark_merge_eligible()


class TestExecutorRollbackProducer:
    def test_failed_merge_rolls_back_and_records_event(self, tmp_path, monkeypatch):
        """The CAS rollback path records one rollback row and restores main."""
        git_ids = _make_git_repo(tmp_path)
        db = ReviewFactoryDB(db_path=tmp_path / "test_merge_real.db")
        db.ensure_tables()
        queue = _v1_policy_queue(db)
        try:
            mf_store = MergeFactoryStore(db_path=tmp_path / "test_mf.db")
            executor = MergeExecutor(
                queue=queue,
                dry_run=False,
                mf_store=mf_store,
                repo_path=git_ids["repo"],
            )
            job_id = _enqueue_merge_ready_job(
                queue,
                base_commit=git_ids["base_commit"],
                candidate_commit=git_ids["candidate_commit"],
                candidate_tree=git_ids["candidate_tree"],
            )
            auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
            assert auth_id is not None
            manifest = _eligible_manifest(
                git_ids["base_commit"],
                git_ids["candidate_commit"],
                git_ids["candidate_commit"],
            )

            mock_receipt = MagicMock()
            mock_receipt.receipt_id = "receipt-123"
            mock_receipt.receipt_sha256 = "a" * 64
            mock_receipt.policy_sha256 = "b" * 64
            mock_store = MagicMock()
            mock_store.get.return_value = mock_receipt
            executor.verification_receipt_store = mock_store

            integration_manifest = MagicMock()
            integration_manifest.merge_sha = git_ids["merge_sha"]
            integration_manifest.is_success.return_value = False
            integration_manifest.error_message = "integration boom"

            def fake_integrate(**kwargs):
                # Simulate the pipeline having created the merge commit on
                # main and then reporting failure.
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(git_ids["repo"]),
                        "update-ref",
                        "refs/heads/main",
                        git_ids["merge_sha"],
                    ],
                    check=True,
                    capture_output=True,
                )
                return integration_manifest

            spy = _Spy()
            monkeypatch.setattr(metrics_feed, "record_rollback", spy)

            with patch(
                "prismatic.review_factory.merge_executor.integrate_pipeline_run",
                side_effect=fake_integrate,
            ):
                result = executor.execute(job_id, manifest=manifest)

            assert not result.success
            row = spy.one()
            assert row["job_id"] == job_id
            assert row["merge_sha"] == git_ids["merge_sha"]
            assert row["reason"]  # non-empty reason required by the feed

            # The CAS rollback really ran: main is back at the base commit.
            head = subprocess.run(
                ["git", "-C", str(git_ids["repo"]), "rev-parse", "main"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert head == git_ids["base_commit"]
        finally:
            queue.close()


# ── receiver rollback producer ────────────────────────────────────────


class TestReceiverRollbackProducer:
    def _receiver(self):
        from pe.deploy.receiver import DeployReceiverPipeline

        return DeployReceiverPipeline.__new__(DeployReceiverPipeline)

    def _record(self, **overrides):
        from pe.deploy.manifest import DeployRecord

        kwargs = {
            "deploy_id": "deploy-abc12345",
            "pr_sha": "f" * 40,
            "failure_reason": "health check failed",
            "gateway_deploy": {"rolled_back": True},
        }
        kwargs.update(overrides)
        return DeployRecord(**kwargs)

    def test_gateway_rollback_records_event(self, monkeypatch):
        from pe.deploy.receiver import DeployReceiverPipeline

        receiver = self._receiver()
        monkeypatch.setattr(
            DeployReceiverPipeline,
            "_learn_loop_job_id",
            staticmethod(lambda pr_sha: "job-42"),
        )
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_rollback", spy)

        receiver._feed_watchdog_rollback(self._record())

        row = spy.one()
        assert row["job_id"] == "job-42"
        assert row["merge_sha"] == "f" * 40
        assert "rolled back" in row["reason"]

    def test_no_record_when_not_rolled_back(self, monkeypatch):
        receiver = self._receiver()
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_rollback", spy)
        receiver._feed_watchdog_rollback(self._record(gateway_deploy={}))
        assert spy.calls == []

    def test_no_record_on_dry_run(self, monkeypatch):
        receiver = self._receiver()
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_rollback", spy)
        receiver._feed_watchdog_rollback(self._record(dry_run=True))
        assert spy.calls == []

    def test_no_record_when_job_unjoinable(self, monkeypatch):
        from pe.deploy.receiver import DeployReceiverPipeline

        receiver = self._receiver()
        monkeypatch.setattr(
            DeployReceiverPipeline,
            "_learn_loop_job_id",
            staticmethod(lambda pr_sha: None),
        )
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_rollback", spy)
        receiver._feed_watchdog_rollback(self._record())
        assert spy.calls == []


# ── authority escalation producer ────────────────────────────────────


class _FakeLock:
    def __init__(self):
        self.entered = 0

    def acquire(self, *, resource, holder, ttl_seconds):
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        return False


class TestEscalationProducer:
    def _authority(self, tmp_path):
        return MergeAuthority(
            policy_path=MERGE_POLICY_YAML,
            lock_client=_FakeLock(),
            executor=SimpleNamespace(),
            audit_log=tmp_path / "audit.jsonl",
        )

    def _input(self, job_id="job-esc-1"):
        return MergeInput(
            job_id=job_id,
            repository="mbgulden/prismatic-engine",
            head_sha="a" * 40,
            tier=0,
        )

    def test_disabled_policy_refusal_records_escalation(self, tmp_path, monkeypatch):
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_escalation", spy)
        auth = self._authority(tmp_path)
        decision = auth.request_merge(self._input())
        assert decision.decision == "refused"
        row = spy.one()
        assert row["job_id"] == "job-esc-1"
        assert row["reason"] == "auto_merge_disabled"

    def test_feed_failure_never_breaks_refusal(self, tmp_path, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("feed exploded")

        monkeypatch.setattr(metrics_feed, "record_escalation", boom)
        auth = self._authority(tmp_path)
        decision = auth.request_merge(self._input(job_id="job-esc-2"))
        assert decision.decision == "refused"
        assert decision.job_id == "job-esc-2"


# ── Jev call producers ───────────────────────────────────────────────


TRIAGE_MASTER_ENV = "SWARMJEV_ENABLED"
TRIAGE_SITE_ENV = "SWARMJEV_CALLSITE_FAILURE_TRIAGE_ENABLED"
DIAG_MASTER_ENV = "SWARMJEV_ENABLED"
DIAG_SITE_ENV = "SWARMJEV_CALLSITE_FAILURE_DIAGNOSIS_ENABLED"

TRIAGE_POLICY = {
    "version": "test-v1",
    "transients": [
        {
            "name": "test-transient",
            "description": "test rule",
            "patterns": ["connection timed out while fetching"],
            "error_classes": ["test-transient-class"],
        }
    ],
}


@contextmanager
def _triage_gate_on():
    with patch.dict(os.environ):
        os.environ[TRIAGE_MASTER_ENV] = "1"
        os.environ[TRIAGE_SITE_ENV] = "1"
        yield


class _StubBackend:
    """Fake Jev backend: scripted answers, no network."""

    backend_name = "stub"

    def __init__(self, answers=None, fail_with=None):
        self.answers = answers or {}
        self.fail_with = fail_with

    def decide(self, state, questions, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        from prismatic.jev.backends import BackendResult

        parsed = {}
        for q in questions:
            parsed[q.name] = q.default_answer(self.answers[q.name])
        return BackendResult(backend=self.backend_name, answers=parsed, latency_ms=3.0)


def _stub_triage_client(**answers):
    return _decision_client(answers=answers)


def _decision_client(answers=None, fail_with=None):
    from prismatic.jev.client import DecisionClient

    return DecisionClient(backend=_StubBackend(answers or {}, fail_with=fail_with))


class _StubDiagClient:
    def __init__(self, choice="infra"):
        probs = {"infra": 0.9, "code": 0.05, "flake": 0.03, "deploy": 0.02}
        self.payload = {
            "backend": "stub",
            "latency_ms": 1.0,
            "answers": {
                "diagnosis_advice": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probs,
                    "confidence": 0.9,
                },
                "confident": {
                    "type": "noul",
                    "probability": 0.9,
                    "confidence": 0.9,
                },
            },
        }

    def decide(self, state, questions, *, on_error="raise"):
        return _FakeResult(self.payload)


class _FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def to_audit_dict(self):
        return dict(self._payload)


class _ErrorDiagClient:
    def decide(self, *args, **kwargs):
        raise DecisionError("backend exploded")


class TestTriageJevProducer:
    def _triager(self, tmp_path, client_factory):
        policy_path = tmp_path / "triage_policy.yaml"
        policy_path.write_text(yaml.safe_dump(TRIAGE_POLICY), encoding="utf-8")
        return FailureTriage(
            policy_path=policy_path,
            audit_log=tmp_path / "audit" / "triage.jsonl",
            client_factory=client_factory,
        )

    def test_successful_jev_call_records_ok(self, tmp_path, monkeypatch):
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_jev_call", spy)
        triager = self._triager(
            tmp_path,
            client_factory=lambda: _stub_triage_client(
                triage_verdict="repair", confident=0.9
            ),
        )
        with _triage_gate_on():
            result = triager.triage(
                FailureInput(
                    failure_id="ci:9:1",
                    source="ci",
                    error_text="unclassified weird failure",
                )
            )
        assert result.jev.status == JEV_ADVISED
        row = spy.one()
        assert row["call_site"] == "failure_triage.FailureTriage._consult_jev"
        assert row["ok"] is True

    def test_failed_jev_call_records_error(self, tmp_path, monkeypatch):
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_jev_call", spy)
        triager = self._triager(
            tmp_path,
            client_factory=lambda: _decision_client(fail_with=DecisionError("boom")),
        )
        with _triage_gate_on():
            result = triager.triage(
                FailureInput(
                    failure_id="ci:9:2",
                    source="ci",
                    error_text="unclassified weird failure",
                )
            )
        assert result.jev.status == JEV_ERRORED
        row = spy.one()
        assert row["call_site"] == "failure_triage.FailureTriage._consult_jev"
        assert row["ok"] is False
        assert row["error"] == "DecisionError"


class TestDiagnosisJevProducer:
    def _diagnoser(self, tmp_path, client):
        return FailureDiagnoser(
            spec_path=DIAGNOSIS_SPEC,
            audit_path=str(tmp_path / "audit.jsonl"),
            client_factory=lambda: client,
        ), tmp_path / "audit.jsonl"

    def test_successful_jev_call_records_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv(DIAG_MASTER_ENV, "1")
        monkeypatch.setenv(DIAG_SITE_ENV, "1")
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_jev_call", spy)
        d, _ = self._diagnoser(tmp_path, _StubDiagClient(choice="infra"))
        result = diagnose_ci_failure(d, error_text="weird novel failure xyz")
        assert result.jev_status == "advised"
        row = spy.one()
        assert row["call_site"] == "failure_diagnosis.FailureDiagnoser._consult_jev"
        assert row["ok"] is True

    def test_failed_jev_call_records_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv(DIAG_MASTER_ENV, "1")
        monkeypatch.setenv(DIAG_SITE_ENV, "1")
        spy = _Spy()
        monkeypatch.setattr(metrics_feed, "record_jev_call", spy)
        d, _ = self._diagnoser(tmp_path, _ErrorDiagClient())
        result = diagnose_ci_failure(d, error_text="weird novel failure xyz")
        assert result.jev_status == "errored"
        row = spy.one()
        assert row["call_site"] == "failure_diagnosis.FailureDiagnoser._consult_jev"
        assert row["ok"] is False
        assert row["error"] == "backend exploded"

    def test_feed_failure_never_breaks_diagnosis(self, tmp_path, monkeypatch):
        monkeypatch.setenv(DIAG_MASTER_ENV, "1")
        monkeypatch.setenv(DIAG_SITE_ENV, "1")

        def boom(**kwargs):
            raise RuntimeError("feed exploded")

        monkeypatch.setattr(metrics_feed, "record_jev_call", boom)
        d, _ = self._diagnoser(tmp_path, _StubDiagClient(choice="infra"))
        result = diagnose_ci_failure(d, error_text="weird novel failure xyz")
        assert result.jev_status == "advised"


# ── feed rows are real rows ──────────────────────────────────────────


class TestFeedRowsValidate:
    def test_spied_payloads_pass_feed_validation(self, tmp_path):
        """Every payload shape the producers emit must validate cleanly."""
        log = tmp_path / "events.jsonl"
        metrics_feed.record_rollback(
            job_id="j1", merge_sha="m" * 40, reason="r", event_log=log
        )
        metrics_feed.record_escalation(
            job_id="j1", reason="auto_merge_disabled", event_log=log
        )
        metrics_feed.record_jev_call(call_site="s", ok=True, event_log=log)
        metrics_feed.record_jev_call(
            call_site="s", ok=False, error="DecisionError", event_log=log
        )
        rows = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
        ]
        assert [r["event"] for r in rows] == [
            "rollback",
            "escalation",
            "jev_call",
            "jev_call",
        ]
