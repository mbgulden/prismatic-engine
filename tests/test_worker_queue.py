"""Unit tests for WorkerQueueManager and distributed queue fencing."""

import time
from pathlib import Path
from prismatic.worker.protocol import WorkerJob, WorkerNode, WorkerReceipt
from prismatic.worker.queue import WorkerQueueManager


def test_worker_queue_job_lifecycle(tmp_path: Path):
    db_path = tmp_path / "test_queue.db"
    mgr = WorkerQueueManager(db_path=db_path)

    # 1. Register worker
    worker = mgr.register_worker(
        node_id="node-test-1",
        hostname="test-host",
        ip="100.64.0.1",
        tags=["general", "gpu"],
    )
    assert worker.node_id == "node-test-1"
    assert worker.status == "active"

    # 2. Enqueue job
    job = mgr.enqueue_job(
        task_id="GRO-TEST-100",
        command="echo hello",
        target="prismatic-core",
        tags=["gpu"],
        timeout_seconds=60,
    )
    assert job.task_id == "GRO-TEST-100"
    assert job.status == "queued"
    assert job.fence_token == 0

    # 3. Lease job
    leased = mgr.lease_next_job(node_id="node-test-1", tags=["gpu"])
    assert leased is not None
    assert leased.id == job.id
    assert leased.status == "leased"
    assert leased.node_id == "node-test-1"
    assert leased.fence_token == 1
    assert leased.lease_id is not None

    # Verify no more jobs available
    assert mgr.lease_next_job(node_id="node-test-1", tags=["gpu"]) is None

    # 4. Heartbeat with correct fence token
    hb_ok = mgr.heartbeat_job(job_id=job.id, node_id="node-test-1", fence_token=1)
    assert hb_ok is True

    # Heartbeat with wrong fence token fails
    hb_fail = mgr.heartbeat_job(job_id=job.id, node_id="node-test-1", fence_token=99)
    assert hb_fail is False

    # 5. Complete job
    receipt = {
        "job_id": job.id,
        "node_id": "node-test-1",
        "exit_code": 0,
        "stdout": "hello\n",
        "stderr": "",
        "duration_seconds": 0.05,
    }
    complete_ok = mgr.complete_job(
        job_id=job.id,
        node_id="node-test-1",
        fence_token=1,
        receipt=receipt,
    )
    assert complete_ok is True

    # 6. Verify final state
    final_job = mgr.get_job(job.id)
    assert final_job is not None
    assert final_job.status == "completed"
    assert final_job.result["exit_code"] == 0

    workers = mgr.list_workers()
    assert len(workers) == 1
    assert workers[0].active_job_id is None


def test_worker_queue_lease_expiry_recovery(tmp_path: Path):
    db_path = tmp_path / "test_expiry.db"
    mgr = WorkerQueueManager(db_path=db_path)

    mgr.register_worker(node_id="node-flaky", hostname="host-a")
    mgr.register_worker(node_id="node-healthy", hostname="host-b")

    job = mgr.enqueue_job(
        task_id="GRO-TEST-TIMEOUT",
        command="sleep 100",
        tags=["general"],
    )

    # Lease with very short TTL
    leased = mgr.lease_next_job(node_id="node-flaky", tags=["general"], ttl_seconds=-1)
    assert leased is not None

    # Node-healthy should be able to reclaim the expired job
    reclaimed = mgr.lease_next_job(node_id="node-healthy", tags=["general"], ttl_seconds=60)
    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.node_id == "node-healthy"
    assert reclaimed.fence_token == 2  # Monotonically incremented
