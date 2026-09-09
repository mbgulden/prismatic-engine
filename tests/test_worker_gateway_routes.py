"""Integration tests for Gateway Worker Routes and execution."""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.gateway.routes.worker import get_queue_manager
from prismatic.worker.daemon import WorkerDaemon


def test_gateway_worker_routes_and_job_execution(tmp_path):
    client = TestClient(app)

    # 1. Register worker via Gateway
    res_reg = client.post(
        "/api/gateway/workers/register",
        json={
            "node_id": "test-remote-worker-1",
            "hostname": "test-box",
            "tags": ["general", "python"],
            "version": "0.2.0",
        },
    )
    assert res_reg.status_code == 200
    reg_data = res_reg.json()
    assert reg_data["ok"] is True
    assert reg_data["worker"]["node_id"] == "test-remote-worker-1"

    # 2. Worker heartbeat
    res_hb = client.post(
        "/api/gateway/workers/heartbeat",
        json={"node_id": "test-remote-worker-1"},
    )
    assert res_hb.status_code == 200
    assert res_hb.json()["ok"] is True

    # 3. List workers
    res_workers = client.get("/api/gateway/workers")
    assert res_workers.status_code == 200
    workers = res_workers.json()["workers"]
    assert any(w["node_id"] == "test-remote-worker-1" for w in workers)

    # 4. Enqueue a job
    res_enq = client.post(
        "/api/gateway/jobs/enqueue",
        json={
            "task_id": "GRO-DAEMON-TEST",
            "command": "python3 -c 'print(\"hello from distributed worker\")'",
            "tags": ["python"],
        },
    )
    assert res_enq.status_code == 200
    job_data = res_enq.json()["job"]
    job_id = job_data["id"]
    assert job_data["task_id"] == "GRO-DAEMON-TEST"
    assert job_data["status"] == "queued"

    # 5. Lease the job
    res_lease = client.post(
        "/api/gateway/jobs/lease",
        json={"node_id": "test-remote-worker-1", "tags": ["python"]},
    )
    assert res_lease.status_code == 200
    leased_job = res_lease.json()["job"]
    assert leased_job is not None
    assert leased_job["id"] == job_id
    assert leased_job["fence_token"] == 1

    # 6. Heartbeat the job
    res_jhb = client.post(
        "/api/gateway/jobs/heartbeat",
        json={
            "job_id": job_id,
            "node_id": "test-remote-worker-1",
            "fence_token": leased_job["fence_token"],
        },
    )
    assert res_jhb.status_code == 200
    assert res_jhb.json()["extended"] is True

    # 7. Complete the job
    res_comp = client.post(
        "/api/gateway/jobs/complete",
        json={
            "job_id": job_id,
            "node_id": "test-remote-worker-1",
            "fence_token": leased_job["fence_token"],
            "receipt": {
                "exit_code": 0,
                "stdout": "hello from distributed worker\n",
                "stderr": "",
                "duration_seconds": 0.12,
                "dld_digest": "test-dld-hash",
            },
        },
    )
    assert res_comp.status_code == 200
    assert res_comp.json()["completed"] is True

    # 8. Query job status
    res_job = client.get(f"/api/gateway/jobs/{job_id}")
    assert res_job.status_code == 200
    finished_job = res_job.json()["job"]
    assert finished_job["status"] == "completed"
    assert finished_job["result"]["exit_code"] == 0


def test_worker_daemon_execute_job():
    daemon = WorkerDaemon(
        gateway_url="http://localhost:9000",
        node_id="test-exec-node",
        tags=["python"],
    )

    from prismatic.worker.protocol import WorkerJob
    job = WorkerJob(
        id="job-exec-test",
        task_id="GRO-EXEC-1",
        command="python3 -c 'print(\"deterministic output 123\")'",
        tags=["python"],
    )

    receipt = daemon._execute_job(job)
    assert receipt.job_id == "job-exec-test"
    assert receipt.node_id == "test-exec-node"
    assert receipt.exit_code == 0
    assert "deterministic output 123" in receipt.stdout
    assert len(receipt.dld_digest) == 64  # SHA-256 hex string
    assert receipt.duration_seconds >= 0.0


def test_worker_daemon_full_loop():
    client = TestClient(app)
    daemon = WorkerDaemon(
        gateway_url="http://testserver",
        node_id="test-full-loop-node",
        tags=["loop-tag"],
    )

    def mock_http_request(endpoint: str, method: str = "GET", data=None, timeout=10.0):
        if method == "GET":
            resp = client.get(endpoint)
        elif method == "POST":
            resp = client.post(endpoint, json=data)
        else:
            return None
        if resp.status_code == 200:
            return resp.json()
        return None

    daemon._http_request = mock_http_request

    import time
    unique_task_id = f"GRO-LOOP-{int(time.time() * 1000)}"

    # Enqueue a job for this tag
    client.post(
        "/api/gateway/jobs/enqueue",
        json={
            "task_id": unique_task_id,
            "command": "python3 -c 'print(\"loop success\")'",
            "tags": ["loop-tag"],
        },
    )

    # Run one iteration of the daemon
    exit_code = daemon.run(once=True)
    assert exit_code == 0

    # Verify job status is completed
    jobs_res = client.get("/api/gateway/jobs?status=completed")
    assert jobs_res.status_code == 200
    jobs = jobs_res.json()["jobs"]
    matching = [j for j in jobs if j["task_id"] == unique_task_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "completed"
    assert matching[0]["result"]["exit_code"] == 0



