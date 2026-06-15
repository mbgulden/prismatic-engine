"""tests/test_starvation.py — Distributed Starvation Regression Sandbox

Mock cluster environment and 8-scenario starvation test matrix covering
the distributed Plugin Hub architecture's failover, routing, and OOM
behavior across the 3-node cluster topology.

Architecture spec: specs/plugin-hub-architecture.md Section 6
"""

from __future__ import annotations

import time
import unittest
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════════


class NodeRole(Enum):
    LLM_INFERENCE = "llm_inference"
    MEDIA_RENDER = "media_render"
    MIXED = "mixed"


class PluginType(Enum):
    VEO_VIDEO = "veo_video"
    ASSET_FORGE_3D = "asset_forge_3d"
    LYRIA_AUDIO = "lyria_audio"
    LLM_INFERENCE = "llm_inference"


class NodeState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    OOM = "oom"
    RECOVERING = "recovering"


@dataclass
class ClusterNode:
    node_id: str
    hostname: str
    gpu_count: int
    vram_per_gpu_mb: int
    role: NodeRole
    state: NodeState = NodeState.HEALTHY
    consecutive_failures: int = 0
    active_jobs: list[str] = field(default_factory=list)
    vram_used_mb: int = 0

    @property
    def total_vram_mb(self) -> int:
        return self.gpu_count * self.vram_per_gpu_mb

    @property
    def free_vram_mb(self) -> int:
        return self.total_vram_mb - self.vram_used_mb


@dataclass
class Job:
    job_id: str
    plugin_type: PluginType
    vram_mb: int
    priority: str = "normal"  # realtime, high, normal, low
    _assigned_node: str | None = None


# ═══════════════════════════════════════════════════════════════════
# Mock Cluster Environment
# ═══════════════════════════════════════════════════════════════════


