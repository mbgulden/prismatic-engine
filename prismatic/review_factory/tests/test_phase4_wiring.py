"""Phase 4 wiring tests: merge-stage authority, Linear hooks, bounded repair
re-dispatch, and the CAS rollback drill (unit/integration level only).

Covers the loop-closing work:

  1. ``MergeStage`` config + tier gating + dry-run + disabled paths.
  2. ``LinearReviewHooks``: comment / create / exhaustion / never-breaks-pipeline.
  3. Bounded repair re-dispatch: first dispatch, backoff, retries, exhaustion,
     and backfill of pre-Phase-4 audit history.
  4. Rollback drill on a scratch git repo: CAS restore after a failed merge,
     and CAS refusal when the target advanced past the merge.
  5. Daemon: the merge step is skipped entirely when no stage is configured.
"""

import hashlib
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    RiskTier,
    VerificationEvidence,
)
from prismatic.providers.tasks.linear import LinearTaskProvider
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.linear_hooks import LinearReviewHooks
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.queue import ReviewQueue


# ── fixtures & helpers ─────────────────────────────────────────────


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Throwaway state dir; no real Linear key; deterministic team id."""
    state = tmp_path / "prismatic-state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setenv("LINEAR_TEAM_ID", "GRO")
    return state


@pytest.fixture()
def queue(tmp_path, isolated_state):
    db = ReviewFactoryDB(db_path=tmp_path / "rf-phase4.db")
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


def _enqueue(queue, **over):
    kwargs = dict(
        completed_work_id="cw-phase4-1",
        task_id="GRO-9999",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
        result_packet_path="/tmp/packet.json",
        result_packet_sha256="e" * 64,
    )
    kwargs.update(over)
    return queue.enqueue_completed_work(**kwargs)


def _repair_job(queue, **over):
    job_id = _enqueue(queue, **over)
    assert queue.db.update_review_job_state(job_id, ReviewJobState.VERIFYING)
    assert queue.db.update_review_job_state(job_id, ReviewJobState.REPAIR_REQUIRED)
    return job_id


class FakeLinear:
    """In-memory LinearTaskProvider double (never touches the network)."""

    instances = []

    def __init__(self):
        self._api_key = "fake-key"
        self.comments = []
        self.created = []
        self.labels_set = []
        FakeLinear.instances.append(self)

    def add_comment(self, ref, body):
        self.comments.append((ref, body))
        return True

    def create_issue(self, team_id, title, description="", label_names=None):
        self.created.append(
            {
                "team_id": team_id,
                "title": title,
                "description": description,
                "label_names": label_names,
            }
        )
        return SimpleNamespace(id="iss-1", identifier="GRO-555", title=title)

    def get_issue(self, issue_id):
        return SimpleNamespace(id=issue_id, labels=[])

    def get_label_id(self, name):
        return f"lbl-{name}"

    def set_labels(self, issue_id, label_ids):
        self.labels_set.append((issue_id, label_ids))
        return True


@pytest.fixture()
def fake_linear(monkeypatch):
    FakeLinear.instances.clear()
    monkeypatch.setattr(
        "prismatic.providers.tasks.linear.LinearTaskProvider", FakeLinear
    )
    return FakeLinear


def _fake_linear_helpers(monkeypatch):
    calls = []
    mod = types.ModuleType("linear_helpers")

    def update_issue_state(issue_id, state_name):
        calls.append({"issue_id": issue_id, "state_name": state_name})
        return {"ok": True}

    mod.update_issue_state = update_issue_state
    monkeypatch.setitem(sys.modules, "linear_helpers", mod)
    return calls


def _hooks(db, provider=None):
    if provider is None:
        provider = FakeLinear()
    return LinearReviewHooks(db=db, provider_factory=lambda: provider), provider


# ── MergeStage config ──────────────────────────────────────────────


class TestMergeStageConfig:
    def test_defaults_are_inert(self):
        cfg = MergeStageConfig()
        assert cfg.enabled is False
        assert cfg.dry_run is True
        assert set(cfg.live_tiers) == set()

    def test_from_env_defaults_inert(self, monkeypatch):
        for var in (
            "PRISMATIC_RF_MERGE_AUTHORITY",
            "PRISMATIC_RF_MERGE_DRY_RUN",
            "PRISMATIC_RF_MERGE_LIVE_TIERS",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = MergeStageConfig.from_env()
        assert cfg.enabled is False
        assert cfg.dry_run is True

    def test_from_env_enables_explicitly(self, monkeypatch):
        monkeypatch.setenv("PRISMATIC_RF_MERGE_AUTHORITY", "1")
        monkeypatch.setenv("PRISMATIC_RF_MERGE_DRY_RUN", "0")
        monkeypatch.setenv("PRISMATIC_RF_MERGE_LIVE_TIERS", "0,1")
        cfg = MergeStageConfig.from_env()
        assert cfg.enabled is True
        assert cfg.dry_run is False
        assert set(cfg.live_tiers) == {0, 1}

    @pytest.mark.parametrize("tiers", [{2}, {3}, {0, 2}, {1, 3}])
    def test_live_tiers_reject_tier_2_and_3(self, tiers):
        with pytest.raises(ValueError, match="tier 2/3"):
            MergeStageConfig(enabled=True, live_tiers=frozenset(tiers))

    @pytest.mark.parametrize("tiers", [set(), {0}, {1}, {0, 1}])
    def test_live_tiers_accept_0_and_1(self, tiers):
        cfg = MergeStageConfig(enabled=True, live_tiers=frozenset(tiers))
        assert set(cfg.live_tiers) == tiers


# ── MergeStage decisions ───────────────────────────────────────────


def _merge_ready_job(queue, tier=0, task_id="GRO-1234"):
    paths = {
        0: ["docs/readme.md"],
        1: ["prismatic/core/x.py"],
        2: ["prismatic/auth/oauth.py"],
    }
    job_id = queue.enqueue_completed_work(
        completed_work_id=f"cw-stage-{tier}-{task_id}",
        task_id=task_id,
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
        changed_paths=paths[tier],
    )
    leased = queue.lease_for_verification("verifier-1")
    assert leased is not None
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
    )
    queue.complete_verification(job_id, receipt, worker_id="verifier-1")
    job = queue.db.get_review_job(job_id)
    needed = job.required_witnesses if job.required_witnesses > 0 else 1
    for n in range(needed):
        leased = queue.lease_for_review(f"reviewer-{n}")
        assert leased is not None
        decision = ReviewDecision(
            review_job_id=job_id,
            reviewer_id=f"reviewer-{n}",
            candidate_commit="c" * 40,
            candidate_tree="c" * 40,
            receipt_id=receipt.receipt_id,
            verdict=ReviewVerdict.CLEAN.value,
        )
        queue.submit_verdict(job_id, decision, reviewer_id=f"reviewer-{n}")
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.MERGE_READY.value
    assert job.risk_tier == tier
    return job_id


class TestMergeStage:
    def test_disabled_stage_skips_without_touching_job(self, queue):
        job_id = _merge_ready_job(queue, tier=0)
        stage = MergeStage(queue, MergeStageConfig())  # disabled by default
        result = stage.process(queue.db.get_review_job(job_id))
        assert result.action == "skipped_disabled"
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.MERGE_READY.value
        assert queue.db.get_authorization_for_job(job_id) is None

    def test_tier_2_refused_fail_closed(self, queue):
        job_id = _merge_ready_job(queue, tier=2)
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=False,
            live_tiers=frozenset({0, 1}),
            repo_path=Path("/tmp"),
        )
        result = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert result.action == "refused_tier"
        assert queue.db.get_authorization_for_job(job_id) is None
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.MERGE_READY.value
        entry = queue.db.find_audit_entry(job_id, "auto_merge_refused_tier")
        assert entry is not None

    def test_tier_not_in_live_tiers_refused(self, queue):
        job_id = _merge_ready_job(queue, tier=1)
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=True,
            live_tiers=frozenset({0}),
        )
        result = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert result.action == "refused_tier"
        assert queue.db.get_authorization_for_job(job_id) is None
        entry = queue.db.find_audit_entry(job_id, "auto_merge_tier_not_enabled")
        assert entry is not None

    def test_live_without_repo_path_refuses(self, queue):
        job_id = _merge_ready_job(queue, tier=0)
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=False,
            live_tiers=frozenset({0}),
            repo_path=None,
        )
        result = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert result.action == "failed"
        assert "repo_path" in result.error
        assert queue.db.get_authorization_for_job(job_id) is None

    def test_dry_run_passes_validation_without_mutation(self, queue, tmp_path):
        job_id = _merge_ready_job(queue, tier=0)
        # The dry-run executor validates against the durable manifest the
        # daemon persisted; mirror that here.
        manifest = MergeCandidateManifest.create(
            issue_id="GRO-1234",
            task_id="GRO-1234",
            task_file_sha256="a" * 64,
            repository="proof/repo",
            target="main",
            base_sha="a" * 40,
            candidate_sha="c" * 40,
            changed_paths=["docs/readme.md"],
            producer="agy",
            preserved_candidate_location="/tmp/x",
            risk_tier=RiskTier.A,
            dashboard_change=False,
            required_ci_checks=["rf-v1-verification"],
        )
        packet_path, digest = _write_manifest_file(tmp_path, manifest)
        with queue.db.transaction() as cur:
            cur.execute(
                "UPDATE review_jobs SET result_packet_path = ?,"
                " result_packet_sha256 = ? WHERE review_job_id = ?",
                (packet_path, digest, job_id),
            )
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=True,
            live_tiers=frozenset({0}),
        )
        result = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert result.action == "dry_run_ok"
        assert result.merge_sha == "dry-run-sha"
        auth_id = result.authorization_id
        assert auth_id
        # The dry-run authorization is spent proving the chain: consumed so
        # it can never back a real merge, and the one-shot guard no longer
        # blocks later runs.
        with queue.db.transaction() as cur:
            cur.execute(
                "SELECT consumed_at FROM merge_authorizations"
                " WHERE authorization_id = ?",
                (auth_id,),
            )
            row = cur.fetchone()
        assert row is not None and row["consumed_at"]
        # The job is stood back down to MERGE_READY -- never stranded.
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.MERGE_READY.value
        assert not job.lease_owner
        entry = queue.db.find_audit_entry(job_id, "merge_dry_run_ok")
        assert entry is not None
        details = json.loads(entry.get("details_json") or "{}")
        assert details.get("stood_down_to_merge_ready") is True
        # Dry runs are repeatable: a second run proves the chain again.
        again = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert again.action == "dry_run_ok"
        assert queue.db.get_review_job(job_id).state == ReviewJobState.MERGE_READY.value

    def test_live_merge_path_calls_executor_and_hooks(
        self, queue, tmp_path, monkeypatch
    ):
        job_id = _merge_ready_job(queue, tier=0, task_id="GRO-1234")
        transitions = _fake_linear_helpers(monkeypatch)
        hooks, provider = _hooks(queue.db)

        seen = {}

        class StubExecutor:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def execute(self, review_job_id, manifest=None):
                assert review_job_id == job_id
                return SimpleNamespace(success=True, merge_sha="abc123live", error="")

        monkeypatch.setattr(
            "prismatic.review_factory.merge_executor.MergeExecutor",
            StubExecutor,
        )
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=False,
            live_tiers=frozenset({0}),
            repo_path=tmp_path,
        )
        result = MergeStage(queue, cfg, linear_hooks=hooks).process(
            queue.db.get_review_job(job_id)
        )
        assert result.action == "merged"
        assert result.merge_sha == "abc123live"
        assert seen.get("dry_run") is False
        entry = queue.db.find_audit_entry(job_id, "auto_merge_completed")
        assert entry is not None
        # Linear hook fired: Done transition + comment.
        assert transitions == [{"issue_id": "GRO-1234", "state_name": "Done"}]
        assert provider.comments and provider.comments[0][0] == "GRO-1234"

    def test_live_merge_failure_is_audited(self, queue, tmp_path, monkeypatch):
        job_id = _merge_ready_job(queue, tier=0)

        class BoomExecutor:
            def __init__(self, **kwargs):
                pass

            def execute(self, review_job_id, manifest=None):
                return SimpleNamespace(success=False, merge_sha="", error="boom")

        monkeypatch.setattr(
            "prismatic.review_factory.merge_executor.MergeExecutor",
            BoomExecutor,
        )
        cfg = MergeStageConfig(
            enabled=True,
            dry_run=False,
            live_tiers=frozenset({0}),
            repo_path=tmp_path,
        )
        result = MergeStage(queue, cfg).process(queue.db.get_review_job(job_id))
        assert result.action == "failed"
        assert "boom" in result.error
        entry = queue.db.find_audit_entry(job_id, "merge_stage_failed")
        assert entry is not None


# ── MergeExecutor paths ────────────────────────────────────────────


def _write_manifest_file(tmp_path, manifest) -> tuple[str, str]:
    path = tmp_path / "merge_candidate.json"
    data = manifest.canonical_json().encode()
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


class TestMergeExecutorPhase4:
    def test_dry_run_ok_with_matching_durable_manifest(self, queue, tmp_path):
        job_id = _merge_ready_job(queue, tier=0)
        auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
        assert auth_id
        manifest = MergeCandidateManifest.create(
            issue_id="GRO-1234",
            task_id="GRO-1234",
            task_file_sha256="a" * 64,
            repository="proof/repo",
            target="main",
            base_sha="a" * 40,
            candidate_sha="c" * 40,
            changed_paths=["docs/readme.md"],
            producer="agy",
            preserved_candidate_location="/tmp/x",
            risk_tier=RiskTier.A,
            dashboard_change=False,
            required_ci_checks=["rf-v1-verification"],
        )
        packet_path, digest = _write_manifest_file(tmp_path, manifest)
        with queue.db.transaction() as cur:
            cur.execute(
                "UPDATE review_jobs SET result_packet_path = ?,"
                " result_packet_sha256 = ? WHERE review_job_id = ?",
                (packet_path, digest, job_id),
            )
        executor = MergeExecutor(queue=queue, dry_run=True)
        result = executor.execute(job_id, manifest=manifest)
        assert result.success
        assert result.merge_sha == "dry-run-sha"

    def test_dry_run_rejects_tampered_supplied_manifest(self, queue, tmp_path):
        job_id = _merge_ready_job(queue, tier=0)
        queue.authorize_merge(job_id, actor="standing-policy: tier-0")
        durable = MergeCandidateManifest.create(
            issue_id="GRO-1234",
            task_id="GRO-1234",
            task_file_sha256="a" * 64,
            repository="proof/repo",
            target="main",
            base_sha="a" * 40,
            candidate_sha="c" * 40,
            changed_paths=["docs/readme.md"],
            producer="agy",
            preserved_candidate_location="/tmp/x",
            risk_tier=RiskTier.A,
            dashboard_change=False,
            required_ci_checks=["rf-v1-verification"],
        )
        packet_path, digest = _write_manifest_file(tmp_path, durable)
        with queue.db.transaction() as cur:
            cur.execute(
                "UPDATE review_jobs SET result_packet_path = ?,"
                " result_packet_sha256 = ? WHERE review_job_id = ?",
                (packet_path, digest, job_id),
            )
        tampered = MergeCandidateManifest.create(
            issue_id="GRO-1234",
            task_id="GRO-1234",
            task_file_sha256="a" * 64,
            repository="proof/repo",
            target="main",
            base_sha="a" * 40,
            candidate_sha="d" * 40,
            changed_paths=["docs/readme.md"],
            producer="agy",
            preserved_candidate_location="/tmp/x",
            risk_tier=RiskTier.A,
            dashboard_change=False,
            required_ci_checks=["rf-v1-verification"],
        )
        executor = MergeExecutor(queue=queue, dry_run=True)
        result = executor.execute(job_id, manifest=tampered)
        assert not result.success
        assert "durable result packet" in result.error

    def test_execute_without_authorization_fails_closed(self, queue):
        job_id = _merge_ready_job(queue, tier=0)
        executor = MergeExecutor(queue=queue, dry_run=True)
        result = executor.execute(job_id)
        assert not result.success
        assert "authorization" in result.error.lower()
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.MERGE_READY.value


# ── Linear hooks ───────────────────────────────────────────────────


class TestLinearHooks:
    def test_repair_notify_comments_on_linked_issue(self, queue):
        job_id = _repair_job(queue, task_id="GRO-1234")
        hooks, provider = _hooks(queue.db)
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_repair_required(
            job,
            failure_reason="checks failed",
            intake_event_id="evt-1",
            attempt=1,
        )
        assert result.ok and result.action == "commented"
        assert result.issue_ref == "GRO-1234"
        assert provider.comments
        ref, body = provider.comments[0]
        assert ref == "GRO-1234"
        assert job_id in body and "attempt 1" in body
        entry = queue.db.find_audit_entry(job_id, "linear_repair_notified")
        assert entry is not None

    def test_repair_notify_creates_issue_when_unlinked(self, queue, isolated_state):
        job_id = _repair_job(queue, task_id="widget-9")
        hooks, provider = _hooks(queue.db)
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_repair_required(job, attempt=2)
        assert result.ok and result.action == "created"
        assert result.issue_ref == "GRO-555"
        assert provider.created
        created = provider.created[0]
        assert created["team_id"] == "GRO"
        assert job_id[:8] in created["title"]
        assert created["label_names"] == ["review-factory:repair-required"]
        entry = queue.db.find_audit_entry(job_id, "linear_issue_created")
        assert entry is not None

    def test_repair_notify_never_breaks_pipeline(self, queue):
        job_id = _repair_job(queue, task_id="GRO-1234")

        class BoomLinear(FakeLinear):
            def add_comment(self, ref, body):
                raise RuntimeError("linear is down")

        hooks = LinearReviewHooks(db=queue.db, provider_factory=lambda: BoomLinear())
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_repair_required(job)  # must not raise
        assert not result.ok and result.action == "failed"
        entry = queue.db.find_audit_entry(job_id, "linear_notify_failed")
        assert entry is not None
        details = json.loads(entry.get("details_json") or "{}")
        assert "linear is down" in details.get("error", "")

    def test_unconfigured_linear_audited_once(self, queue):
        job_id = _repair_job(queue, task_id="GRO-1234")
        hooks = LinearReviewHooks(db=queue.db)  # real provider, no API key
        job = queue.db.get_review_job(job_id)
        first = hooks.notify_repair_required(job)
        second = hooks.notify_repair_required(job)
        assert first.action == "unconfigured"
        assert second.action == "unconfigured"
        with queue.db.transaction() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM review_factory_audit_log"
                " WHERE review_job_id = ? AND action = ?",
                (job_id, "linear_unconfigured"),
            )
            count = cur.fetchone()["n"]
        assert count == 1

    def test_notify_merged_transitions_to_done(self, queue, monkeypatch):
        job_id = _merge_ready_job(queue, tier=0, task_id="GRO-1234")
        transitions = _fake_linear_helpers(monkeypatch)
        hooks, provider = _hooks(queue.db)
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_merged(job, merge_sha="abc123def456")
        assert result.ok and result.action == "transitioned"
        assert transitions == [{"issue_id": "GRO-1234", "state_name": "Done"}]
        assert provider.comments and provider.comments[0][0] == "GRO-1234"
        assert "abc123def456"[:12] in provider.comments[0][1]
        entry = queue.db.find_audit_entry(job_id, "linear_merged_notified")
        assert entry is not None

    def test_notify_merged_without_issue_is_audited(self, queue):
        job_id = _merge_ready_job(queue, tier=0, task_id="widget-9")
        hooks, provider = _hooks(queue.db)
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_merged(job, merge_sha="abc")
        assert not result.ok and result.action == "no_issue"
        assert not provider.comments
        entry = queue.db.find_audit_entry(job_id, "linear_no_issue")
        assert entry is not None

    def test_notify_repair_exhausted_is_loud(self, queue):
        job_id = _repair_job(queue, task_id="GRO-1234")
        hooks, provider = _hooks(queue.db)
        job = queue.db.get_review_job(job_id)
        result = hooks.notify_repair_exhausted(job, attempts=3)
        assert result.ok and result.action == "commented"
        assert provider.comments
        assert "FAILED LOUDLY" in provider.comments[0][1]
        entry = queue.db.find_audit_entry(job_id, "linear_exhaustion_notified")
        assert entry is not None


# ── Bounded repair re-dispatch ─────────────────────────────────────


def _set_repair_tracking(queue, job_id, attempts, last_dispatch_iso):
    with queue.db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET repair_attempts = ?,"
            " repair_last_dispatch_at = ? WHERE review_job_id = ?",
            (attempts, last_dispatch_iso, job_id),
        )


def _count_audits(queue, job_id, action):
    with queue.db.transaction() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM review_factory_audit_log"
            " WHERE review_job_id = ? AND action = ?",
            (job_id, action),
        )
        return cur.fetchone()["n"]


class TestRedispatch:
    def test_first_dispatch_for_never_dispatched_job(self, queue):
        job_id = _repair_job(queue)
        summary = queue.redispatch_stalled_repairs()
        assert len(summary["redispatched"]) == 1
        entry = summary["redispatched"][0]
        assert entry["job_id"] == job_id and entry["attempt"] == 1
        assert entry["intake_event_id"]
        job = queue.db.get_review_job(job_id)
        assert job.repair_attempts == 1
        assert job.repair_last_dispatch_at
        assert _count_audits(queue, job_id, "repair_dispatched") == 1

    def test_backoff_skips_recent_dispatch(self, queue):
        job_id = _repair_job(queue)
        assert queue.dispatch_repair_task(job_id, force=True)
        summary = queue.redispatch_stalled_repairs()
        assert summary["redispatched"] == []
        assert summary["exhausted"] == []
        assert summary["skipped"] == 1
        # No duplicate intake task.
        assert _count_audits(queue, job_id, "repair_dispatched") == 1

    def test_retry_after_quiet_period(self, queue):
        job_id = _repair_job(queue)
        assert queue.dispatch_repair_task(job_id, force=True)
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        _set_repair_tracking(queue, job_id, 1, old)
        summary = queue.redispatch_stalled_repairs(
            max_attempts=3,
            stall_timeout_seconds=3600,
            backoff_base_seconds=900,
        )
        assert len(summary["redispatched"]) == 1
        entry = summary["redispatched"][0]
        assert entry["attempt"] == 2
        job = queue.db.get_review_job(job_id)
        assert job.repair_attempts == 2

    def test_exhausted_budget_fails_loud_and_stays_visible(self, queue):
        job_id = _repair_job(queue, task_id="GRO-1234")
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        _set_repair_tracking(queue, job_id, 3, old)
        summary = queue.redispatch_stalled_repairs(max_attempts=3)
        assert len(summary["exhausted"]) == 1
        assert summary["exhausted"][0] == {"job_id": job_id, "attempts": 3}
        entry = queue.db.find_audit_entry(job_id, "repair_redispatch_exhausted")
        assert entry is not None
        details = json.loads(entry.get("details_json") or "{}")
        assert details["attempts"] == 3
        # No further dispatch was attempted.
        assert _count_audits(queue, job_id, "repair_dispatched") == 0
        # Job stays visibly REPAIR_REQUIRED for operator action.
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.REPAIR_REQUIRED.value
        # Linear outage path is audited, not silent (no API key in tests).
        assert queue.db.find_audit_entry(job_id, "linear_unconfigured") is not None

    def test_exhaustion_audited_only_once(self, queue):
        job_id = _repair_job(queue)
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        _set_repair_tracking(queue, job_id, 3, old)
        first = queue.redispatch_stalled_repairs(max_attempts=3)
        second = queue.redispatch_stalled_repairs(max_attempts=3)
        assert len(first["exhausted"]) == 1
        assert second["exhausted"] == []
        assert second["skipped"] == 1
        assert _count_audits(queue, job_id, "repair_redispatch_exhausted") == 1

    def test_backfill_from_legacy_audit_no_duplicate_dispatch(self, queue):
        job_id = _repair_job(queue)
        assert queue.dispatch_repair_task(job_id, force=True)
        # Simulate a pre-Phase-4 row: audit history exists, columns are empty.
        _set_repair_tracking(queue, job_id, 0, "")
        before = _count_audits(queue, job_id, "repair_dispatched")
        assert before == 1
        summary = queue.redispatch_stalled_repairs(max_attempts=3)
        assert summary["redispatched"] == []
        assert summary["exhausted"] == []
        entry = queue.db.find_audit_entry(job_id, "repair_dispatch_backfilled")
        assert entry is not None
        job = queue.db.get_review_job(job_id)
        assert job.repair_attempts == 1
        assert job.repair_last_dispatch_at
        # Still exactly one dispatch — no duplicate intake task.
        assert _count_audits(queue, job_id, "repair_dispatched") == 1

    def test_requeue_repaired_candidate_alias(self, queue):
        job_id = _repair_job(queue)
        new_commit = "d" * 40
        assert (
            queue.requeue_repaired_candidate(
                job_id, new_commit, new_candidate_tree="e" * 40
            )
            is True
        )
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.QUEUED.value
        assert job.candidate_commit == new_commit

    def test_no_task_id_audited_as_failed_not_redispatched(self, queue, monkeypatch):
        job_id = _repair_job(queue)
        monkeypatch.setattr(queue, "dispatch_repair_task", lambda *a, **k: None)
        summary = queue.redispatch_stalled_repairs()
        assert summary["redispatched"] == []
        assert summary["failed"] == [{"job_id": job_id, "attempt": 1}]
        entry = queue.db.find_audit_entry(job_id, "repair_redispatch_no_task_id")
        assert entry is not None
        # Not reported as a success anywhere.
        assert _count_audits(queue, job_id, "repair_redispatched") == 0
        # The job is still stalled, not silently dropped.
        job = queue.db.get_review_job(job_id)
        assert job.state == ReviewJobState.REPAIR_REQUIRED.value

    def test_no_task_id_on_retry_audited_as_failed(self, queue, monkeypatch):
        job_id = _repair_job(queue)
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        _set_repair_tracking(queue, job_id, 1, old)
        monkeypatch.setattr(queue, "dispatch_repair_task", lambda *a, **k: None)
        summary = queue.redispatch_stalled_repairs(max_attempts=3)
        assert summary["redispatched"] == []
        assert summary["failed"] == [{"job_id": job_id, "attempt": 2}]
        assert (
            queue.db.find_audit_entry(job_id, "repair_redispatch_no_task_id")
            is not None
        )


# ── Rollback drill (scratch repo; unit/integration level only) ─────


def _git(repo):
    def run(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    return run


@pytest.fixture()
def scratch_repo(tmp_path):
    repo = tmp_path / "rollback-repo"
    repo.mkdir()
    git = _git(repo)
    git("init", "-b", "main")
    git("config", "user.email", "rf-test@example.com")
    git("config", "user.name", "RF Test")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("# fixture\n")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    git("checkout", "-b", "candidate")
    (repo / "docs" / "readme.md").write_text("# fixture\n\ncandidate\n")
    git("add", ".")
    git("commit", "-m", "candidate")
    candidate = git("rev-parse", "HEAD").stdout.strip()
    candidate_tree = git("rev-parse", f"{candidate}^{{tree}}").stdout.strip()

    git("checkout", "main")
    git("merge", "--no-ff", "candidate", "-m", "merge candidate")
    merge_sha = git("rev-parse", "HEAD").stdout.strip()
    git("reset", "--hard", base)
    return {
        "repo": repo,
        "git": git,
        "base": base,
        "candidate": candidate,
        "candidate_tree": candidate_tree,
        "merge_sha": merge_sha,
    }


class TestRollbackDrill:
    def test_cas_rollback_restores_exact_merge(self, queue, scratch_repo):
        git = scratch_repo["git"]
        executor = MergeExecutor(queue=queue, repo_path=scratch_repo["repo"])
        # Simulate: merge landed, then the pipeline failed post-merge.
        git(
            "update-ref",
            "refs/heads/main",
            scratch_repo["merge_sha"],
            scratch_repo["base"],
        )
        assert (
            executor._is_exact_merge_commit(
                merge_sha=scratch_repo["merge_sha"],
                original_head=scratch_repo["base"],
                candidate_commit=scratch_repo["candidate"],
            )
            is True
        )
        assert (
            executor._rollback_target(
                target_branch="main",
                original_head=scratch_repo["base"],
                failed_head=scratch_repo["merge_sha"],
            )
            is None
        )
        assert git("rev-parse", "HEAD").stdout.strip() == scratch_repo["base"]

    def test_cas_rollback_refuses_when_target_advanced(self, queue, scratch_repo):
        git = scratch_repo["git"]
        executor = MergeExecutor(queue=queue, repo_path=scratch_repo["repo"])
        git(
            "update-ref",
            "refs/heads/main",
            scratch_repo["merge_sha"],
            scratch_repo["base"],
        )
        # Someone else advanced main past our merge.
        (scratch_repo["repo"] / "docs" / "other.md").write_text("other\n")
        git("add", ".")
        git("commit", "-m", "foreign advance")
        foreign = git("rev-parse", "HEAD").stdout.strip()
        assert (
            executor._is_exact_merge_commit(
                merge_sha=foreign,
                original_head=scratch_repo["base"],
                candidate_commit=scratch_repo["candidate"],
            )
            is False
        )
        with pytest.raises(subprocess.CalledProcessError):
            executor._rollback_target(
                target_branch="main",
                original_head=scratch_repo["base"],
                failed_head=scratch_repo["merge_sha"],  # stale expectation
            )
        # The foreign commit is untouched.
        assert git("rev-parse", "HEAD").stdout.strip() == foreign

    def test_exact_merge_proof_rejects_non_merges(self, queue, scratch_repo):
        executor = MergeExecutor(queue=queue, repo_path=scratch_repo["repo"])
        # Base and candidate are not merge commits.
        assert (
            executor._is_exact_merge_commit(
                merge_sha=scratch_repo["base"],
                original_head=scratch_repo["base"],
                candidate_commit=scratch_repo["candidate"],
            )
            is False
        )
        assert (
            executor._is_exact_merge_commit(
                merge_sha=scratch_repo["candidate"],
                original_head=scratch_repo["base"],
                candidate_commit=scratch_repo["candidate"],
            )
            is False
        )


def _v1_policy_queue(db):
    policy_path = Path(__file__).resolve().parents[1] / "spec" / "policy_file_v1.yaml"
    return ReviewQueue(db=db, policy=PolicyEngine.from_yaml(policy_path))


def _create_merge_ready_manifest(base_sha, candidate_sha):
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
    return manifest.record_review(review)


def _merge_ready_job_for_drill(queue, git_ids):
    job_id = queue.enqueue_completed_work(
        completed_work_id=f"agy-cw-drill-{id(queue)}",
        task_id="GRO-TEST-MERGE",
        repository="mbgulden/prismatic-engine",
        base_commit=git_ids["base"],
        candidate_commit=git_ids["candidate"],
        candidate_tree=git_ids["candidate_tree"],
        changed_paths=["docs/readme.md"],
    )
    leased = queue.lease_for_verification("verifier-1")
    assert leased is not None
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit=git_ids["candidate"],
        candidate_tree=git_ids["candidate_tree"],
    )
    queue.complete_verification(job_id, receipt, worker_id="verifier-1")
    job = queue.db.get_review_job(job_id)
    needed = job.required_witnesses if job.required_witnesses > 0 else 1
    for n in range(needed):
        leased = queue.lease_for_review(f"reviewer-{n}")
        assert leased is not None
        decision = ReviewDecision(
            review_job_id=job_id,
            reviewer_id=f"reviewer-{n}",
            candidate_commit=git_ids["candidate"],
            candidate_tree=git_ids["candidate_tree"],
            receipt_id=receipt.receipt_id,
            verdict=ReviewVerdict.CLEAN.value,
        )
        queue.submit_verdict(job_id, decision, reviewer_id=f"reviewer-{n}")
    assert queue.db.get_review_job(job_id).state == ReviewJobState.MERGE_READY.value
    return job_id


def _mock_receipt_store(git_ids):
    mock_receipt = MagicMock()
    mock_receipt.receipt_id = "receipt-123"
    mock_receipt.receipt_sha256 = "a" * 64
    mock_receipt.policy_sha256 = "b" * 64
    mock_receipt.task_id = "test-task"
    mock_receipt.candidate_commit = git_ids["candidate"]
    mock_receipt.candidate_tree = git_ids["candidate_tree"]
    mock_receipt.base_commit = git_ids["base"]
    mock_receipt.base_tree = git_ids["base"]
    store = MagicMock()
    store.get.return_value = mock_receipt
    return store


def _drill_executor(tmp_path, isolated_state, git_ids):
    from prismatic.core.merge_factory import MergeFactoryStore

    db = ReviewFactoryDB(db_path=tmp_path / "drill.db")
    db.ensure_tables()
    queue = _v1_policy_queue(db)
    mf_store = MergeFactoryStore(db_path=tmp_path / "drill_mf.db")
    executor = MergeExecutor(
        queue=queue,
        dry_run=False,
        mf_store=mf_store,
        repo_path=git_ids["repo"],
    )
    executor.verification_receipt_store = _mock_receipt_store(git_ids)
    return queue, executor


def _drill_manifest(git_ids):
    manifest = _create_merge_ready_manifest(git_ids["base"], git_ids["candidate"])
    ci_checks = [
        CICheck(
            name="rf-v1-verification",
            run_id=1000,
            conclusion="SUCCESS",
            head_sha=git_ids["candidate"],
            details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
            provider_receipt_id="receipt-123",
            provider_receipt_sha256="a" * 64,
            provider_policy_sha256="b" * 64,
        )
    ]
    manifest = manifest.record_ci(ci_checks)
    return manifest.mark_merge_eligible()


class TestFailedLiveMergeDrill:
    """Integration-level rollback drill on a scratch repo.

    No live systems are involved: the repo, queue, and merge-factory store
    are all throwaway fixtures, and the pipeline runner is stubbed.
    """

    def test_failed_merge_rolls_back_branch(
        self, tmp_path, isolated_state, scratch_repo
    ):
        git_ids = scratch_repo
        git = git_ids["git"]
        queue, executor = _drill_executor(tmp_path, isolated_state, git_ids)
        try:
            job_id = _merge_ready_job_for_drill(queue, git_ids)
            assert queue.authorize_merge(job_id, actor="standing-policy: tier-0")
            manifest = _drill_manifest(git_ids)

            def _fake_integrate(**kwargs):
                # The merge lands in git, then the pipeline fails.
                git(
                    "update-ref",
                    "refs/heads/main",
                    git_ids["merge_sha"],
                    git_ids["base"],
                )
                raise RuntimeError("simulated post-merge failure")

            with patch(
                "prismatic.review_factory.merge_executor.integrate_pipeline_run",
                side_effect=_fake_integrate,
            ):
                result = executor.execute(job_id, manifest=manifest)

            assert not result.success
            assert "simulated post-merge failure" in result.error
            # CAS rollback restored the target branch.
            assert git("rev-parse", "HEAD").stdout.strip() == git_ids["base"]
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_VERIFICATION_FAILED.value
        finally:
            queue.close()

    def test_failed_merge_refuses_rollback_when_target_advanced(
        self, tmp_path, isolated_state, scratch_repo
    ):
        git_ids = scratch_repo
        git = git_ids["git"]
        queue, executor = _drill_executor(tmp_path, isolated_state, git_ids)
        try:
            job_id = _merge_ready_job_for_drill(queue, git_ids)
            assert queue.authorize_merge(job_id, actor="standing-policy: tier-0")
            manifest = _drill_manifest(git_ids)
            # The target advanced past the authorized base before we ran.
            (git_ids["repo"] / "docs" / "foreign.md").write_text("x\n")
            git("add", ".")
            git("commit", "-m", "foreign")
            foreign = git("rev-parse", "HEAD").stdout.strip()

            with patch(
                "prismatic.review_factory.merge_executor.integrate_pipeline_run",
            ) as mocked:
                result = executor.execute(job_id, manifest=manifest)

            assert not result.success
            assert "advanced" in result.error
            mocked.assert_not_called()
            # Fail closed: the foreign head is untouched, no rollback tried.
            assert git("rev-parse", "HEAD").stdout.strip() == foreign
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_VERIFICATION_FAILED.value
        finally:
            queue.close()


# ── Daemon wiring ──────────────────────────────────────────────────


class TestLinearTeamResolution:
    """LINEAR_TEAM_ID holds the team key ("GRO"); the provider must resolve
    it to the UUID the mutations require. UUID passthrough and the empty
    case never touch the network."""

    def test_uuid_passes_through(self):
        provider = LinearTaskProvider(team_id="ignored")
        uuid = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
        assert provider._resolve_team_id(uuid) == uuid

    def test_empty_returns_none(self):
        provider = LinearTaskProvider(team_id="ignored")
        provider._team_id = ""
        assert provider._resolve_team_id(None) is None

    def test_key_resolved_via_graphql(self, monkeypatch):
        provider = LinearTaskProvider(team_id="ignored")

        def fake_graphql(query, variables):
            assert variables == {"key": "GRO"}
            return {"teams": {"nodes": [{"id": "uuid-1", "key": "GRO"}]}}

        monkeypatch.setattr(provider, "_graphql_data", fake_graphql)
        assert provider._resolve_team_id("GRO") == "uuid-1"

    def test_unresolvable_key_returns_none(self, monkeypatch):
        provider = LinearTaskProvider(team_id="ignored")
        monkeypatch.setattr(
            provider,
            "_graphql_data",
            lambda query, variables: {"teams": {"nodes": []}},
        )
        assert provider._resolve_team_id("NOPE") is None


class TestDaemonMergeWiring:
    def test_merge_step_skipped_when_stage_unconfigured(
        self, queue, tmp_path, isolated_state
    ):
        from prismatic.gateway.verification_daemon import (
            VerificationWorkerDaemon,
        )

        job_id = _merge_ready_job(queue, tier=0)
        daemon = VerificationWorkerDaemon(repo_path=str(tmp_path), merge_stage=None)
        daemon.queue = queue  # point at the throwaway queue
        try:
            assert daemon._pump_once() is False
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_READY.value
            assert not job.lease_owner
        finally:
            daemon.stop()

    def test_merge_step_not_leased_when_stage_disabled(
        self, queue, tmp_path, isolated_state
    ):
        from prismatic.gateway.verification_daemon import (
            VerificationWorkerDaemon,
        )

        job_id = _merge_ready_job(queue, tier=0)
        stage = MergeStage(queue, MergeStageConfig())  # disabled
        daemon = VerificationWorkerDaemon(repo_path=str(tmp_path), merge_stage=stage)
        daemon.queue = queue
        try:
            # Disabled stage: no merge leasing at all (no hot loop).
            assert daemon._pump_once() is False
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_READY.value
            assert not job.lease_owner
        finally:
            daemon.stop()

    def test_merge_step_not_leased_when_no_live_tiers(
        self, queue, tmp_path, isolated_state
    ):
        from prismatic.gateway.verification_daemon import (
            VerificationWorkerDaemon,
        )

        job_id = _merge_ready_job(queue, tier=0)
        stage = MergeStage(
            queue,
            MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset()),
        )
        daemon = VerificationWorkerDaemon(repo_path=str(tmp_path), merge_stage=stage)
        daemon.queue = queue
        try:
            # Authority enabled but no tiers in PRISMATIC_RF_MERGE_LIVE_TIERS:
            # still no leasing at all.
            assert daemon._pump_once() is False
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_READY.value
            assert not job.lease_owner
        finally:
            daemon.stop()

    def test_lease_for_merge_skips_refused_tiers(self, queue):
        tier2 = _merge_ready_job(queue, tier=2)
        # Tier 2 is not in the enabled live tiers: never leased. The job
        # stays MERGE_READY, unleased, without hot-looping.
        assert queue.lease_for_merge("w-1", tiers={0, 1}) is None
        job = queue.db.get_review_job(tier2)
        assert job.state == ReviewJobState.MERGE_READY.value
        assert not job.lease_owner
        # The tier filter is what skips it: it leases fine under {2}.
        leased2 = queue.lease_for_merge("w-3", tiers={2})
        assert leased2 is not None and leased2.review_job_id == tier2
        queue.release_merge_lease(tier2, "w-3")
        # And tier 0/1 jobs still lease under the filter.
        tier0 = _merge_ready_job(queue, tier=0)
        leased0 = queue.lease_for_merge("w-4", tiers={0, 1})
        assert leased0 is not None and leased0.review_job_id == tier0

    def test_authorize_failed_job_never_released_for_merge(
        self, queue, tmp_path, isolated_state, monkeypatch
    ):
        from prismatic.gateway.verification_daemon import (
            VerificationWorkerDaemon,
        )

        job_id = _merge_ready_job(queue, tier=0)
        calls = []

        def _refuse(*args, **kwargs):
            calls.append(1)
            return None

        monkeypatch.setattr(queue, "authorize_merge", _refuse)
        stage = MergeStage(
            queue,
            MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({0, 1})),
        )
        daemon = VerificationWorkerDaemon(repo_path=str(tmp_path), merge_stage=stage)
        daemon.queue = queue
        try:
            leased = queue.lease_for_merge(daemon.worker_id, tiers={0, 1})
            assert leased is not None
            daemon._run_merge_stage(leased)
            assert len(calls) == 1
            entry = queue.db.find_audit_entry(job_id, "merge_authorize_failed")
            assert entry is not None
            # The refused job is released back to MERGE_READY but never
            # re-leased for merge on later pumps (no hot loop).
            assert daemon._pump_once() is False
            assert len(calls) == 1
            job = queue.db.get_review_job(job_id)
            assert job.state == ReviewJobState.MERGE_READY.value
            assert not job.lease_owner
        finally:
            daemon.stop()
