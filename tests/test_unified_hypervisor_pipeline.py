"""
Unit and Integration Test Suite for Prismatic Unified Agent Hypervisor Pipeline.
Validates the orchestration of SwarmLock, SwarmSaga, SwarmProof, SwarmGate, and SwarmLedger.
"""

import asyncio
import tempfile
from pathlib import Path
import pytest

from prismatic.hypervisor import PrismaticHypervisor
from swarmlock.hierarchy import HierarchyLockEngine, LockMode, ResourceKey


def test_unified_pipeline_happy_path():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            j_db = str(Path(tmpdir) / "journal.db")
            l_db = str(Path(tmpdir) / "ledger.db")
            
            target_file = Path(tmpdir) / "service.py"
            target_file.write_text("def run(): return 42\n", encoding="utf-8")
            
            res = f"file:{target_file}"
            hypervisor = PrismaticHypervisor(journal_db_path=j_db, ledger_db_path=l_db)
            
            forward_log = []
            comp_log = []

            async with hypervisor.transaction(
                resource=res,
                agent_id="refactor_bot",
                task_id="GRO-3319",
                task_title="Refactor Service Logic"
            ) as tx:
                assert tx.fence_token > 0
                assert tx.version == 1

                # Register Step 1
                tx.register_step(
                    name="step_1_backup",
                    forward_fn=lambda ctx: (forward_log.append("backup_created"), {"backup": "v1"}),
                    compensate_fn=lambda p: comp_log.append("backup_restored")
                )

                # Register Step 2 (dependent on Step 1)
                tx.register_step(
                    name="step_2_mutate",
                    forward_fn=lambda ctx: (forward_log.append("file_mutated"), {"status": "ok"}),
                    compensate_fn=lambda p: comp_log.append("file_reverted"),
                    dependencies=["step_1_backup"]
                )

            # Assertions after successful commit
            assert forward_log == ["backup_created", "file_mutated"]
            assert comp_log == []  # No rollback on success
            
            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "COMMITTED"

            # Assert zero-trust Merkle ledger audit
            audit_report = hypervisor.auditor.verify_span(tx.span_id)
            assert audit_report.passed is True
            assert audit_report.verified_nodes >= 2
            assert len(audit_report.violations) == 0

    asyncio.run(_run())


def test_unified_pipeline_failure_triggers_automatic_rollback():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            j_db = str(Path(tmpdir) / "journal.db")
            l_db = str(Path(tmpdir) / "ledger.db")
            
            hypervisor = PrismaticHypervisor(journal_db_path=j_db, ledger_db_path=l_db)
            res = "file:src/billing/checkout.py"
            
            forward_log = []
            comp_log = []

            with pytest.raises((RuntimeError, ValueError), match="Simulated payment failure"):
                async with hypervisor.transaction(resource=res, agent_id="billing_bot") as tx:
                    tx.register_step(
                        name="step_1_hold",
                        forward_fn=lambda ctx: (forward_log.append("hold_acquired"), {"hold_id": "h_100"}),
                        compensate_fn=lambda p: comp_log.append(f"hold_canceled_{p['hold_id']}")
                    )
                    tx.register_step(
                        name="step_2_failing_charge",
                        forward_fn=lambda ctx: (_ for _ in ()).throw(ValueError("Simulated payment failure")),
                        compensate_fn=lambda p: comp_log.append("charge_refunded"),
                        dependencies=["step_1_hold"]
                    )

            # Assertions on failure: Step 1 was cleanly compensated in reverse order
            assert forward_log == ["hold_acquired"]
            assert comp_log == ["charge_refunded", "hold_canceled_h_100"]

            # Saga is marked ABORTED
            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "ABORTED"

            # Lock was fully released
            conflict = hypervisor.lock_engine.check_conflict(ResourceKey.parse(res), LockMode.X, "other_bot")
            assert conflict is None

    asyncio.run(_run())


def test_unified_pipeline_syntax_error_blocked_by_swarmproof():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            j_db = str(Path(tmpdir) / "journal.db")
            l_db = str(Path(tmpdir) / "ledger.db")
            
            broken_file = Path(tmpdir) / "bad_syntax.py"
            broken_file.write_text("def broken_syntax(:\n    pass\n", encoding="utf-8")
            
            res = f"file:{broken_file}"
            hypervisor = PrismaticHypervisor(journal_db_path=j_db, ledger_db_path=l_db)
            comp_called = []

            with pytest.raises(RuntimeError, match="SwarmProof Invariant Failure"):
                async with hypervisor.transaction(resource=res, agent_id="syntax_checker") as tx:
                    tx.register_step(
                        name="step_1",
                        forward_fn=lambda ctx: ({"done": True}, {"undo": "ok"}),
                        compensate_fn=lambda p: comp_called.append("step_1_undone")
                    )

            assert comp_called == ["step_1_undone"]
            assert hypervisor.journal.get_saga(tx.tx_id)["state"] == "ABORTED"

    asyncio.run(_run())


def test_unified_action_decorator():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            j_db = str(Path(tmpdir) / "journal.db")
            l_db = str(Path(tmpdir) / "ledger.db")
            
            hypervisor = PrismaticHypervisor(journal_db_path=j_db, ledger_db_path=l_db)

            @hypervisor.action(resource_extractor=lambda file_path: f"file:{file_path}", mode="X")
            async def refactor_endpoint(tx, file_path: str):
                tx.register_step("edit", lambda ctx: ("mutated_result", {}))
                return "SUCCESS_ENDPOINT"

            res = await refactor_endpoint(str(Path(tmpdir) / "api.py"))
            assert res == "SUCCESS_ENDPOINT"

    asyncio.run(_run())