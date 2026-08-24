"""
Prismatic Unified Agent Hypervisor Kernel Pipeline.
Orchestrates SwarmLock (concurrency), SwarmSaga (transactions), SwarmProof (verification),
SwarmGate (attention governance), and SwarmLedger (cryptographic provenance) into a single execution loop.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from swarmgate.bridge import PendingDecisionStore, SwarmgateBridge
from swarmgate.distiller import DecisionDiffDistiller
from swarmgate.evaluator import EscalationEvaluator
from swarmgate.schemas import AttentionTier, DecisionPacket, DecisionStatus
from swarmledger.core.node import EventType, LedgerNode
from swarmledger.storage.auditor import CryptographicAuditor
from swarmledger.storage.engine import StorageEngine
from swarmlock.hierarchy import HierarchyLockEngine, LockMode, ResourceKey
from swarmproof.bridge import SwarmproofBridge
from swarmsaga.core.coordinator import SagaCoordinator
from swarmsaga.core.step import Step
from swarmsaga.core.unwinder import TopologicalUnwinder
from swarmsaga.journal.engine import JournalEngine

logger = logging.getLogger("prismatic.hypervisor")


@dataclass
class HypervisorTransactionContext:
    tx_id: str
    span_id: str
    agent_id: str
    resource: str
    mode: str
    fence_token: int
    version: int
    lock_id: str
    journal: JournalEngine
    ledger: StorageEngine
    coordinator: SagaCoordinator
    lock_engine: HierarchyLockEngine
    gate_evaluator: EscalationEvaluator
    steps_executed: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def register_step(
        self,
        name: str,
        forward_fn: Callable[[Dict[str, Any]], Tuple[Any, Dict[str, Any]]],
        compensate_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
        dependencies: Optional[List[str]] = None
    ) -> None:
        """Registers a forward DAG step and its corresponding inverse compensation."""
        step = Step(
            name=name,
            forward_handler=forward_fn,
            compensate_handler=compensate_fn,
            dependencies=dependencies or []
        )
        self.coordinator.add_step(step)
        self.steps_executed.append(name)


class PrismaticHypervisor:
    """
    Unified execution hypervisor managing the complete 5-primitive lifecycle.
    """

    def __init__(
        self,
        journal_db_path: Optional[str | Path] = None,
        ledger_db_path: Optional[str | Path] = None,
        lock_engine: Optional[HierarchyLockEngine] = None
    ):
        self.journal = JournalEngine(db_path=journal_db_path)
        self.ledger = StorageEngine(db_path=ledger_db_path)
        self.lock_engine = lock_engine or HierarchyLockEngine()
        self.gate_evaluator = EscalationEvaluator()
        self.auditor = CryptographicAuditor(self.ledger)

    @asynccontextmanager
    async def transaction(
        self,
        resource: str,
        agent_id: str = "prismatic_agent",
        mode: str = "X",
        span_id: Optional[str] = None,
        tx_id: Optional[str] = None,
        task_id: Optional[str] = None,
        task_title: Optional[str] = None,
        expected_version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        span_id = span_id or f"span_{uuid.uuid4().hex[:8]}"
        tx_id = tx_id or f"tx_{uuid.uuid4().hex[:8]}"
        lock_id = f"lck_{uuid.uuid4().hex[:8]}"
        res_key = ResourceKey.parse(resource)
        lock_mode = getattr(LockMode, mode, LockMode.X)
        fence_token = time.time_ns()

        # Step 1: Concurrency Acquisition with Monotonic Fencing Token (SwarmLock)
        granted, conflict_lock, current_version = self.lock_engine.acquire_lock(
            lock_id=lock_id,
            holder=agent_id,
            resource=res_key,
            mode=lock_mode,
            fence_token=fence_token,
            ttl_seconds=60.0
        )

        if not granted:
            held_by = conflict_lock.holder if conflict_lock else "unknown"
            raise RuntimeError(f"ConcurrencyConflict: Resource {resource} currently locked by {held_by}")

        if expected_version is not None and current_version != expected_version:
            self.lock_engine.release_lock(lock_id=lock_id, holder=agent_id, resource=res_key)
            raise RuntimeError(f"StaleReadConflict: Resource {resource} version {current_version} != expected {expected_version}")

        # Step 2: Initialize Saga Journal & Merkle DAG Span (SwarmSaga & SwarmLedger)
        self.journal.begin_saga(tx_id, f"Transaction on {resource} by {agent_id}")
        coordinator = SagaCoordinator(journal=self.journal)

        n_root = self.ledger.append_node(
            span_id=span_id,
            event_type=EventType.MUTATE,
            agent_id=agent_id,
            payload={
                "tx_id": tx_id,
                "resource": resource,
                "mode": mode,
                "fence_token": fence_token,
                "task_id": task_id
            }
        )

        ctx = HypervisorTransactionContext(
            tx_id=tx_id,
            span_id=span_id,
            agent_id=agent_id,
            resource=resource,
            mode=mode,
            fence_token=fence_token,
            version=current_version,
            lock_id=lock_id,
            journal=self.journal,
            ledger=self.ledger,
            coordinator=coordinator,
            lock_engine=self.lock_engine,
            gate_evaluator=self.gate_evaluator,
            metadata=metadata or {}
        )

        try:
            yield ctx

            # Step 3: Run Forward DAG Execution
            if ctx.coordinator._steps:
                results = await ctx.coordinator.execute(tx_id=tx_id)

            # Step 4: Deterministic Multi-Oracle Verification Barrier (SwarmProof)
            if resource.startswith("file:"):
                fpath = resource.split("file:", 1)[1]
                if Path(fpath).exists():
                    passed, cert, diags = SwarmproofBridge.verify_and_settle(
                        target_file=Path(fpath),
                        tx_id=tx_id,
                        holder=agent_id,
                        run_tests=False
                    )
                    if not passed:
                        raise RuntimeError(f"SwarmProof Invariant Failure: {[f'{d.error_type}: {d.message}' for d in diags]}")

                    if cert:
                        self.ledger.append_node(
                            span_id=span_id,
                            event_type=EventType.PROOF,
                            agent_id=agent_id,
                            payload={"proof_id": cert.proof_id, "ast_checksum": cert.ast_checksum},
                            parent_node_ids=[n_root.node_id]
                        )

            # Step 5: Attention Governor Evaluation (SwarmGate)
            decision = self.gate_evaluator.evaluate(
                resource=resource,
                agent_id=agent_id,
                metadata=metadata
            )
            decision.task_id = task_id
            decision.task_title = task_title

            if decision.tier == AttentionTier.TIER_3_BARRIER:
                # Suspend and queue for human sign-off
                card = DecisionDiffDistiller.distill(decision, "(Code mutation)")
                PendingDecisionStore.add(decision, card)
                logger.info("Transaction %s suspended for Tier 3 human approval (%s)", tx_id, decision.decision_id)

            # Step 6: Commit Transaction & Seal in Cryptographic Merkle DAG (SwarmLedger)
            self.ledger.append_node(
                span_id=span_id,
                event_type=EventType.COMMIT,
                agent_id=agent_id,
                payload={"tx_id": tx_id, "status": "COMMITTED", "fence_token": fence_token},
                parent_node_ids=[n_root.node_id]
            )
            self.journal.finalize_saga(tx_id, "COMMITTED")
            self.lock_engine.increment_version_and_propagate(res_key)

        except Exception as exc:
            logger.error("Transaction %s failed (%s). Triggering reverse compensation rollback...", tx_id, exc)
            
            # Step 7: Automatic Backward Topological Rollback & DLQ Fault Isolation (SwarmSaga)
            try:
                unwinder = TopologicalUnwinder(journal=self.journal)
                step_handlers = {name: s.compensate_handler for name, s in ctx.coordinator._steps.items() if s.compensate_handler}
                await unwinder.unwind(tx_id=tx_id, step_handlers=step_handlers)
            except Exception as unwind_err:
                logger.error("Error during unwinding: %s", unwind_err)

            self.ledger.append_node(
                span_id=span_id,
                event_type=EventType.ABORT,
                agent_id=agent_id,
                payload={"tx_id": tx_id, "status": "ABORTED", "error": str(exc)},
                parent_node_ids=[n_root.node_id]
            )
            raise exc

        finally:
            # Release Held Leases
            self.lock_engine.release_lock(lock_id=lock_id, holder=agent_id, resource=res_key)

    def action(self, resource_extractor: Callable[..., str], mode: str = "X"):
        """Decorator wrapper binding functions to the unified hypervisor transaction lifecycle."""
        def decorator(func: Callable):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                resource = resource_extractor(*args, **kwargs)
                async with self.transaction(resource=resource, mode=mode) as tx:
                    return await func(tx, *args, **kwargs)
            return wrapper
        return decorator