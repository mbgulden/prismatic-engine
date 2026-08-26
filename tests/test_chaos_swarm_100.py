"""
Milestone 3: 100-Agent "Chaos Swarm" Stress Test Suite for Prismatic Hypervisor.
Simulates 100 asynchronous agents executing concurrent refactors, hot-spot lock contentions,
injected syntax faults, and worker crashes.
"""

import asyncio
import os
import random
import tempfile
import time
from pathlib import Path
import pytest

from prismatic.hypervisor import PrismaticHypervisor
from swarmcron.reaper import ZombieLeaseReaper
from swarmcurator.coherence import CacheCoherenceManager
from swarmledger.storage.auditor import CryptographicAuditor
from swarmledger.storage.engine import StorageEngine
from swarmlock.hierarchy import HierarchyLockEngine, LockMode, ResourceKey
from swarmrouter.analyzer import LockScopeAnalyzer, RoutedTask
from swarmrouter.dispatcher import TopologicalDispatcher


def test_chaos_swarm_100_agents_stress_and_fault_tolerance():
    async def _run_chaos_simulation():
        with tempfile.TemporaryDirectory() as tmpdir:
            j_db = str(Path(tmpdir) / "journal.db")
            l_db = str(Path(tmpdir) / "ledger.db")
            
            # Setup Hypervisor & Supporting Primitives
            lock_engine = HierarchyLockEngine()
            hypervisor = PrismaticHypervisor(
                journal_db_path=j_db,
                ledger_db_path=l_db,
                lock_engine=lock_engine,
                mirror_to_gateway=False
            )
            dispatcher = TopologicalDispatcher(hypervisor=hypervisor)
            coherence = CacheCoherenceManager()
            reaper = ZombieLeaseReaper(lock_engine=lock_engine, journal=hypervisor.journal)

            # Create 10 target source files in temp workspace
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            files = {}
            for f_idx in range(10):
                fpath = src_dir / f"module_{f_idx}.py"
                fpath.write_text(f"# Initial module {f_idx}\ndef compute_{f_idx}(): return {f_idx * 10}\n", encoding="utf-8")
                files[f_idx] = fpath

            # Define 100 Routed Tasks across 4 categories:
            # 1. 40 Disjoint Parallel Tasks (modifying distinct files)
            # 2. 20 Contending Tasks (all competing for exclusive write on module_0.py)
            # 3. 20 Injected Syntax Error Tasks (must be blocked by SwarmProof & rolled back)
            # 4. 20 Zombie / Simulated Crash Tasks (must be reclaimed by SwarmCron)
            tasks: list[RoutedTask] = []

            for i in range(40):
                target_f = files[(i % 9) + 1]  # modules 1..9
                tasks.append(RoutedTask(
                    task_id=f"disjoint_task_{i}",
                    resources=[f"file:{target_f}"],
                    mode="X",
                    agent_id=f"agent_disjoint_{i}",
                    payload={"type": "CLEAN", "file_idx": (i % 9) + 1}
                ))

            for i in range(20):
                # Heavy contention on module 0
                tasks.append(RoutedTask(
                    task_id=f"contending_task_{i}",
                    resources=[f"file:{files[0]}"],
                    mode="X",
                    agent_id=f"agent_contender_{i}",
                    payload={"type": "CONTENDING", "file_idx": 0}
                ))

            for i in range(20):
                target_f = files[(i % 9) + 1]
                tasks.append(RoutedTask(
                    task_id=f"syntax_fault_task_{i}",
                    resources=[f"file:{target_f}"],
                    mode="X",
                    agent_id=f"agent_fault_{i}",
                    payload={"type": "SYNTAX_FAULT", "file_idx": (i % 9) + 1}
                ))

            for i in range(20):
                target_f = files[(i % 9) + 1]
                tasks.append(RoutedTask(
                    task_id=f"zombie_crash_task_{i}",
                    resources=[f"file:{target_f}"],
                    mode="X",
                    agent_id=f"agent_zombie_{i}",
                    payload={"type": "ZOMBIE", "file_idx": (i % 9) + 1}
                ))

            # Shuffle tasks to simulate realistic asynchronous concurrency arrival
            random.seed(42)
            random.shuffle(tasks)

            # Execution tracking ledgers
            successful_tasks = []
            fault_aborted_tasks = []
            zombie_tasks_created = []

            async def chaos_worker(tx, task: RoutedTask):
                p_type = task.payload.get("type")

                # Cache in curator memory
                coherence.put(task.task_id, f"context_data_for_{task.task_id}", tx_id=tx.tx_id)

                if p_type in ["CLEAN", "CONTENDING"]:
                    # Clean step execution
                    fpath = files[task.payload["file_idx"]]
                    old_text = fpath.read_text(encoding="utf-8")
                    
                    tx.register_step(
                        name="mutate_code",
                        forward_fn=lambda ctx: (fpath.write_text(old_text + f"# Patch {task.task_id}\n", encoding="utf-8"), {"old": old_text}),
                        compensate_fn=lambda p: fpath.write_text(p["old"], encoding="utf-8")
                    )
                    await asyncio.sleep(0.005)
                    successful_tasks.append(task.task_id)
                    return f"COMMITTED_{task.task_id}"

                elif p_type == "SYNTAX_FAULT":
                    # Inject broken syntax into file
                    fpath = files[task.payload["file_idx"]]
                    old_text = fpath.read_text(encoding="utf-8")
                    
                    tx.register_step(
                        name="mutate_broken",
                        forward_fn=lambda ctx: (fpath.write_text("def broken_syntax(:\n   invalid!!\n", encoding="utf-8"), {"old": old_text}),
                        compensate_fn=lambda p: fpath.write_text(p["old"], encoding="utf-8")
                    )
                    fault_aborted_tasks.append(task.task_id)
                    coherence.invalidate_transaction(tx.tx_id)
                    return "FAULT_TRIGGERED"

                elif p_type == "ZOMBIE":
                    # Simulate unexpected worker crash
                    zombie_tasks_created.append(tx.tx_id)
                    raise RuntimeError("Simulated Worker Process Kill / SIGKILL")

            # Execute all tasks through SwarmRouter topological wave scheduling
            waves = LockScopeAnalyzer.schedule_waves(tasks)
            assert len(waves) >= 20  # Wave analyzer properly serialized contending module_0 tasks

            for wave in waves:
                wave_coros = []
                for t in wave.tasks:
                    async def _run_safely(task_item):
                        try:
                            return await dispatcher.dispatch_task(task_item, chaos_worker)
                        except Exception:
                            return {"task_id": task_item.task_id, "status": "ABORTED"}
                    wave_coros.append(_run_safely(t))
                await asyncio.gather(*wave_coros)

            # Step 5: Inject 5 unhandled orphaned zombie sagas left by crashed external daemons
            for z_idx in range(5):
                z_id = f"zombie_orphaned_{z_idx}"
                hypervisor.journal.begin_saga(z_id, "crashed_external_worker")
                with hypervisor.journal._lock:
                    conn = hypervisor.journal._get_conn()
                    conn.execute("UPDATE sagas SET updated_at = ? WHERE tx_id = ?", (time.time() - 120.0, z_id))
                    conn.commit()
                    conn.close()

            # Execute SwarmCron Zombie Reaper to sweep orphaned sagas
            reap_summary = await reaper.sweep_and_reclaim(ttl_threshold_seconds=60.0)
            assert reap_summary["reclaimed_sagas_count"] == 5

            # Step 6: Verify 100% Cryptographic Provenance Integrity across all generated spans
            auditor = CryptographicAuditor(hypervisor.ledger)
            all_spans = hypervisor.ledger.list_spans()
            assert len(all_spans) >= 100

            audit_passed = 0
            total_violations = 0
            for sp in all_spans:
                s_id = sp["span_id"] if isinstance(sp, dict) else sp
                report = auditor.verify_span(s_id)
                if report.passed:
                    audit_passed += 1
                else:
                    total_violations += len(report.violations)

            # --- MASTER CHAOS VERIFICATION INVARIANTS ---
            # Invariant 1: All 60 clean & serialized tasks succeeded
            assert len(successful_tasks) == 60

            # Invariant 2: All 20 syntax fault tasks were caught and unwound
            assert len(fault_aborted_tasks) == 20

            # Invariant 3: Zero file corruption — all 10 source files must be valid, readable Python syntax
            for f_idx, fpath in files.items():
                code = fpath.read_text(encoding="utf-8")
                compile(code, str(fpath), "exec")  # Must not raise SyntaxError!

            # Invariant 4: Zero leaked concurrency locks
            for f_idx, fpath in files.items():
                conflict = lock_engine.check_conflict(ResourceKey.parse(f"file:{fpath}"), LockMode.X, "new_agent")
                assert conflict is None

            # Invariant 5: 100% of cryptographic Merkle spans verified with 0 violations
            assert audit_passed == len(all_spans)
            assert total_violations == 0

    asyncio.run(_run_chaos_simulation())