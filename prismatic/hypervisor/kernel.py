"""Prismatic Unified Agent Hypervisor Kernel Pipeline.

Orchestrates SwarmLock (concurrency), SwarmSaga (transactions), SwarmProof (verification),
SwarmGate (attention governance), and SwarmLedger (cryptographic provenance) into a single execution loop.

Portability
-----------
Every path this kernel touches resolves portably; there are no machine-specific
addresses, credentials, or OS-specific locations:

- Journal / ledger databases default to ``~/.swarmsaga`` / ``~/.swarmledger``
  (overridable per constructor argument).
- The gateway lease mirror writes to the shared file-based lock registry at
  ``$PRISMATIC_HOME/.antigravity/swarm_locks.json`` (``PRISMATIC_HOME`` unset
  falls back to ``~``). Mirroring is best-effort observability and never gates
  correctness, so a stranger's install works with zero setup.
- Agent signals go to ``$PRISMATIC_STATE_DIR`` or ``./prismatic_state``.
- Tier-3 barrier decisions queue in ``~/.swarmgate/pending_decisions.json``.
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from prismatic.agent_signal_stream import record_agent_signal
from prismatic.lock import _get_lock_manager
from swarmgate.bridge import PendingDecisionStore
from swarmgate.distiller import DecisionDiffDistiller
from swarmgate.evaluator import EscalationEvaluator
from swarmgate.schemas import AttentionTier
from swarmledger.core.node import EventType
from swarmledger.storage.auditor import CryptographicAuditor
from swarmledger.storage.engine import StorageEngine
from swarmlock.hierarchy import HierarchyLockEngine, LockMode, ResourceKey
from swarmproof.bridge import SwarmproofBridge
from swarmsaga.core.coordinator import SagaCoordinator
from swarmsaga.core.step import Step
from swarmsaga.core.unwinder import TopologicalUnwinder
from swarmsaga.journal.engine import JournalEngine

logger = logging.getLogger("prismatic.hypervisor")


def _require_non_empty_str(name: str, value: Any) -> str:
    """Validate an identifier-style argument; fail fast on wrong/blank input."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


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
        dependencies: Optional[List[str]] = None,
    ) -> None:
        """Registers a forward DAG step and its corresponding inverse compensation."""
        step = Step(
            name=name,
            forward_handler=forward_fn,
            compensate_handler=compensate_fn,
            dependencies=dependencies or [],
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
        lock_engine: Optional[HierarchyLockEngine] = None,
        mirror_to_gateway: bool = True,
    ):
        self.journal = JournalEngine(db_path=journal_db_path)
        self.ledger = StorageEngine(db_path=ledger_db_path)
        self.lock_engine = lock_engine or HierarchyLockEngine()
        self.gate_evaluator = EscalationEvaluator()
        self.auditor = CryptographicAuditor(self.ledger)
        self.mirror_to_gateway = mirror_to_gateway

    def _mirror_lease_acquire(
        self,
        resource: str,
        agent_id: str,
        tx_id: str,
        span_id: str,
        task_id: Optional[str],
        task_title: Optional[str],
    ) -> bool:
        """Best-effort mirror of the SwarmLock lease to the gateway manager.

        Fail-open by design: mirroring is observability, never a correctness gate.
        Returns True when the mirror lease was acquired (so it can be released).
        """
        try:
            mgr = _get_lock_manager()
            mirrored = mgr.acquire(
                resource_id=resource,
                agent_id=agent_id,
                metadata={
                    "intention": task_title or f"Hypervisor Transaction ({tx_id})",
                    "task_id": task_id or "",
                    "tx_id": tx_id,
                    "span_id": span_id,
                },
            )
            if not mirrored:
                # The registry explicitly denied the mirror lease (it is held
                # by someone else): do not emit a success signal and do not
                # mark it mirrored, so we never release a lease we don't hold.
                logger.debug(
                    "Gateway mirror denied lease for %s; skipping mirror", resource
                )
                return False
            record_agent_signal(
                agent=agent_id.lower(),
                severity="lease",
                event_type="lock_acquired",
                issue_id=task_id or "",
                message=f"{agent_id} acquired lease on {resource} for {task_title or tx_id}",
            )
            return True
        except Exception as e:
            logger.debug("Mirroring lock to gateway manager failed: %s", e)
            return False

    def _mirror_lease_release(
        self, resource: str, agent_id: str, task_id: Optional[str]
    ) -> None:
        """Best-effort release of a mirrored gateway lease (fail-open)."""
        try:
            mgr = _get_lock_manager()
            mgr.release(resource_id=resource, agent_id=agent_id)
            record_agent_signal(
                agent=agent_id.lower(),
                severity="lease",
                event_type="lock_released",
                issue_id=task_id or "",
                message=f"{agent_id} released lease on {resource}",
            )
        except Exception as e:
            logger.debug("Mirroring release to gateway manager failed: %s", e)

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
        metadata: Optional[Dict[str, Any]] = None,
        ttl_seconds: float = 60.0,
    ):
        # Fail fast on bad input: a blank resource or agent id would otherwise
        # produce confusing downstream errors (or lock the wrong thing).
        _require_non_empty_str("resource", resource)
        _require_non_empty_str("agent_id", agent_id)
        for _name, _value in (
            ("span_id", span_id),
            ("tx_id", tx_id),
            ("task_id", task_id),
            ("task_title", task_title),
        ):
            if _value is not None:
                _require_non_empty_str(_name, _value)
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError(f"metadata must be a dict, got {type(metadata).__name__}")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not ttl_seconds > 0
        ):
            raise ValueError(
                f"ttl_seconds must be a positive number, got {ttl_seconds!r}"
            )
        span_id = span_id or f"span_{uuid.uuid4().hex[:8]}"
        tx_id = tx_id or f"tx_{uuid.uuid4().hex[:8]}"
        lock_id = f"lck_{uuid.uuid4().hex[:8]}"
        res_key = ResourceKey.parse(resource)
        try:
            lock_mode = LockMode(mode)
        except ValueError:
            raise ValueError(
                f"Invalid lock mode {mode!r}: expected one of {[m.value for m in LockMode]}"
            ) from None
        # Monotonic (not wall-clock): fence tokens must never go backwards,
        # even across NTP adjustments. time.time_ns() does not guarantee that.
        fence_token = time.monotonic_ns()

        # Step 1: Concurrency Acquisition (SwarmLock)
        granted, conflict_lock, current_version = self.lock_engine.acquire_lock(
            lock_id=lock_id,
            holder=agent_id,
            resource=res_key,
            mode=lock_mode,
            fence_token=fence_token,
            ttl_seconds=ttl_seconds,
        )

        if not granted:
            held_by = conflict_lock.holder if conflict_lock else "unknown"
            raise RuntimeError(
                f"ConcurrencyConflict: Resource {resource} currently locked by {held_by}"
            )

        if expected_version is not None and current_version != expected_version:
            self.lock_engine.release_lock(
                lock_id=lock_id, holder=agent_id, resource=res_key
            )
            raise RuntimeError(
                f"StaleReadConflict: Resource {resource} version {current_version} != expected {expected_version}"
            )

        # Step 2: Initialize Saga Journal & Merkle DAG Span (SwarmSaga & SwarmLedger).
        # Everything acquired from here on is released if setup fails before the
        # transaction body runs (the finally below only covers post-yield).
        gateway_mirrored = False
        try:
            if getattr(self, "mirror_to_gateway", True):
                gateway_mirrored = self._mirror_lease_acquire(
                    resource, agent_id, tx_id, span_id, task_id, task_title
                )

            self.journal.begin_saga(
                tx_id,
                agent_id,
                metadata={"description": f"Transaction on {resource} by {agent_id}"},
            )
            coordinator = SagaCoordinator(journal=self.journal)

            n_root = self.ledger.append_node(
                span_id=span_id,
                event_type=EventType.MUTATE,
                agent_id=agent_id,
                payload={
                    "tx_id": tx_id,
                    "resource": resource,
                    "mode": lock_mode.value,
                    "fence_token": fence_token,
                    "task_id": task_id,
                },
            )

            ctx = HypervisorTransactionContext(
                tx_id=tx_id,
                span_id=span_id,
                agent_id=agent_id,
                resource=resource,
                mode=lock_mode.value,
                fence_token=fence_token,
                version=current_version,
                lock_id=lock_id,
                journal=self.journal,
                ledger=self.ledger,
                coordinator=coordinator,
                lock_engine=self.lock_engine,
                gate_evaluator=self.gate_evaluator,
                metadata=metadata or {},
            )
        except Exception:
            # Setup failed after the lease was granted: release everything we
            # hold, then re-raise. Without this the SwarmLock lease leaks until
            # its TTL expires and the resource looks busy to everyone else.
            try:
                self.lock_engine.release_lock(
                    lock_id=lock_id, holder=agent_id, resource=res_key
                )
            except Exception as rel_err:
                logger.debug("Setup-failure lock release failed: %s", rel_err)
            if gateway_mirrored:
                self._mirror_lease_release(resource, agent_id, task_id)
            raise

        committed = False
        failed = False
        try:
            yield ctx

            # Step 3: Run Forward DAG Execution
            if ctx.coordinator._steps:
                await ctx.coordinator.execute(tx_id=tx_id)

            # Step 4: Deterministic Multi-Oracle Verification Barrier (SwarmProof)
            if resource.startswith("file:"):
                fpath = resource.split("file:", 1)[1]
                if Path(fpath).exists():
                    passed, cert, diags = SwarmproofBridge.verify_and_settle(
                        target_file=Path(fpath),
                        tx_id=tx_id,
                        holder=agent_id,
                        run_tests=False,
                    )
                    if not passed:
                        raise RuntimeError(
                            f"SwarmProof Invariant Failure: "
                            f"{[f'{d.error_type}: {d.message}' for d in diags]}"
                        )

                    if cert:
                        self.ledger.append_node(
                            span_id=span_id,
                            event_type=EventType.PROOF,
                            agent_id=agent_id,
                            payload={
                                "proof_id": cert.proof_id,
                                "ast_checksum": cert.ast_checksum,
                            },
                            parent_node_ids=[n_root.node_id],
                        )

            # Step 5: Attention Governor Evaluation (SwarmGate)
            decision = self.gate_evaluator.evaluate(
                resource=resource,
                agent_id=agent_id,
                metadata=metadata,
            )
            decision.task_id = task_id
            decision.task_title = task_title

            if decision.tier == AttentionTier.TIER_3_BARRIER:
                # Fail closed: a barrier decision suspends the transaction for
                # human sign-off. Nothing is committed; forward effects are
                # compensated below and the decision stays queued for a human.
                card = DecisionDiffDistiller.distill(decision, "(Code mutation)")
                PendingDecisionStore.add(decision, card)
                logger.info(
                    "Transaction %s held for Tier 3 human approval (%s); rolling back",
                    tx_id,
                    decision.decision_id,
                )
                raise RuntimeError(
                    f"Tier3Barrier: Transaction {tx_id} requires human approval "
                    f"(decision {decision.decision_id}); no commit was recorded"
                )

            # Step 6: Commit Transaction & Seal in Cryptographic Merkle DAG (SwarmLedger).
            # finalize_saga("COMMITTED") is the commit point: once it returns the
            # transaction is committed and must NOT be compensated. The version
            # bump and COMMIT node seal the commit; failures there are recorded,
            # never rolled back.
            self.journal.finalize_saga(tx_id, "COMMITTED")
            committed = True
            self.lock_engine.increment_version_and_propagate(res_key)
            self.ledger.append_node(
                span_id=span_id,
                event_type=EventType.COMMIT,
                agent_id=agent_id,
                payload={
                    "tx_id": tx_id,
                    "status": "COMMITTED",
                    "fence_token": fence_token,
                },
                parent_node_ids=[n_root.node_id],
            )

        except Exception as exc:
            failed = True
            if committed:
                # The journal already records COMMITTED: compensating now would
                # undo committed work. Record the seal failure and surface it.
                logger.critical(
                    "Transaction %s finalized COMMITTED but the commit seal failed (%s); "
                    "manual reconciliation may be needed.",
                    tx_id,
                    exc,
                )
                try:
                    self.ledger.append_node(
                        span_id=span_id,
                        event_type=EventType.ABORT,
                        agent_id=agent_id,
                        payload={
                            "tx_id": tx_id,
                            "status": "COMMIT_SEAL_FAILED",
                            "error": str(exc),
                        },
                        parent_node_ids=[n_root.node_id],
                    )
                except Exception as ledger_err:
                    logger.error(
                        "Failed to record commit-seal failure for %s: %s",
                        tx_id,
                        ledger_err,
                    )
                raise

            logger.error(
                "Transaction %s failed (%s). Triggering reverse compensation rollback...",
                tx_id,
                exc,
            )

            # Step 7: Automatic Backward Topological Rollback & DLQ Fault Isolation (SwarmSaga)
            try:
                unwinder = TopologicalUnwinder(journal=self.journal)
                step_handlers = {
                    name: s.compensate_handler
                    for name, s in ctx.coordinator._steps.items()
                    if s.compensate_handler
                }
                await unwinder.unwind(tx_id=tx_id, step_handlers=step_handlers)
            except Exception as unwind_err:
                logger.error("Error during unwinding: %s", unwind_err)

            # The ABORT node must never mask the original failure.
            try:
                self.ledger.append_node(
                    span_id=span_id,
                    event_type=EventType.ABORT,
                    agent_id=agent_id,
                    payload={"tx_id": tx_id, "status": "ABORTED", "error": str(exc)},
                    parent_node_ids=[n_root.node_id],
                )
            except Exception as ledger_err:
                logger.error(
                    "Failed to append ABORT node for transaction %s: %s",
                    tx_id,
                    ledger_err,
                )
            raise

        finally:
            # Release Held Leases. A release failure must never mask the
            # transaction's own error; but on an otherwise successful
            # transaction it is surfaced, because a silently leaked lease
            # would look like a live lock to everyone else.
            try:
                self.lock_engine.release_lock(
                    lock_id=lock_id, holder=agent_id, resource=res_key
                )
            except Exception as rel_err:
                if failed:
                    logger.error(
                        "Transaction %s failed AND its lease release failed: %s",
                        tx_id,
                        rel_err,
                    )
                else:
                    raise
            if getattr(self, "mirror_to_gateway", True):
                self._mirror_lease_release(resource, agent_id, task_id)

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