class MockClusterEnvironment:
    """In-memory mock of the 3-node GPU cluster topology.

    Topology:
        Node 1 — 4 × 24GB RTX 3090 (96GB) — LLM inference + fallback GPU 2
        PVE2  — 1 × 24GB RTX 3090          — Media rendering (VeoVideo)
        PVE3  — 1 × 24GB RTX 3090          — Media rendering (AssetForge3D)
    """

    def __init__(self):
        self.nodes: dict[str, ClusterNode] = {
            "node1": ClusterNode(
                node_id="node1",
                hostname="prism-host",
                gpu_count=4,
                vram_per_gpu_mb=24576,
                role=NodeRole.LLM_INFERENCE,
            ),
            "pve2": ClusterNode(
                node_id="pve2",
                hostname="pve2.tail023677.ts.net",
                gpu_count=1,
                vram_per_gpu_mb=24576,
                role=NodeRole.MEDIA_RENDER,
            ),
            "pve3": ClusterNode(
                node_id="pve3",
                hostname="pve3.tail023677.ts.net",
                gpu_count=1,
                vram_per_gpu_mb=24576,
                role=NodeRole.MEDIA_RENDER,
            ),
        }
        self.jobs: dict[str, Job] = {}
        self.routing_log: list[dict[str, Any]] = []
        self._job_counter = 0

    # ── State Mutators ───────────────────────────────────────────────

    def set_node_state(self, node_id: str, state: NodeState) -> None:
        """Set a node's operational state."""
        if node_id not in self.nodes:
            raise ValueError(f"Unknown node: {node_id}")
        self.nodes[node_id].state = state
        if state == NodeState.HEALTHY:
            self.nodes[node_id].consecutive_failures = 0

    def allocate_vram(self, node_id: str, vram_mb: int) -> bool:
        """Reserve VRAM on a node. Returns False if insufficient."""
        node = self.nodes[node_id]
        if node.free_vram_mb < vram_mb:
            return False
        node.vram_used_mb += vram_mb
        return True

    def release_vram(self, node_id: str, vram_mb: int) -> None:
        """Release VRAM on a node."""
        node = self.nodes[node_id]
        node.vram_used_mb = max(0, node.vram_used_mb - vram_mb)

    def clear_jobs(self, node_id: str) -> None:
        """Clear all active jobs from a node (simulating teardown)."""
        node = self.nodes[node_id]
        job_ids = list(node.active_jobs)
        for jid in job_ids:
            self.jobs.pop(jid, None)
        node.active_jobs.clear()
        node.vram_used_mb = 0

    # ── Routing Logic ────────────────────────────────────────────────

    def _node_is_usable(self, node_id: str) -> bool:
        """A node is usable if healthy and not OOM."""
        node = self.nodes[node_id]
        return node.state in (NodeState.HEALTHY, NodeState.RECOVERING)

    def select_target_node(
        self, plugin_type: PluginType, vram_mb: int = 14336
    ) -> str | None:
        """Route a plugin to the correct node, applying failover logic.

        Returns the node_id of the selected node, or None if no node can
        handle the job (starvation scenario).
        """
        # Determine preferred and fallback nodes per plugin type
        if plugin_type == PluginType.VEO_VIDEO:
            primary = "pve2"
            secondary = "pve3"
            tertiary = "node1"  # fallback GPU 2
        elif plugin_type == PluginType.ASSET_FORGE_3D:
            primary = "pve3"
            secondary = "pve2"
            tertiary = "node1"
        elif plugin_type == PluginType.LYRIA_AUDIO:
            primary = "pve3"
            secondary = "pve2"
            tertiary = "node1"
        elif plugin_type == PluginType.LLM_INFERENCE:
            primary = "node1"
            secondary = None
            tertiary = None
        else:
            raise ValueError(f"Unknown plugin type: {plugin_type}")

        # Check primary
        if primary and self._node_is_usable(primary):
            if self.allocate_vram(primary, vram_mb):
                self._log_routing(plugin_type, primary, "primary")
                return primary
            else:
                self.nodes[primary].consecutive_failures += 1

        # Check secondary (failover)
        if secondary and self._node_is_usable(secondary):
            if self.allocate_vram(secondary, vram_mb):
                self._log_routing(plugin_type, secondary, "failover")
                return secondary
            else:
                self.nodes[secondary].consecutive_failures += 1

        # Check tertiary (RED pressure fallback)
        if tertiary and self._node_is_usable(tertiary):
            # For LLM inference, never fall back to media nodes
            if plugin_type == PluginType.LLM_INFERENCE and tertiary != "node1":
                self._log_routing(plugin_type, None, "no_fallback_llm_protected")
                return None
            if self.allocate_vram(tertiary, vram_mb):
                self._log_routing(plugin_type, tertiary, "red_pressure_fallback")
                return tertiary
            else:
                self.nodes[tertiary].consecutive_failures += 1

        # Starvation — no node can handle it
        self._log_routing(plugin_type, None, "starvation")
        return None

    def simulate_network_timeout(self, node_id: str) -> None:
        """Mark a node as degraded after a simulated network timeout."""
        node = self.nodes[node_id]
        node.state = NodeState.DEGRADED
        node.consecutive_failures += 1
        self._log_routing(None, node_id, "network_timeout_degraded")

    def simulate_partial_recovery(self, node_id: str) -> None:
        """Bring a node back online mid-cycle."""
        node = self.nodes[node_id]
        node.state = NodeState.RECOVERING
        node.consecutive_failures = 0

    def trigger_oom_teardown(self, node_id: str) -> list[Job]:
        """Simulate OOM: clear all non-LLM jobs from a node.

        Returns the list of evicted jobs.
        """
        node = self.nodes[node_id]
        node.state = NodeState.OOM

        evicted: list[Job] = []
        remaining_jobs: list[str] = []

        for jid in node.active_jobs:
            job = self.jobs.get(jid)
            if job is None:
                continue
            # Preserve LLM inference jobs
            if job.plugin_type == PluginType.LLM_INFERENCE:
                remaining_jobs.append(jid)
            else:
                evicted.append(job)

        # Update active jobs
        node.active_jobs = remaining_jobs

        # Recalculate VRAM used from remaining LLM jobs
        node.vram_used_mb = sum(
            self.jobs[jid].vram_mb for jid in remaining_jobs if jid in self.jobs
        )

        self._log_routing(None, node_id, "oom_teardown", evicted_count=len(evicted))
        return evicted

    def create_job(self, plugin_type: PluginType, vram_mb: int = 14336) -> Job:
        """Create a job with a unique ID and try to route it."""
        self._job_counter += 1
        job = Job(
            job_id=f"job_{self._job_counter}",
            plugin_type=plugin_type,
            vram_mb=vram_mb,
        )
        target = self.select_target_node(plugin_type, vram_mb)
        if target:
            self.jobs[job.job_id] = job
            self.nodes[target].active_jobs.append(job.job_id)
            job._assigned_node = target
        return job

    def check_node_health(self, node_id: str) -> dict[str, Any]:
        """Return a health snapshot for a node."""
        node = self.nodes[node_id]
        return {
            "node_id": node.node_id,
            "state": node.state.value,
            "reachable": node.state in (NodeState.HEALTHY, NodeState.RECOVERING),
            "gpu_count": node.gpu_count,
            "vram_used_mb": node.vram_used_mb,
            "vram_free_mb": node.free_vram_mb,
            "vram_total_mb": node.total_vram_mb,
            "active_jobs": len(node.active_jobs),
            "consecutive_failures": node.consecutive_failures,
        }

    # ── Internals ────────────────────────────────────────────────────

    def _log_routing(
        self,
        plugin_type: PluginType | None,
        target: str | None,
        reason: str,
        **extra: Any,
    ) -> None:
        self.routing_log.append({
            "plugin": plugin_type.value if plugin_type else "none",
            "target": target,
            "reason": reason,
            **extra,
        })

    def last_routing_action(self) -> dict[str, Any] | None:
        return self.routing_log[-1] if self.routing_log else None


