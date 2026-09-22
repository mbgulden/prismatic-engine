"""Zombie-job safeguards tests (Gap 1/2/3).

Covers the safeguards against duplicate review jobs and repair dispatches
for candidates already merged into main:

1. Submitting the same (task_id, candidate_commit) twice -> one job, same id.
2. Submitting a candidate already in main -> job lands in ``superseded``,
   never ``queued``.
3. ``redispatch_stalled_repairs`` on a repair_required job whose candidate
   merged -> job becomes ``superseded``, no intake task created.
4. Repair cycle with a genuinely new candidate commit -> still re-queues
   normally.
"""

import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from prismatic.ingestion_queue import queue_db_path
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    RepairPacket,
    ReviewJobState,
)
from prismatic.review_factory.queue import ReviewQueue

needs_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required"
)


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Point the ingestion-queue state dir at a throwaway directory."""
    state = tmp_path / "prismatic-state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    # LINEAR_API_KEY must be absent so no real Linear comment is attempted.
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    return state


def _queue(tmp_path):
    return ReviewQueue(db=ReviewFactoryDB(db_path=tmp_path / "t.db"))


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_out(repo, *args):
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _make_repo(tmp_path):
    """Build a git repo with ``main`` and a side branch.

    Returns (repo_path, main_tip_sha, side_tip_sha) where the side tip is
    NOT an ancestor of main.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "one")
    main_tip = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("two\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "two")
    side_tip = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return repo, main_tip, side_tip


def _enqueue(queue, **over):
    kwargs = dict(
        completed_work_id="cw-zombie-1",
        task_id="GRO-Z1",
        repository="proof/repo",
        base_commit="a" * 40,
        candidate_commit="c" * 40,
        result_packet_path="/tmp/packet.json",
        result_packet_sha256="e" * 64,
    )
    kwargs.update(over)
    return queue.enqueue_completed_work(**kwargs)


def _to_repair_required(queue, job_id):
    queue.db.update_review_job_state(
        job_id, ReviewJobState.VERIFYING, lease_owner="w", lease_expires_at="x"
    )
    packet = RepairPacket(
        review_job_id=job_id,
        candidate_tree="c" * 40,
        findings_json=json.dumps([{"check": "package", "message": "boom"}]),
        producer_id="reviewer-1",
    )
    queue.db.insert_repair_packet(packet)
    queue.db.update_review_job_state(
        job_id, ReviewJobState.REPAIR_REQUIRED, lease_owner="", lease_expires_at=""
    )


def _audit_actions(queue, job_id):
    return [
        e["action"]
        for e in queue.db.list_audit_entries(limit=200)
        if e["review_job_id"] == job_id
    ]


def _queue_rows():
    path = queue_db_path()
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM linear_webhook_queue")]
    finally:
        conn.close()


# ── 1. duplicate submit -> one job ────────────────────────────────────


def test_duplicate_submit_returns_same_job(tmp_path):
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)

    # Same (task_id, candidate_commit), new completed_work_id.
    again_id = _enqueue(queue, completed_work_id="cw-zombie-2")

    assert again_id == job_id
    assert queue.db.total_jobs() == 1

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.QUEUED.value

    actions = _audit_actions(queue, job_id)
    assert "duplicate_submit_linked" in actions
    entry = queue.db.find_audit_entry(job_id, "duplicate_submit_linked")
    details = json.loads(entry["details_json"])
    assert details["completed_work_id"] == "cw-zombie-2"


def test_terminal_job_does_not_block_resubmit(tmp_path):
    """A resubmit after the first job reached a terminal state makes a job.

    The uniqueness guard only links to non-terminal jobs, so a genuinely
    new review of the same candidate after rejection is not swallowed.
    """
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)
    # QUEUED -> QUARANTINED is a valid transition to a terminal state.
    assert queue.db.update_review_job_state(job_id, ReviewJobState.QUARANTINED)

    new_id = _enqueue(queue, completed_work_id="cw-zombie-2")

    assert new_id != job_id
    assert queue.db.total_jobs() == 2


