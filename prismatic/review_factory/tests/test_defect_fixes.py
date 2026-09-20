"""Defect-fix regression tests for the Review Factory (post-#455).

Covers the five fixes shipped in the defect-fix PR:
1. Gateway boot shims (the names server.py imports exist again).
2. Package check runs on a writable copy, never the read-only archive.
3. CLEAN manifest persisted once a job reaches MERGE_READY.
4. Repair dispatch failure is loud (explicit audit entry), never silent.
5. Poison jobs are quarantined (DB-persisted counter, terminal state).
"""

import os

from prismatic.gateway.verification_daemon import (
    start_verification_daemon,
    stop_verification_daemon,
)
from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
    VerificationEvidence,
)
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.reviewer import StubReviewerCapability


def _queue(tmp_path):
    return ReviewQueue(db=ReviewFactoryDB(db_path=tmp_path / "t.db"))


def _enqueue(queue, **over):
    kwargs = dict(
        completed_work_id="cw-defect-1",
        task_id="T-DEF-1",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
        result_packet_path="/tmp/packet.json",
        result_packet_sha256="e" * 64,
    )
    kwargs.update(over)
    return queue.enqueue_completed_work(**kwargs)


def _daemon_for_queue(tmp_path, queue, **over):
    from prismatic.gateway.verification_daemon import VerificationWorkerDaemon

    kwargs = dict(
        repo_path=tmp_path, poll_interval_seconds=60.0, janitor_interval_seconds=3600.0
    )
    kwargs.update(over)
    daemon = VerificationWorkerDaemon(**kwargs)
    daemon.queue = queue
    return daemon


# ── Fix 1: boot shims ────────────────────────────────────────────────


def test_boot_shims_importable_and_server_imports():
    # Regression: #454's daemon rewrite dropped the module-level lifecycle
    # functions that server.py imports, so merged main could not boot.
    assert callable(start_verification_daemon)
    assert callable(stop_verification_daemon)
    import prismatic.gateway.server  # noqa: F401


# ── Fix 2: package check on a writable copy ──────────────────────────