# ═══════════════════════════════════════════════════════════════════
# Starvation Regression Tests (8 scenarios)
# ═══════════════════════════════════════════════════════════════════


class StubDispatcher:
    """Minimal dispatcher stub that wraps MockClusterEnvironment for
    use in multi-scenario test assertions."""

    def __init__(self, env: MockClusterEnvironment):
        self.env = env

    def dispatch(self, plugin_type: PluginType, vram_mb: int = 14336) -> str | None:
        """Route a plugin job and return the target node or None."""
        return self.env.select_target_node(plugin_type, vram_mb)


class TestDistributedStarvation(unittest.TestCase):
    """8 regression scenarios from architecture spec Section 6."""

    maxDiff = None

    def setUp(self):
        self.env = MockClusterEnvironment()
        self.dispatcher = StubDispatcher(self.env)

    def _assert_routed_to(self, plugin_type: PluginType, expected_node: str) -> None:
        """Helper: dispatch a plugin and assert it routes to expected_node."""
        target = self.env.select_target_node(plugin_type)
        self.assertEqual(
            target,
            expected_node,
            f"{plugin_type.value} expected on {expected_node} but got {target}",
        )
        last = self.env.last_routing_action()
        assert last is not None, "No routing action recorded"

    def _assert_not_routed(self, plugin_type: PluginType) -> None:
        """Helper: dispatch a plugin and assert it routes to None (starvation)."""
        target = self.env.select_target_node(plugin_type)
        self.assertIsNone(target, f"{plugin_type.value} should have been starved")

    def test_01_healthy_cluster_veo_to_pve2(self) -> None:
        """Scenario 1: Healthy cluster — all 3 nodes up → VeoVideo routes to PVE2."""
        self._assert_routed_to(PluginType.VEO_VIDEO, "pve2")
        self.assertEqual(self.env.last_routing_action()["reason"], "primary")

    def test_02_healthy_cluster_forge_to_pve3(self) -> None:
        """Scenario 2: Healthy cluster — AssetForge3D routes to PVE3."""
        self._assert_routed_to(PluginType.ASSET_FORGE_3D, "pve3")
        self.assertEqual(self.env.last_routing_action()["reason"], "primary")

    def test_03_pve2_offline_veo_failover_to_pve3(self) -> None:
        """Scenario 3: PVE2 offline → VeoVideo fails over to PVE3."""
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self._assert_routed_to(PluginType.VEO_VIDEO, "pve3")
        self.assertEqual(self.env.last_routing_action()["reason"], "failover")

    def test_04_pve2_pve3_offline_red_pressure_fallback(self) -> None:
        """Scenario 4: PVE2 + PVE3 offline (RED pressure) → VeoVideo falls
        back to Node 1 GPU 2."""
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        self._assert_routed_to(PluginType.VEO_VIDEO, "node1")
        self.assertEqual(
            self.env.last_routing_action()["reason"], "red_pressure_fallback"
        )

    def test_05_llm_preserved_during_red_pressure(self) -> None:
        """Scenario 5: PVE2 + PVE3 offline → LLM inference on Node 1 is
        NOT evicted or re-routed. LLM stays on node1."""
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)

        # LLM inference routes to node1 (primary)
        target = self.env.select_target_node(PluginType.LLM_INFERENCE, vram_mb=24576)
        self.assertEqual(target, "node1")
        last = self.env.last_routing_action()
        assert last is not None
        self.assertEqual(last["reason"], "primary")

        # Fill node1 VRAM close to capacity (98304 MB total)
        # After LLM took 24576, free = 73728. Fill 65536 more, leaving 8192 free.
        # VeoVideo needs 14336 — insufficient → correctly starved
        node1_free_before = self.env.nodes["node1"].free_vram_mb
        fill_amount = node1_free_before - 8192  # leave 8192 MB free (less than 14336)
        self.env.allocate_vram("node1", fill_amount)

        # VeoVideo should be starved (insufficient VRAM on all nodes)
        target2 = self.env.select_target_node(PluginType.VEO_VIDEO, vram_mb=14336)
        self.assertIsNone(target2, "VeoVideo should be starved — insufficient VRAM")
        # If somehow it did land on node1, verify LLM wasn't evicted
        if target2 == "node1":
            node1 = self.env.nodes["node1"]
            llm_active = any(
                self.env.jobs.get(jid)
                and self.env.jobs[jid].plugin_type == PluginType.LLM_INFERENCE
                for jid in node1.active_jobs
            )
            self.assertTrue(
                llm_active,
                "LLM inference job was evicted from node1 during RED pressure",
            )

    def test_06_network_timeout_degrade(self) -> None:
        """Scenario 6: Network timeout mock — 15s SSH timeout → task
        re-queued, node marked degraded."""
        # Simulate a healthy dispatch to pve2
        target = self.env.select_target_node(PluginType.VEO_VIDEO)
        self.assertEqual(target, "pve2")

        # Simulate network timeout on pve2
        self.env.simulate_network_timeout("pve2")
        pve2_health = self.env.check_node_health("pve2")
        self.assertEqual(pve2_health["state"], "degraded")
        self.assertEqual(pve2_health["consecutive_failures"], 1)

        # Next dispatch should fail over to pve3 (pve2 is degraded)
        self._assert_routed_to(PluginType.VEO_VIDEO, "pve3")
        self.assertEqual(self.env.last_routing_action()["reason"], "failover")

    def test_07_partial_recovery_auto_resume(self) -> None:
        """Scenario 7: Partial recovery — PVE2 comes back online mid-cycle
        → auto-resume routing to PVE2."""
        # PVE2 goes offline
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self._assert_routed_to(PluginType.VEO_VIDEO, "pve3")
        self.assertEqual(self.env.last_routing_action()["reason"], "failover")

        # PVE2 recovers
        self.env.simulate_partial_recovery("pve2")
        pve2_health = self.env.check_node_health("pve2")
        self.assertEqual(pve2_health["state"], "recovering")
        self.assertEqual(pve2_health["consecutive_failures"], 0)

        # Next VeoVideo dispatch should go back to PVE2
        self._assert_routed_to(PluginType.VEO_VIDEO, "pve2")
        self.assertEqual(self.env.last_routing_action()["reason"], "primary")

    def test_08_oom_emergency_teardown(self) -> None:
        """Scenario 8: OOM imminent — emergency teardown of non-LLM
        workloads on Node 1. LLM inference is preserved."""
        node1 = self.env.nodes["node1"]

        # Load node1 with LLM inference
        llm_job = self.env.create_job(PluginType.LLM_INFERENCE, vram_mb=24576)
        self.assertIn(llm_job.job_id, node1.active_jobs)

        # Take PVE2 and PVE3 offline to force VeoVideo to node1
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        fallback_job = self.env.create_job(PluginType.VEO_VIDEO, vram_mb=14336)
        self.assertEqual(fallback_job._assigned_node, "node1")

        # Trigger OOM teardown on node1
        evicted = self.env.trigger_oom_teardown("node1")
        evicted_types = [j.plugin_type for j in evicted]

        # Non-LLM VEO_VIDEO job should be evicted
        self.assertIn(
            PluginType.VEO_VIDEO, evicted_types,
            "Media jobs should be evicted during OOM teardown",
        )

        # LLM job should still be on node1
        llm_still_active = any(
            self.env.jobs.get(jid)
            and self.env.jobs[jid].plugin_type == PluginType.LLM_INFERENCE
            for jid in node1.active_jobs
        )
        self.assertTrue(
            llm_still_active,
            "LLM inference job was evicted during OOM teardown — should be preserved",
        )

        # Verify node1 state is OOM
        health = self.env.check_node_health("node1")
        self.assertEqual(health["state"], "oom")


