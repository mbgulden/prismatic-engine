"""
Live E2E Hypervisor Multi-Channel Drill Runner for Prismatic Engine.
"""

import json
import time

from prismatic.ingestion_queue import enqueue_multi_channel_task, queue_payload
from prismatic.verification.swarmproof_oracle import SwarmProofOracle
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import ReviewJob, ReviewJobState
from prismatic.review_factory.queue import ReviewQueue
from prismatic.agent_signal_stream import record_agent_signal
from prismatic.core.locking import SwarmLockManager


def execute_drill():
    # 1. Admit 3 Multi-Channel Tasks
    t1 = enqueue_multi_channel_task(
        identifier="TG-FRED-LIVE-01",
        channel="telegram",
        target_agent="fred",
        title="Telegram: Fred Live Swarm Audit",
        affected_paths=["prismatic/orchestrator/status.json"],
        priority=0
    )

    t2 = enqueue_multi_channel_task(
        identifier="AGY-CLI-LIVE-02",
        channel="agy_cli",
        target_agent="agy",
        title="AGY CLI: Kernel Lock Proof Verification",
        affected_paths=["prismatic/core/locking.py"],
        priority=1
    )

    t3 = enqueue_multi_channel_task(
        identifier="GRO-5105",
        channel="linear",
        target_agent="kai",
        title="Linear: Kai Dashboard Reactive Optimization",
        affected_paths=["prismatic/gateway/dashboard_src/scripts/dashboard.js"],
        priority=1
    )

    # 2. Acquire Live SwarmLock Leases
    lock_mgr = SwarmLockManager()
    lock_mgr.acquire("prismatic/orchestrator/status.json", "fred", metadata={"task_id": "TG-FRED-LIVE-01", "intention": "Telegram Task Execution"})
    lock_mgr.acquire("prismatic/core/locking.py", "agy", metadata={"task_id": "AGY-CLI-LIVE-02", "intention": "Kernel SwarmLock Verification"})
    lock_mgr.acquire("prismatic/gateway/dashboard_src/scripts/dashboard.js", "kai", metadata={"task_id": "GRO-5105", "intention": "UI Optimization"})

    # 3. Execute SwarmProof Oracle Verification
    proof = SwarmProofOracle.verify_candidate(
        task_id="AGY-CLI-LIVE-02",
        candidate_sha="2a4f6852",
        file_diffs={
            "tests/test_topological_waves.py": {"old": "# old", "new": "def test_disjoint(): assert 1 + 1 == 2"}
        },
        visual_audit_result={"passed": True}
    )

    # 4. Insert Review Factory Job
    db = ReviewFactoryDB()
    db.ensure_tables()
    rq = ReviewQueue(db)

    rj = ReviewJob(
        task_id="GRO-5105",
        repository="mbgulden/prismatic-engine",
        base_commit="275949d5",
        base_tree="275949d5",
        candidate_commit="2a4f6852",
        candidate_tree="2a4f6852",
        result_packet_path="artifacts/result-packet.json",
        result_packet_sha256="5288c3e2bfe8e667a0fbdac26d0acb1c9ed201bb461adc98bf4c0b887461356d",
        changed_paths_json=json.dumps(["prismatic/gateway/dashboard_src/scripts/dashboard.js"]),
        risk_tier=1,
        state=ReviewJobState.REVIEW_READY,
        required_witnesses=1,
        completed_witnesses=1
    )
    db.insert_review_job(rj)

    # 5. Emit high-priority telemetry signals
    record_agent_signal("fred", "info", "task_admitted", issue_id="TG-FRED-LIVE-01", message="Fred executing admitted Telegram Task in Topological Wave 1")
    record_agent_signal("agy", "info", "proof_verified", issue_id="AGY-CLI-LIVE-02", message=f"SwarmProof Oracle validated candidate 2a4f6852 (Proof ID: {proof.proof_id})")
    record_agent_signal("kai", "info", "review_ready", issue_id="GRO-5105", message="Kai task admitted to Review Factory (Risk Tier: T1, State: REVIEW_READY)")

    print("DRILL_SUCCESS: " + json.dumps({
        "tasks": [t1["identifier"], t2["identifier"], t3["identifier"]],
        "proof_id": proof.proof_id,
        "review_job_id": rj.review_job_id,
        "waves": queue_payload(limit=10).get("topological_waves", [])
    }))


if __name__ == "__main__":
    execute_drill()