# ── 2. candidate already in main -> superseded at birth ───────────────


@needs_git
def test_submit_candidate_in_main_superseded(tmp_path):
    repo, main_tip, _side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository=str(repo),
        candidate_commit=main_tip,
    )

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.SUPERSEDED.value

    # Never queued: nothing to lease.
    assert queue.lease_for_verification("verifier-1") is None

    actions = _audit_actions(queue, job_id)
    assert "candidate_already_merged" in actions


@needs_git
def test_submit_candidate_not_in_main_queued(tmp_path):
    repo, _main_tip, side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository=str(repo),
        candidate_commit=side_tip,
    )

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.QUEUED.value


# ── 3. redispatch on merged candidate -> superseded, no intake ────────


@needs_git
def test_redispatch_merged_candidate_superseded(tmp_path, isolated_state):
    repo, _main_tip, side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository=str(repo),
        candidate_commit=side_tip,
    )
    _to_repair_required(queue, job_id)

    # The candidate lands in main after the job went repair_required.
    _git(repo, "merge", "side", "--no-ff", "-m", "merge side")

    summary = queue.redispatch_stalled_repairs()

    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.SUPERSEDED.value
    assert summary["skipped"] >= 1
    assert summary["redispatched"] == []
    assert summary["failed"] == []

    # No intake task was created for the merged candidate.
    assert _queue_rows() == []

    actions = _audit_actions(queue, job_id)
    assert "repair_dispatch_skipped_merged" in actions


@needs_git
def test_dispatch_repair_skips_merged_candidate(tmp_path, isolated_state):
    """dispatch_repair_task itself refuses merged candidates (Gap 3)."""
    repo, main_tip, _side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository="proof/repo",  # unresolvable at submit: queued normally
        candidate_commit="d" * 40,
    )
    # Point the job at the real repo with a merged candidate afterwards.
    queue.db.conn.execute(
        "UPDATE review_jobs SET repository = ?, candidate_commit = ? "
        "WHERE review_job_id = ?",
        (str(repo), main_tip, job_id),
    )
    queue.db.conn.commit()
    _to_repair_required(queue, job_id)

    event_id = queue.dispatch_repair_task(job_id, failure_reason="checks failed")

    assert event_id is None
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.SUPERSEDED.value
    assert _queue_rows() == []
    assert "repair_dispatch_skipped_merged" in _audit_actions(queue, job_id)


# ── 4. genuine repair cycle still re-queues ────────────────────────────


@needs_git
def test_repair_cycle_new_candidate_requeues(tmp_path):
    repo, _main_tip, side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository=str(repo),
        candidate_commit=side_tip,
    )
    _to_repair_required(queue, job_id)

    # A repaired candidate is a NEW commit on the side branch (not in main).
    _git(repo, "checkout", "side")
    (repo / "f.txt").write_text("three\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "three")
    new_tip = _git_out(repo, "rev-parse", "HEAD")
    assert new_tip != side_tip

    ok = queue.consume_repair(
        job_id,
        new_candidate_commit=new_tip,
        new_candidate_tree=new_tip,
    )

    assert ok is True
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.QUEUED.value
    assert job.candidate_commit == new_tip


# ── janitor sweep (Gap 2 follow-through) ──────────────────────────────


@needs_git
def test_janitor_supersedes_newly_merged(tmp_path):
    repo, _main_tip, side_tip = _make_repo(tmp_path)
    queue = _queue(tmp_path)

    job_id = _enqueue(
        queue,
        repository=str(repo),
        candidate_commit=side_tip,
    )
    assert queue.db.get_review_job(job_id).state == ReviewJobState.QUEUED.value

    # The candidate merges after submit; the janitor sweep catches it.
    _git(repo, "merge", "side", "--no-ff", "-m", "merge side")

    result = queue.run_janitor(actor="test")

    assert result["superseded_merged"] == 1
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.SUPERSEDED.value
    assert "candidate_superseded_by_merge" in _audit_actions(queue, job_id)