# ═══════════════════════════════════════════════════════════════════
# Multi-scenario Integration Tests
# ═══════════════════════════════════════════════════════════════════


class TestMultiScenarioFailover(unittest.TestCase):
    """End-to-end failover sequences combining multiple scenarios."""

    def setUp(self):
        self.env = MockClusterEnvironment()

    def test_sequential_failover_chain(self) -> None:
        """Verify the full failover chain: pve2 → pve3 → node1 → starvation."""
        # Step 1: VeoVideo → pve2 (primary)
        t1 = self.env.select_target_node(PluginType.VEO_VIDEO)
        self.assertEqual(t1, "pve2")

        # Step 2: pve2 goes offline → VeoVideo → pve3
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        t2 = self.env.select_target_node(PluginType.VEO_VIDEO)
        self.assertEqual(t2, "pve3")

        # Step 3: pve3 goes offline → VeoVideo → node1
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        t3 = self.env.select_target_node(PluginType.VEO_VIDEO)
        self.assertEqual(t3, "node1")

        # Step 4: fill node1 almost-full to trigger starvation for VeoVideo
        # After step 3, node1 has 14336 MB used. Node1 total = 98304 MB.
        # Fill all remaining VRAM: 98304 - 14336 = 83968
        remaining = self.env.nodes["node1"].free_vram_mb  # should be 98304 - 14336 = 83968
        self.env.allocate_vram("node1", remaining)  # fills node1 to 100%, free = 0
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        t4 = self.env.select_target_node(PluginType.VEO_VIDEO, vram_mb=14336)
        self.assertIsNone(t4, "Expected starvation — no node has free VRAM")

    def test_concurrent_node_failure(self) -> None:
        """Both PVE2 and PVE3 fail simultaneously — VeoVideo should go
        directly to node1."""
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        target = self.env.select_target_node(PluginType.VEO_VIDEO)
        self.assertEqual(target, "node1")
        last = self.env.last_routing_action()
        assert last is not None
        self.assertEqual(last["reason"], "red_pressure_fallback")

    def test_recovery_then_refailure(self) -> None:
        """PVE2 recovers, takes traffic, then fails again — failover to
        PVE3 should work after recovery (VRAM released between steps)."""
        # Start: pve2 offline → VeoVideo goes to pve3
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.assertEqual(self.env.select_target_node(PluginType.VEO_VIDEO), "pve3")

        # Clear VRAM from pve3 (simulating job completion) before pve2 recovers
        self.env.release_vram("pve3", 14336)

        # PVE2 recovers
        self.env.simulate_partial_recovery("pve2")
        self.env.set_node_state("pve2", NodeState.HEALTHY)
        self.assertEqual(self.env.select_target_node(PluginType.VEO_VIDEO), "pve2")

        # Clear VRAM from pve2 (job completed)
        self.env.release_vram("pve2", 14336)

        # PVE2 fails again → VeoVideo should go back to pve3
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.assertEqual(self.env.select_target_node(PluginType.VEO_VIDEO), "pve3")

    def test_asset_forge_pve2_fallback(self) -> None:
        """AssetForge3D's primary is PVE3; if PVE3 is down, fall back to
        PVE2 (not directly to node1)."""
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        target = self.env.select_target_node(PluginType.ASSET_FORGE_3D)
        self.assertEqual(target, "pve2")
        last = self.env.last_routing_action()
        assert last is not None
        self.assertEqual(last["reason"], "failover")

    def test_vram_booking_for_veo_prevents_overcommit(self) -> None:
        """Verify that VRAM booking prevents routing to a node with
        insufficient free VRAM."""
        # Fill node1 almost to capacity
        self.env.allocate_vram("node1", 86016)
        # VeoVideo needs 14336 MB — node1 has ~10000 MB free, insufficient
        self.env.set_node_state("pve2", NodeState.OFFLINE)
        self.env.set_node_state("pve3", NodeState.OFFLINE)
        target = self.env.select_target_node(PluginType.VEO_VIDEO, vram_mb=14336)
        self.assertIsNone(target, "Expected starvation — node1 has insufficient VRAM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
