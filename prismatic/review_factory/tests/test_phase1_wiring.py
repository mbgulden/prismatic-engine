"""Phase 1 wiring tests: daemon manifest construction, transition graph, intake hook."""

import pytest

from prismatic.agy_completed_work import CompletedWorkRow
from prismatic.gateway.verification_daemon import _manifest_for_job
from prismatic.merge_candidate_manifest import MergeCandidateManifest, RiskTier
from prismatic.review_factory.backlog_importer import BacklogImporter
from prismatic.review_factory.models import ReviewJob, ReviewJobState


def _job(**over):
    kwargs = dict(
        review_job_id="job-1",
        completed_work_id="agy-cw-abc123",
        task_id="GRO-9999",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        base_tree="b" * 40,
        candidate_commit="c" * 40,
        candidate_tree="d" * 40,
        result_packet_path="/tmp/packet.json",
        result_packet_sha256="e" * 64,
        changed_paths_json='["prismatic/foo.py"]',
        risk_tier=1,
    )
    kwargs.update(over)
    return ReviewJob(**kwargs)


def test_manifest_for_job_builds_valid_manifest():
    manifest = _manifest_for_job(_job())
    assert isinstance(manifest, MergeCandidateManifest)
    assert manifest.base_sha == "a" * 40
    assert manifest.candidate_sha == "c" * 40
    assert manifest.risk_tier is RiskTier.B
    assert manifest.producer == "agy"
    assert manifest.required_ci_checks == ("rf-v1-verification",)
    assert not manifest.dashboard_change


def test_manifest_tier_mapping():
    assert _manifest_for_job(_job(risk_tier=0)).risk_tier is RiskTier.A
    assert _manifest_for_job(_job(risk_tier=1)).risk_tier is RiskTier.B
    assert _manifest_for_job(_job(risk_tier=2)).risk_tier is RiskTier.C
    assert _manifest_for_job(_job(risk_tier=3)).risk_tier is RiskTier.C


def test_manifest_dashboard_change_flag():
    job = _job(changed_paths_json='["prismatic/gateway/dashboard/index.html"]')
    assert _manifest_for_job(job).dashboard_change


def test_manifest_producer_derivation():
    assert _manifest_for_job(_job(completed_work_id="ned-cw-1")).producer == "ned"
    assert _manifest_for_job(_job(completed_work_id="weird-id")).producer == "agy"


def test_graph_allows_operator_requested_repair():
    assert ReviewJobState.REVIEW_READY.can_transition_to(ReviewJobState.REPAIR_REQUIRED)
    assert ReviewJobState.MERGE_READY.can_transition_to(ReviewJobState.REPAIR_REQUIRED)


def test_graph_existing_transitions_intact():
    assert ReviewJobState.QUEUED.can_transition_to(ReviewJobState.VERIFYING)
    assert ReviewJobState.VERIFYING.can_transition_to(ReviewJobState.REVIEW_READY)
    assert ReviewJobState.REVIEWING.can_transition_to(ReviewJobState.MERGE_READY)
    assert ReviewJobState.MERGE_READY.can_transition_to(ReviewJobState.MERGE_AUTHORIZED)


def _row(**over):
    kwargs = dict(
        id="agy-cw-x",
        created_at="t",
        updated_at="t",
        agent="agy",
        source_branch="b",
        source_path="p",
        base_branch="main",
        classification="x",
        eligible_for_merge=False,
        requires_clean_rebuild=False,
        proof_result=None,
        proof_marker=None,
        gate_marker="g",
        ingestion_marker="i",
        packet={},
        gate={},
        non_claims=(),
        evidence_retention={},
    )
    kwargs.update(over)
    return CompletedWorkRow(**kwargs)


def test_ingest_completed_work_row_skips_ineligible():
    importer = BacklogImporter.__new__(BacklogImporter)  # no DB needed for skip path
    assert importer.ingest_completed_work_row(_row()) is False


def test_graph_review_ready_requeue():
    # Daemon self-healing: a REVIEW_READY job with no persisted manifest
    # goes back for re-verification.
    assert ReviewJobState.REVIEW_READY.can_transition_to(ReviewJobState.QUEUED)


def test_graph_verify_failure_to_repair():
    # Work that fails verification checks goes to the repair flow,
    # not back to the queue and not on to review.
    assert ReviewJobState.VERIFYING.can_transition_to(ReviewJobState.REPAIR_REQUIRED)


def test_manifest_persist_roundtrip(tmp_path):
    from prismatic.review_factory.db import ReviewFactoryDB
    from prismatic.review_factory.queue import ReviewQueue

    queue = ReviewQueue(db=ReviewFactoryDB(db_path=tmp_path / "t.db"))
    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-manifest-1",
        task_id="T-1",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
    )
    assert queue.db.get_review_job(job_id).manifest_json == ""
    assert queue.db.update_job_manifest(job_id, '{"state": "REVIEW_REQUIRED"}')
    assert (
        queue.db.get_review_job(job_id).manifest_json == '{"state": "REVIEW_REQUIRED"}'
    )


def test_force_release_lease_roundtrip(tmp_path):
    # Regression: force_release_lease passed .value strings to a db method
    # expecting enums and raised AttributeError on every real release.
    from prismatic.review_factory.db import ReviewFactoryDB
    from prismatic.review_factory.queue import ReviewQueue

    queue = ReviewQueue(db=ReviewFactoryDB(db_path=tmp_path / "t.db"))
    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-lease-1",
        task_id="T-2",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
    )
    leased = queue.lease_for_verification("worker-1")
    assert leased is not None
    assert leased.state == ReviewJobState.VERIFYING.value
    assert queue.force_release_lease(job_id, actor="worker-1") is True
    assert queue.db.get_review_job(job_id).state == ReviewJobState.QUEUED.value


def _daemon_for_queue(tmp_path, queue):
    from prismatic.gateway.verification_daemon import VerificationWorkerDaemon

    daemon = VerificationWorkerDaemon(
        repo_path=tmp_path, poll_interval_seconds=60.0, janitor_interval_seconds=3600.0
    )
    daemon.queue = queue
    return daemon


def test_daemon_requeues_when_manifest_missing(tmp_path):
    # A REVIEW_READY job with no persisted manifest is sent back for
    # re-verification instead of being reviewed blind.
    from prismatic.review_factory.db import ReviewFactoryDB
    from prismatic.review_factory.queue import ReviewQueue

    queue = ReviewQueue(db=ReviewFactoryDB(db_path=tmp_path / "t.db"))
    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-requeue-1",
        task_id="T-3",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
    )
    daemon = _daemon_for_queue(tmp_path, queue)
    # Simulate a job verified before manifest persistence existed:
    # REVIEW_READY, leased for review, no receipt needed to reach the
    # manifest check? No - receipts are checked first. Insert a stub receipt.
    from prismatic.review_factory.models import VerificationReceipt

    queue.db.update_review_job_state(
        job_id, ReviewJobState.REVIEW_READY, lease_owner="", lease_expires_at=""
    )
    queue.db.insert_receipt(
        VerificationReceipt(
            receipt_id="r1",
            review_job_id=job_id,
            candidate_commit="c" * 40,
            candidate_tree="c" * 40,
        )
    )
    job = queue.db.get_review_job(job_id)
    assert job.manifest_json == ""
    daemon._run_review_stage(job)
    assert queue.db.get_review_job(job_id).state == ReviewJobState.QUEUED.value
