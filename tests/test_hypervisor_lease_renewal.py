"""Lease-renewal heartbeat tests for the hypervisor kernel.

A transaction body that outlives its SwarmLock TTL must keep exclusivity:
the kernel renews the lease in the background (ttl/3 heartbeat) until the
transaction ends, then stops.
"""

import asyncio
import tempfile
import time
from pathlib import Path

from swarmlock.hierarchy import HierarchyLockEngine, LockMode, ResourceKey

from prismatic.hypervisor import PrismaticHypervisor


def _make_hypervisor(tmpdir, **kwargs):
    kwargs.setdefault("mirror_to_gateway", False)
    return PrismaticHypervisor(
        journal_db_path=str(Path(tmpdir) / "journal.db"),
        ledger_db_path=str(Path(tmpdir) / "ledger.db"),
        **kwargs,
    )


def test_long_transaction_keeps_lease_via_heartbeat():
    """A body running 3x past its TTL still holds the lock at the end."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:heartbeat-doc"
            async with hypervisor.transaction(
                resource=res, agent_id="bot_hb", ttl_seconds=0.6
            ):
                await asyncio.sleep(1.8)  # 3x the TTL
                # The lease must still be held: a rival X acquire fails.
                ok, conflict, _ = hypervisor.lock_engine.acquire_lock(
                    lock_id="rival",
                    holder="rival_bot",
                    resource=ResourceKey.parse(res),
                    mode=LockMode.X,
                    fence_token=999,
                    ttl_seconds=60,
                )
                assert ok is False
                assert conflict is not None and conflict.holder == "bot_hb"

    asyncio.run(_run())


def test_heartbeat_stops_after_transaction():
    """Renewals happen during the transaction and stop when it ends."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            calls = []
            orig_renew = hypervisor.lock_engine.renew_lock

            def counting(**kwargs):
                calls.append(time.monotonic())
                return orig_renew(**kwargs)

            hypervisor.lock_engine.renew_lock = counting
            async with hypervisor.transaction(
                resource="swarm:hb-stop", agent_id="bot_hb2", ttl_seconds=0.6
            ):
                await asyncio.sleep(1.0)
            n_during = len(calls)
            assert n_during >= 1, "heartbeat never renewed during the transaction"
            await asyncio.sleep(1.0)
            assert len(calls) == n_during, (
                "heartbeat kept renewing after the transaction ended"
            )

    asyncio.run(_run())


def test_missing_renew_lock_degrades_gracefully():
    """A lock engine predating renew_lock must not break transactions."""

    class OldEngine(HierarchyLockEngine):
        renew_lock = None  # simulate a swarmlock install without renew_lock

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, lock_engine=OldEngine())
            async with hypervisor.transaction(
                resource="swarm:old-engine", agent_id="bot_old"
            ) as tx:
                tx.register_step(
                    name="step_1",
                    forward_fn=lambda ctx: ("done", {}),
                )
            # Committed despite no renewal path.
            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "COMMITTED"

    asyncio.run(_run())
