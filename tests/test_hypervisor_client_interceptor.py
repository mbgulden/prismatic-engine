"""Unit and integration tests for HypervisorClient and Agent Interceptor SDK."""

import time
import pytest
from prismatic.client import HypervisorClient, SignalPayload, LeaseContext, TaskContext


def test_signal_payload_serialization():
    sig = SignalPayload(
        agent_id="agy-lightbringer",
        stage="REFACTORING",
        message="Refactoring client interceptor",
        task_id="GRO-5001",
        metadata={"file_count": 3},
    )
    data = sig.to_dict()
    assert data["agent_id"] == "agy-lightbringer"
    assert data["stage"] == "REFACTORING"
    assert data["task_id"] == "GRO-5001"
    assert data["metadata"]["file_count"] == 3
    assert isinstance(data["timestamp"], float)


def test_hypervisor_client_fallback_offline():
    client = HypervisorClient(endpoint="http://127.0.0.1:59999")
    # In offline mode, status reports offline safely without raising
    status = client.get_status()
    assert status["health"]["status"] == "offline"
    assert status["locks"]["active_leases"] == []


def test_lease_context_heartbeat_and_release():
    client = HypervisorClient(endpoint="http://127.0.0.1:59999")
    lease = LeaseContext(client=client, paths=["foo/bar.py"], ttl=10, owner="test-agent")
    
    # Enter context
    with lease:
        assert lease.acquired is True
        assert lease._heartbeat_thread is not None
        assert lease._heartbeat_thread.is_alive()
        time.sleep(0.1)

    # Exited context
    assert lease.acquired is False
    assert lease._stop_heartbeat.is_set()


def test_task_context_admit_and_signals():
    client = HypervisorClient(endpoint="http://127.0.0.1:59999")
    with client.admit_task(task_id="GRO-TEST-01", producer="test-runner") as task:
        assert task.task_id == "GRO-TEST-01"
        assert task.producer == "test-runner"
        
        with task.acquire_lease(paths=["test.py"], ttl=15) as lease:
            assert lease.task_id == "GRO-TEST-01"
            task.emit_signal(stage="IN_PROGRESS", message="Running unit test logic")
