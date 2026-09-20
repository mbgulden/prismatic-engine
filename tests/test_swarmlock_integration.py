"""
TDD Tests for SwarmLockManager integrated with Swarmlock v0.2.0.
"""

import time
import pytest
from pathlib import Path
from prismatic.core.locking import SwarmLockManager
from swarmlock import SwarmlockError


def test_swarm_lock_manager_acquire_and_release(tmp_path):
    lock_file = tmp_path / "swarm_locks.json"
    mgr = SwarmLockManager(lock_file=lock_file)

    # Acquire lock for resource
    res = mgr.acquire("workspace/server.py", "agent-alpha", timeout_s=5.0)
    assert res is True

    # Check status
    status = mgr.get_status()
    assert len(status) == 1
    assert status[0]["filePath"] == "workspace/server.py"
    assert status[0]["agentId"] == "agent-alpha"

    # Release lock
    rel = mgr.release("workspace/server.py", "agent-alpha")
    assert rel is True

    status_after = mgr.get_status()
    assert len(status_after) == 0


def test_swarm_lock_manager_lock_conflict_timeout(tmp_path):
    lock_file = tmp_path / "swarm_locks.json"
    mgr1 = SwarmLockManager(lock_file=lock_file)
    mgr2 = SwarmLockManager(lock_file=lock_file)

    # Agent 1 acquires lock
    assert mgr1.acquire("workspace/server.py", "agent-1", timeout_s=5.0) is True

    # Agent 2 attempts acquire with short timeout (0.2s) -> should fail
    start = time.time()
    assert mgr2.acquire("workspace/server.py", "agent-2", timeout_s=0.2) is False
    elapsed = time.time() - start
    assert elapsed >= 0.15


def test_swarm_lock_manager_heartbeat_renewal(tmp_path):
    lock_file = tmp_path / "swarm_locks.json"
    mgr = SwarmLockManager(lock_file=lock_file)

    assert mgr.acquire("workspace/server.py", "agent-hb", timeout_s=5.0) is True

    # Refresh heartbeat
    renewed = mgr.heartbeat("workspace/server.py", "agent-hb")
    assert renewed is True

    # Non-existent lock heartbeat renewal -> False
    assert mgr.heartbeat("workspace/other.py", "agent-hb") is False
