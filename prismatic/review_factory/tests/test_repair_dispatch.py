"""Repair-loop wiring tests: rejection -> engine task intake -> audit trail.

Covers the wiring of ``ReviewQueue.dispatch_repair_task`` into the
existing multi-channel task intake
(``prismatic.ingestion_queue.enqueue_multi_channel_task``):

1. A rejected job's repair is enqueued into the intake with full context
   (review job id, Linear task id, verdict receipt, repair packet).
2. Dispatch is idempotent per job (no duplicate intake tasks).
3. When the intake is genuinely unavailable the failure stays loud
   (``repair_dispatch_unavailable`` audit entry) -- the last-resort fallback.
4. The automatic REPAIR_REQUIRED verdict path also dispatches.
"""

import json
import os
import sqlite3

import pytest

import prismatic.ingestion_queue as ingestion_queue
from prismatic.ingestion_queue import queue_db_path
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    RepairPacket,
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue


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


def _enqueue(queue, **over):
    kwargs = dict(
        completed_work_id="cw-repair-1",
        task_id="GRO-9999",
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
    packet_id = queue.db.insert_repair_packet(packet)
    queue.db.update_review_job_state(
        job_id, ReviewJobState.REPAIR_REQUIRED, lease_owner="", lease_expires_at=""
    )
    return packet_id


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


def _audit_actions(queue, job_id):
    return [
        e["action"]
        for e in queue.db.list_audit_entries(limit=100)
        if e["review_job_id"] == job_id
    ]


# ── 1. happy path: rejection -> intake ───────────────────────────────


def test_repair_dispatch_enqueues_intake_task(tmp_path, isolated_state):
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)
    packet_id = _to_repair_required(queue, job_id)

    event_id = queue.dispatch_repair_task(job_id, failure_reason="checks failed")

    assert event_id and event_id.startswith("evt_review-factory_")

    rows = _queue_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == event_id
    assert row["identifier"] == f"RF-REPAIR-{job_id[:8]}"
    assert row["dispatch_status"] == "pending"
    assert row["target_agent"] == "fred"

    raw = json.loads(row["raw_json"])
    assert raw["review_job_id"] == job_id
    assert raw["task_id"] == "GRO-9999"
    assert raw["repository"] == "proof/repo"
    assert raw["candidate_commit"] == "c" * 40
    assert raw["repair_packet_id"] == packet_id
    assert raw["failure_reason"] == "checks failed"
    assert "requeue_repaired_candidate" in raw["description"]
    assert raw["requeue"]["review_job_id"] == job_id

    actions = _audit_actions(queue, job_id)
    assert "repair_dispatched" in actions
    assert "repair_dispatch_unavailable" not in actions
    entry = queue.db.find_audit_entry(job_id, "repair_dispatched")
    assert json.loads(entry["details_json"])["intake_event_id"] == event_id


def test_repair_dispatch_target_agent_override(tmp_path, isolated_state):
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)
    _to_repair_required(queue, job_id)

    event_id = queue.dispatch_repair_task(job_id, target_agent="george")
    rows = _queue_rows()
    assert len(rows) == 1
    assert rows[0]["target_agent"] == "george"
    assert rows[0]["event_id"] == event_id

    # Unknown agents fall back to a resolvable one instead of poisoning routing.
    # NOTE: distinct candidate_commit -- the zombie-job safeguard (Gap 1)
    # links a resubmit of the same (task_id, candidate_commit) to the
    # surviving job instead of creating a second one.
    job_id2 = _enqueue(
        queue, completed_work_id="cw-repair-2", candidate_commit="d" * 40
    )
    _to_repair_required(queue, job_id2)
    queue.dispatch_repair_task(job_id2, target_agent="not-an-agent")
    rows = _queue_rows()
    assert rows[1]["target_agent"] == "fred"


# ── 2. idempotency ───────────────────────────────────────────────────


def test_repair_dispatch_idempotent_per_job(tmp_path, isolated_state):
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)
    _to_repair_required(queue, job_id)

    first = queue.dispatch_repair_task(job_id, failure_reason="x")
    second = queue.dispatch_repair_task(job_id, failure_reason="x")

    assert first == second
    assert len(_queue_rows()) == 1
    dispatched = [
        e
        for e in queue.db.list_audit_entries(limit=100)
        if e["review_job_id"] == job_id and e["action"] == "repair_dispatched"
    ]
    assert len(dispatched) == 1


# ── 3. loud fallback when the intake is unavailable ──────────────────


def test_repair_dispatch_fallback_when_intake_missing(
    tmp_path, isolated_state, monkeypatch
):
    queue = _queue(tmp_path)
    job_id = _enqueue(queue)
    _to_repair_required(queue, job_id)

    def _boom(**kwargs):
        raise RuntimeError("intake down")

    monkeypatch.setattr(ingestion_queue, "enqueue_multi_channel_task", _boom)

    assert queue.dispatch_repair_task(job_id, failure_reason="x") is None
    assert _queue_rows() == []
    actions = _audit_actions(queue, job_id)
    assert "repair_dispatch_unavailable" in actions
    assert "repair_dispatched" not in actions


# ── 4. automatic REPAIR_REQUIRED verdict also dispatches ─────────────


def _verdict_flow_to_reviewing(queue):
    job_id = _enqueue(queue, completed_work_id="cw-verdict-1")
    job = queue.lease_for_verification("verifier-1")
    assert job is not None
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
        classification="targeted",
    )
    assert queue.complete_verification(job_id, receipt, worker_id="verifier-1")
    leased = queue.lease_for_review("agy-v1.0")
    assert leased is not None
    return job_id, receipt


def test_verdict_repair_required_dispatches(tmp_path, isolated_state):
    queue = _queue(tmp_path)
    job_id, receipt = _verdict_flow_to_reviewing(queue)

    decision = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="agy-v1.0",
        reviewer_capability_version="t",
        candidate_commit="c" * 40,
        candidate_tree="c" * 40,
        receipt_id=receipt.receipt_id,
        verdict=ReviewVerdict.REPAIR_REQUIRED.value,
        findings=json.dumps([{"check": "tests", "message": "2 failures"}]),
    )
    new_state = queue.submit_verdict(job_id, decision, reviewer_id="agy-v1.0")

    assert new_state == ReviewJobState.REPAIR_REQUIRED.value
    rows = _queue_rows()
    assert len(rows) == 1
    raw = json.loads(rows[0]["raw_json"])
    assert raw["review_job_id"] == job_id
    assert raw["verdict"] == ReviewVerdict.REPAIR_REQUIRED.value
    assert raw["receipt_id"] == receipt.receipt_id
    assert "2 failures" in raw["failure_reason"]
    actions = _audit_actions(queue, job_id)
    assert "repair_dispatched" in actions


# ── 5. Linear visibility helper never breaks dispatch ───────────────


def test_linear_notify_is_best_effort(tmp_path, isolated_state, monkeypatch):
    # Even if the Linear provider explodes, dispatch still succeeds.
    queue = _queue(tmp_path)
    job_id = _enqueue(queue, task_id="GRO-4242")
    _to_repair_required(queue, job_id)

    import prismatic.providers.tasks.linear as linear_mod

    def _boom(self, issue_id, body):
        raise RuntimeError("linear down")

    monkeypatch.setattr(linear_mod.LinearTaskProvider, "add_comment", _boom)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")

    event_id = queue.dispatch_repair_task(job_id, failure_reason="x")
    assert event_id
    assert "repair_dispatched" in _audit_actions(queue, job_id)
