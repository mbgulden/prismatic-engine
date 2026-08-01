"""Tests for prismatic.distributed_watchdog — Multi-Node Health Circuit."""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from prismatic.distributed_watchdog import (
    DistributedWatchdog,
    NodeRegistry,
    NodeHealth,
    JobRecord,
    NodeDecommissionError,
    TimeoutError,
    JOB_TIMEOUT_S,
    MAX_CONSECUTIVE_FAILURES,
    _scan_vram_markers,
    _cleanup_vram_marker,
    VRAM_MARKER_DIR,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_state_path(tmp_path):
    return str(tmp_path / "watchdog_state.json")


@pytest.fixture
def registry(tmp_state_path):
    r = NodeRegistry(state_path=tmp_state_path)
    r.register_node(
        node_id="worker-1",
        hostname="worker-1.local",
        vram_total_mb=24576,
        gpu_type="rtx-3090",
    )
    r.register_node(
        node_id="worker-2",
        hostname="worker-2.local",
        vram_total_mb=24576,
        gpu_type="rtx-3090",
    )
    r.register_node(
        node_id="worker-3",
        hostname="worker-3.local",
        vram_total_mb=8192,
        gpu_type="rtx-2080",
    )
    return r


@pytest.fixture
def watchdog(registry, tmp_state_path):
    wd = DistributedWatchdog(registry=registry)
    wd.registry._state_path = Path(tmp_state_path)
    return wd


@pytest.fixture
def vram_marker_dir(tmp_path):
    """Create a VRAM marker directory and set the global path."""
    vram_dir = tmp_path / "vram"
    vram_dir.mkdir(parents=True, exist_ok=True)
    original = VRAM_MARKER_DIR
    # Monkey-patch by internal convention — use import path patching
    import prismatic.distributed_watchdog as dw
    dw.VRAM_MARKER_DIR = vram_dir
    yield vram_dir
    dw.VRAM_MARKER_DIR = original


# ═══════════════════════════════════════════════════════════════
# NodeHealth Tests
# ═══════════════════════════════════════════════════════════════


class TestNodeHealth:
    def test_is_alive_fresh(self):
        """A newly created node with recent heartbeat is alive."""
        node = NodeHealth(node_id="test", last_heartbeat=time.time())
        assert node.is_alive is True

    def test_is_alive_stale(self):
        """A node with no heartbeat for >2x timeout is NOT alive."""
        node = NodeHealth(node_id="test", last_heartbeat=time.time() - (JOB_TIMEOUT_S * 3))
        assert node.is_alive is False

    def test_is_alive_decommissioned(self):
        """A decommissioned node is never alive."""
        node = NodeHealth(
            node_id="test",
            last_heartbeat=time.time(),
            is_decommissioned=True,
        )
        assert node.is_alive is False

    def test_healthy_for_failover_empty_active(self):
        """A healthy, non-decommissioned node with no jobs is failover-ready."""
        node = NodeHealth(node_id="test", last_heartbeat=time.time())
        assert node.healthy_for_failover is True

    def test_healthy_for_failover_has_jobs(self):
        """A node with active jobs is NOT failover-ready (already busy)."""
        node = NodeHealth(
            node_id="test",
            last_heartbeat=time.time(),
            active_jobs=["job-1"],
        )
        assert node.healthy_for_failover is False

    def test_healthy_for_failover_decommissioned(self):
        """A decommissioned node is never failover-ready."""
        node = NodeHealth(
            node_id="test",
            last_heartbeat=time.time(),
            is_decommissioned=True,
        )
        assert node.healthy_for_failover is False


# ═══════════════════════════════════════════════════════════════
# JobRecord Tests
# ═══════════════════════════════════════════════════════════════


class TestJobRecord:
    def test_age_starts_at_zero(self):
        """A just-created job has age_s near 0."""
        job = JobRecord(job_id="j1", node_id="n1", started_at=time.time())
        assert job.age_s < 1.0

    def test_idle_s_tracks_heartbeat_gap(self):
        """idle_s grows with time since last heartbeat."""
        job = JobRecord(job_id="j1", node_id="n1", last_heartbeat=time.time() - 60)
        assert 58 <= job.idle_s <= 62

    def test_status_default(self):
        """Default status is running."""
        job = JobRecord(job_id="j1", node_id="n1")
        assert job.status == "running"


# ═══════════════════════════════════════════════════════════════
# NodeRegistry Tests
# ═══════════════════════════════════════════════════════════════


class TestNodeRegistry:
    def test_register_node(self, registry):
        """register_node creates a new NodeHealth entry."""
        registry.register_node(node_id="new-node", hostname="new.local", vram_total_mb=16384)
        node = registry.get_node("new-node")
        assert node is not None
        assert node.hostname == "new.local"
        assert node.vram_total_mb == 16384

    def test_register_duplicate_updates(self, registry):
        """Registering the same node_id updates fields without duplicating."""
        registry.register_node(node_id="worker-1", hostname="renamed.local")
        node = registry.get_node("worker-1")
        assert node.hostname == "renamed.local"
        # Original fields preserved
        assert node.vram_total_mb == 24576

    def test_record_heartbeat_resets_failures(self, registry):
        """A heartbeat resets consecutive_failures to 0."""
        node = registry.get_node("worker-1")
        node.consecutive_failures = 3
        registry.record_heartbeat("worker-1")
        assert node.consecutive_failures == 0
        assert node.is_alive

    def test_record_failure_increments(self, registry):
        """record_failure increments consecutive_failures."""
        registry.record_failure("worker-1")
        node = registry.get_node("worker-1")
        assert node.consecutive_failures == 1

    def test_record_failure_unknown_node(self, registry):
        """record_failure on unknown node returns None."""
        result = registry.record_failure("nonexistent")
        assert result is None

    def test_auto_decommission_after_max(self, registry):
        """After MAX_CONSECUTIVE_FAILURES failures, check_failures decommissions the node."""
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            registry.record_failure("worker-1")

        # Now run the decommission check
        from prismatic.distributed_watchdog import DistributedWatchdog
        wd = DistributedWatchdog(registry=registry)
        wd.check_failures()

        node = registry.get_node("worker-1")
        assert node.is_decommissioned is True
        assert node.decommissioned_at is not None
        assert node.consecutive_failures == MAX_CONSECUTIVE_FAILURES

    def test_recommission(self, registry):
        """recommission_node restores a decommissioned node."""
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            registry.record_failure("worker-1")
        wd = DistributedWatchdog(registry=registry)
        wd.check_failures()
        node = registry.get_node("worker-1")
        assert node.is_decommissioned

        registry.recommission_node("worker-1")
        assert node.is_decommissioned is False
        assert node.decommissioned_at is None
        assert node.consecutive_failures == 0

    def test_get_failover_targets(self, registry):
        """failover targets are alive, non-decommissioned, zero-active-job nodes."""
        # Give nodes recent heartbeats
        for nid in ["worker-1", "worker-2", "worker-3"]:
            registry.record_heartbeat(nid)

        targets = registry.get_failover_targets()
        assert len(targets) == 3

        # Assign a job to worker-1 — it should no longer be a failover target
        registry.register_job(job_id="j1", node_id="worker-1")
        targets = registry.get_failover_targets()
        assert len(targets) == 2
        assert all(n.node_id != "worker-1" for n in targets)

    def test_register_job_tracks_vram(self, registry):
        """register_job adds VRAM reservation to the node."""
        job = registry.register_job(
            job_id="j1", node_id="worker-1", vram_reserved_mb=4096,
        )
        assert job.job_id == "j1"
        node = registry.get_node("worker-1")
        assert node.vram_allocated_mb == 4096
        assert "j1" in node.active_jobs

    def test_complete_job_frees_vram(self, registry):
        """complete_job removes VRAM reservation and active job tracking."""
        registry.register_job(job_id="j1", node_id="worker-1", vram_reserved_mb=4096)
        registry.complete_job("j1")
        node = registry.get_node("worker-1")
        assert node.vram_allocated_mb == 0
        assert "j1" not in node.active_jobs

    def test_complete_job_unknown(self, registry):
        """complete_job on unknown job returns None."""
        result = registry.complete_job("nonexistent")
        assert result is None

    def test_heartbeat_job(self, registry):
        """heartbeat_job refreshes the job's last_heartbeat."""
        registry.register_job(job_id="j1", node_id="worker-1")
        old_hb = registry._jobs["j1"].last_heartbeat
        time.sleep(0.01)
        registry.heartbeat_job("j1")
        assert registry._jobs["j1"].last_heartbeat > old_hb

    def test_find_stale_jobs(self, registry):
        """find_stale_jobs returns jobs idle longer than JOB_TIMEOUT_S."""
        registry.register_job(
            job_id="stale-job", node_id="worker-1",
            issue_id="GRO-TIMEOUT",
        )
        # Set heartbeat to long ago
        registry._jobs["stale-job"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 10)

        stale = registry.find_stale_jobs()
        assert len(stale) == 1
        assert stale[0].job_id == "stale-job"

    def test_find_stale_jobs_ignore_completed(self, registry):
        """Completed jobs are not returned as stale."""
        registry.register_job(job_id="done-job", node_id="worker-1")
        registry._jobs["done-job"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 10)
        registry._jobs["done-job"].status = "completed"

        stale = registry.find_stale_jobs()
        assert len(stale) == 0

    def test_get_node_jobs(self, registry):
        """get_node_jobs returns only active jobs for the specified node."""
        registry.register_job(job_id="j1", node_id="worker-1")
        registry.register_job(job_id="j2", node_id="worker-1")
        registry.register_job(job_id="j3", node_id="worker-2")

        node1_jobs = registry.get_node_jobs("worker-1")
        assert len(node1_jobs) == 2
        assert all(j.node_id == "worker-1" for j in node1_jobs)

    def test_persistence(self, tmp_path):
        """NodeRegistry persists and restores state correctly."""
        state_path = tmp_path / "persist_test.json"
        r1 = NodeRegistry(state_path=state_path)
        r1.register_node(node_id="p-node", hostname="p.local", vram_total_mb=8192)
        r1.register_job(job_id="p-job", node_id="p-node", vram_reserved_mb=1024)
        r1.save()

        r2 = NodeRegistry(state_path=state_path)
        assert r2.get_node("p-node") is not None
        assert r2.get_node("p-node").hostname == "p.local"
        assert r2.get_node("p-node").vram_total_mb == 8192
        assert r2._jobs["p-job"] is not None

    def test_persistence_corrupt(self, tmp_path):
        """Corrupt state file doesn't crash the registry."""
        state_path = tmp_path / "corrupt.json"
        state_path.write_text("{{{{ not valid json }}}}")
        r = NodeRegistry(state_path=state_path)
        # Should load with empty state, not crash
        assert len(r.all_nodes()) == 0


# ═══════════════════════════════════════════════════════════════
# DistributedWatchdog Tests
# ═══════════════════════════════════════════════════════════════


class TestDistributedWatchdog:
    def test_sync_roster_from_file(self, watchdog, tmp_path):
        """sync_roster loads nodes from the roster file."""
        import prismatic.distributed_watchdog as dw

        roster_path = tmp_path / "swarm_nodes.json"
        original_roster = dw.NODE_ROSTER
        dw.NODE_ROSTER = roster_path

        roster = [
            {"node_id": "roster-node-1", "hostname": "r1.local", "vram_total_mb": 16384, "tags": {"gpu": "a100"}},
            {"node_id": "roster-node-2", "hostname": "r2.local", "vram_total_mb": 8192},
        ]
        with open(roster_path, "w") as f:
            json.dump(roster, f)

        count = watchdog.sync_roster()
        assert count == 2
        assert watchdog.registry.get_node("roster-node-1") is not None
        assert watchdog.registry.get_node("roster-node-2") is not None

        dw.NODE_ROSTER = original_roster

    def test_sync_roster_no_file(self, watchdog):
        """sync_roster with no roster file returns 0."""
        count = watchdog.sync_roster()
        assert count == 0

    def test_find_timeouts_returns_stale(self, watchdog):
        """find_timeouts returns jobs past JOB_TIMEOUT_S."""
        watchdog.registry.register_job(
            job_id="hung-job", node_id="worker-1",
            issue_id="GRO-HUNG",
        )
        watchdog.registry._jobs["hung-job"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 30)

        stale = watchdog.find_timeouts()
        assert len(stale) == 1
        assert stale[0].job_id == "hung-job"

    def test_cleanup_stale_marks_and_frees(self, watchdog):
        """cleanup_stale marks jobs timed_out, frees VRAM, increments fails."""
        watchdog.registry.register_job(
            job_id="stale-1", node_id="worker-1",
            vram_reserved_mb=2048,
        )
        watchdog.registry._jobs["stale-1"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 30)

        dirty_job = watchdog.registry._jobs["stale-1"]
        from prismatic.distributed_watchdog import JobRecord
        stale_jobs = [dirty_job]

        count = watchdog.cleanup_stale(stale_jobs)
        assert count == 1

        # Job is now timed_out
        assert watchdog.registry._jobs["stale-1"].status == "timed_out"

        # Node failure incremented
        node = watchdog.registry.get_node("worker-1")
        assert node.consecutive_failures == 1

        # VRAM freed
        assert node.vram_allocated_mb == 0

    def test_cleanup_stale_emits_actions(self, watchdog):
        """cleanup_stale increments the watchdog's action counter."""
        watchdog.registry.register_job(
            job_id="stale-1", node_id="worker-1",
        )
        watchdog.registry._jobs["stale-1"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 30)

        staler = watchdog.registry._jobs["stale-1"]
        count = watchdog.cleanup_stale([staler])
        assert watchdog.actions_taken() >= 1

    def test_check_failures_decommissions(self, watchdog):
        """check_failures decommissions nodes at threshold."""
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            watchdog.registry.record_failure("worker-1")

        decommissioned = watchdog.check_failures()
        assert "worker-1" in decommissioned

        node = watchdog.registry.get_node("worker-1")
        assert node.is_decommissioned

    def test_check_failures_below_threshold(self, watchdog):
        """check_failures does NOT decommission below threshold."""
        watchdog.registry.record_failure("worker-1")
        watchdog.registry.record_failure("worker-1")

        decommissioned = watchdog.check_failures()
        assert len(decommissioned) == 0

        node = watchdog.registry.get_node("worker-1")
        assert node.is_decommissioned is False

    def test_find_failover_candidates(self, watchdog):
        """find_failover_candidates returns healthy non-excluded nodes."""
        # Record recent heartbeats
        for nid in ["worker-1", "worker-2", "worker-3"]:
            watchdog.registry.record_heartbeat(nid)

        candidates = watchdog.find_failover_candidates(exclude_node_id="worker-1")
        assert len(candidates) == 2
        assert all(c.node_id != "worker-1" for c in candidates)
        assert any(c.node_id == "worker-2" for c in candidates)
        assert any(c.node_id == "worker-3" for c in candidates)

    def test_run_once_no_issues(self, watchdog):
        """run_once with no stale jobs or issues returns 0."""
        watchdog.registry.record_heartbeat("worker-1")
        watchdog.registry.record_heartbeat("worker-2")
        watchdog.registry.record_heartbeat("worker-3")

        actions = watchdog.run_once()
        assert actions == 0

    def test_run_once_with_stale_job(self, watchdog):
        """run_once detects and cleans up stale jobs."""
        watchdog.registry.register_job(
            job_id="stale-job", node_id="worker-1",
            vram_reserved_mb=1024,
        )
        watchdog.registry._jobs["stale-job"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 60)

        actions = watchdog.run_once()
        assert actions >= 1

        # Job should be cleaned up
        assert watchdog.registry._jobs["stale-job"].status == "timed_out"

    def test_run_once_decommissions(self, watchdog):
        """run_once decommissions nodes with too many failures."""
        watchdog.registry.register_job(
            job_id="fail-job", node_id="worker-1",
        )
        watchdog.registry._jobs["fail-job"].last_heartbeat = time.time() - (JOB_TIMEOUT_S + 60)

        # Manually set high failure count BEFORE run_once so the timeout
        # brings it over the threshold
        watchdog.registry.get_node("worker-1").consecutive_failures = MAX_CONSECUTIVE_FAILURES - 1

        actions = watchdog.run_once()
        assert actions >= 1

        node = watchdog.registry.get_node("worker-1")
        # Either already decommissioned or one more failure took it over
        assert node.consecutive_failures >= MAX_CONSECUTIVE_FAILURES

    def test_load_state_preserves_actions(self, watchdog, tmp_path):
        """load_state() reloads from file and resets action count."""
        watchdog.registry.save()
        watchdog._actions_taken = 42
        watchdog.load_state()
        assert watchdog.actions_taken() == 0

    def test_emit_heartbeat_does_not_crash(self, watchdog):
        """emit_heartbeat is best-effort and should not raise."""
        # Should not raise even without a running IPC bridge
        watchdog.emit_heartbeat()


# ═══════════════════════════════════════════════════════════════
# VRAM Marker Tests
# ═══════════════════════════════════════════════════════════════


class TestVRAMMarkers:
    def test_cleanup_marker_file(self, tmp_path):
        """Cleaning up a VRAM marker removes the file."""
        marker = tmp_path / "vram" / "node1_1234.vram"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"pid": 999999, "node_id": "node1", "mb": 2048}))

        assert marker.exists()
        result = _cleanup_vram_marker(marker)
        assert result is True
        assert not marker.exists()

    def test_cleanup_missing(self, tmp_path):
        """Cleaning a nonexistent marker returns True (idempotent)."""
        result = _cleanup_vram_marker(tmp_path / "nonexistent.vram")
        assert result is True


# ═══════════════════════════════════════════════════════════════
# Exception Tests
# ═══════════════════════════════════════════════════════════════


class TestExceptions:
    def test_node_decommission_error(self):
        """NodeDecommissionError carries node_id and failure count."""
        err = NodeDecommissionError("worker-1", 5)
        assert err.node_id == "worker-1"
        assert err.failures == 5
        assert "worker-1" in str(err)
        assert "5" in str(err)

    def test_timeout_error(self):
        """TimeoutError carries node_id, job_id, and age."""
        err = TimeoutError("worker-1", "job-abc", 150.5)
        assert err.node_id == "worker-1"
        assert err.job_id == "job-abc"
        assert err.age_s == 150.5
        assert "150" in str(err)