def test_package_check_never_writes_to_readonly_archive(tmp_path):
    # Regression: the package check ran `py_compile` inside the read-only
    # materialized archive, so every tier-1+ verification failed it (EACCES).
    from prismatic.review_factory.verifier import VerificationWorker

    repo = tmp_path / "archive"
    pkg = repo / "prismatic"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""pkg"""\nVALUE = 1\n')
    for root, dirs, files in os.walk(repo):
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o555)
        for f in files:
            os.chmod(os.path.join(root, f), 0o444)
    os.chmod(repo, 0o555)

    worker = VerificationWorker(repo_path=repo, log_dir=tmp_path / "logs")
    result = worker._run_package_check()
    assert result.passed, f"package check failed: {result.stderr}"
    assert not list(repo.rglob("__pycache__")), (
        "py_compile wrote into the read-only archive"
    )


def _review_required_manifest(job):
    """Build the REVIEW_REQUIRED manifest the verify stage would persist."""
    from prismatic.gateway.verification_daemon import _manifest_for_job

    evidence = VerificationEvidence(
        proof_class="focused",
        command="rf-verify-focused",
        summary="focused checks passed",
        result="PASS",
        log_path="/tmp/focused.log",
        log_sha256="a" * 64,
    )
    manifest = _manifest_for_job(job).request_review([evidence])
    assert manifest.state is PromotionState.REVIEW_REQUIRED
    return manifest


# ── Fix 3: CLEAN manifest persisted at MERGE_READY ───────────────────


def test_clean_manifest_persisted_at_merge_ready(tmp_path):
    # Regression: _run_review_stage discarded the reviewer's updated manifest,
    # so the DB kept the REVIEW_REQUIRED manifest (no recorded review) while
    # the job sat at merge_ready -- breaking the manifest digest chain.
    queue = _queue(tmp_path)
    job_id = _enqueue(
        queue, completed_work_id="cw-defect-manifest", changed_paths=["README.md"]
    )
    with queue.db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET required_witnesses = 1 WHERE review_job_id = ?",
            (job_id,),
        )
    queue.db.insert_receipt(
        VerificationReceipt(
            receipt_id="r-def-1",
            review_job_id=job_id,
            candidate_commit="c" * 40,
            candidate_tree="c" * 40,
        )
    )
    # Drive the job down the real pipeline path to REVIEW_READY first.
    assert queue.lease_for_verification("worker-1") is not None
    assert queue.db.update_review_job_state(
        job_id, ReviewJobState.REVIEW_READY, lease_owner="", lease_expires_at=""
    )
    leased = queue.lease_for_review("stub-reviewer")
    assert leased is not None
    # Persist the REVIEW_REQUIRED manifest, as the verify stage does.
    manifest = _review_required_manifest(leased)
    assert queue.db.update_job_manifest(job_id, manifest.canonical_json())
    # Re-fetch so the in-memory job carries the persisted manifest.
    leased = queue.db.get_review_job(job_id)

    daemon = _daemon_for_queue(tmp_path, queue, reviewer_id="stub-reviewer")
    daemon.reviewer = StubReviewerCapability(
        verdict=ReviewVerdict.CLEAN, reviewer_id="stub-reviewer"
    )
    daemon._run_review_stage(leased)

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.MERGE_READY.value
    stored = MergeCandidateManifest.from_json(job.manifest_json)
    assert stored.state is PromotionState.CLEAN
    assert stored.independent_review is not None
    assert stored.independent_review.verdict == "CLEAN"


def test_review_required_manifest_kept_while_witnesses_outstanding(tmp_path):
    # While more witnesses are outstanding the job returns to REVIEW_READY and
    # the REVIEW_REQUIRED manifest must stay, so the next reviewer can still
    # record theirs (record_review only accepts REVIEW_REQUIRED manifests).
    queue = _queue(tmp_path)
    job_id = _enqueue(
        queue, completed_work_id="cw-defect-manifest-2", changed_paths=["README.md"]
    )
    with queue.db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET required_witnesses = 2 WHERE review_job_id = ?",
            (job_id,),
        )
    queue.db.insert_receipt(
        VerificationReceipt(
            receipt_id="r-def-2",
            review_job_id=job_id,
            candidate_commit="c" * 40,
            candidate_tree="c" * 40,
        )
    )
    # Drive the job down the real pipeline path to REVIEW_READY first.
    assert queue.lease_for_verification("worker-1") is not None
    assert queue.db.update_review_job_state(
        job_id, ReviewJobState.REVIEW_READY, lease_owner="", lease_expires_at=""
    )
    leased = queue.lease_for_review("stub-reviewer")
    assert leased is not None
    manifest = _review_required_manifest(leased)
    assert queue.db.update_job_manifest(job_id, manifest.canonical_json())
    # Re-fetch so the in-memory job carries the persisted manifest.
    leased = queue.db.get_review_job(job_id)

    daemon = _daemon_for_queue(tmp_path, queue, reviewer_id="stub-reviewer")
    daemon.reviewer = StubReviewerCapability(
        verdict=ReviewVerdict.CLEAN, reviewer_id="stub-reviewer"
    )
    daemon._run_review_stage(leased)

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.REVIEW_READY.value
    stored = MergeCandidateManifest.from_json(job.manifest_json)
    assert stored.state is PromotionState.REVIEW_REQUIRED


# ── Fix 4: loud repair-dispatch failure ──────────────────────────────


def test_repair_dispatch_wired_into_task_intake(tmp_path, monkeypatch):
    # Repair dispatch is wired into the engine's multi-channel task intake:
    # a rejected job produces a real, visible intake task (not a silent
    # dead end), with an audit entry recording the intake event id.
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, completed_work_id="cw-defect-repair")
    event_id = queue.dispatch_repair_task(job_id, failure_reason="checks failed")
    assert event_id and event_id.startswith("evt_review-factory_")
    actions = [e["action"] for e in queue.db.list_audit_entries(limit=50)]
    assert "repair_dispatched" in actions
    assert "repair_dispatch_unavailable" not in actions


def test_repair_dispatch_failure_is_loud_not_silent(tmp_path, monkeypatch):
    # Last-resort fallback: when the intake itself is unavailable, the
    # failure is recorded as an explicit audit entry -- never silent.
    import prismatic.ingestion_queue as ingestion_queue

    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    def _boom(**kwargs):
        raise RuntimeError("intake down")

    monkeypatch.setattr(ingestion_queue, "enqueue_multi_channel_task", _boom)
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, completed_work_id="cw-defect-repair-fallback")
    assert queue.dispatch_repair_task(job_id, failure_reason="checks failed") is None
    actions = [e["action"] for e in queue.db.list_audit_entries(limit=50)]
    assert "repair_dispatch_unavailable" in actions


def test_verification_failure_audit_records_dispatch_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, completed_work_id="cw-defect-repair-2")
    daemon = _daemon_for_queue(tmp_path, queue)
    receipt = VerificationReceipt(
        receipt_id="r-def-3",
        review_job_id=job_id,
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
        classification="canonical_suite",
    )
    # Simulate the daemon routing a failed verification to repair.
    job = queue.db.get_review_job(job_id)
    queue.db.update_review_job_state(
        job_id, ReviewJobState.VERIFYING, lease_owner="w", lease_expires_at="x"
    )
    daemon._route_verification_failure(job, receipt)
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.REPAIR_REQUIRED.value
    entries = {e["action"]: e for e in queue.db.list_audit_entries(limit=50)}
    assert "verification_failed" in entries
    assert "repair_dispatched" in entries
    assert "repair_dispatch_unavailable" not in entries
    import json as _json

    failed_details = _json.loads(entries["verification_failed"]["details_json"])
    assert failed_details["moved_to_repair"] is True
    assert failed_details["repair_dispatched"] is True


# ── Fix 5: poison quarantine ─────────────────────────────────────────


def test_quarantine_graph():
    for src in (
        ReviewJobState.QUEUED,
        ReviewJobState.VERIFYING,
        ReviewJobState.REVIEW_READY,
        ReviewJobState.REVIEWING,
    ):
        assert src.can_transition_to(ReviewJobState.QUARANTINED), src
    # Terminal: nothing leaves quarantine without operator action.
    assert (
        ReviewJobState.QUARANTINED.valid_transitions()[ReviewJobState.QUARANTINED.value]
        == []
    )


def test_poison_job_quarantined_after_max_failures(tmp_path):
    # Regression: poison jobs were re-leased forever (in-memory failure
    # counter, force-released on every pump, wiped on daemon restart).
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, completed_work_id="cw-defect-poison")
    daemon = _daemon_for_queue(tmp_path, queue, max_consecutive_failures=2)

    job = queue.lease_for_verification("worker-1")
    assert job is not None
    daemon._record_failure(job, stage="verify")
    assert queue.db.get_review_job(job_id).state == ReviewJobState.QUEUED.value
    assert queue.db.get_review_job(job_id).consecutive_failures == 1

    job = queue.lease_for_verification("worker-1")
    daemon._record_failure(job, stage="verify")
    final = queue.db.get_review_job(job_id)
    assert final.state == ReviewJobState.QUARANTINED.value
    assert final.consecutive_failures == 2
    actions = [e["action"] for e in queue.db.list_audit_entries(limit=50)]
    assert "job_quarantined" in actions
    # Terminal: no longer leasable, never re-leased.
    assert queue.lease_for_verification("worker-1") is None


def test_failure_count_survives_across_daemon_instances(tmp_path):
    # The counter lives on the job row, not in daemon memory.
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, completed_work_id="cw-defect-poison-2")
    daemon = _daemon_for_queue(tmp_path, queue, max_consecutive_failures=3)
    job = queue.lease_for_verification("worker-1")
    daemon._record_failure(job, stage="verify")

    restarted = _daemon_for_queue(tmp_path, queue, max_consecutive_failures=3)
    job = queue.lease_for_verification("worker-1")
    assert job.consecutive_failures == 1
    assert restarted._is_poisoned(job) is False
    restarted._record_failure(job, stage="verify")
    restarted._record_failure(queue.lease_for_verification("worker-1"), stage="verify")
    assert queue.db.get_review_job(job_id).state == ReviewJobState.QUARANTINED.value
