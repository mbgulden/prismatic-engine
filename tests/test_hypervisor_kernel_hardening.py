"""Hardening tests for the Prismatic Unified Agent Hypervisor kernel.

Covers the failure modes the happy-path suite does not:
- Tier-3 attention barriers must fail closed (no commit), not commit anyway.
- Concurrent transactions on the same resource serialize (one wins).
- A setup failure after lease acquisition must release the lease (no leak).
- Invalid lock modes are rejected instead of silently degrading.
- A denied lock must not emit phantom gateway mirror signals.
- A denied *gateway mirror* must not emit a phantom lock_acquired signal.
- A lease-release failure on success is surfaced; during a failed transaction
  it never masks the original error.
- Blank or wrong-typed transaction arguments fail fast with clear errors.
- A commit-seal failure after the commit point must not trigger compensation.
- An ABORT-node ledger failure must not mask the original error.
- Fence tokens are monotonic.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest
import swarmgate.bridge
from swarmgate.schemas import AttentionTier
from swarmledger.core.node import EventType
from swarmlock.hierarchy import LockMode, ResourceKey, HierarchyLockEngine

import prismatic.hypervisor.kernel as kernel_mod
from prismatic.hypervisor import PrismaticHypervisor


def _make_hypervisor(tmpdir, **kwargs):
    kwargs.setdefault("mirror_to_gateway", False)
    return PrismaticHypervisor(
        journal_db_path=str(Path(tmpdir) / "journal.db"),
        ledger_db_path=str(Path(tmpdir) / "ledger.db"),
        **kwargs,
    )


def _span_event_types(hypervisor, span_id):
    nodes = hypervisor.ledger.get_span_nodes(span_id)
    return [n.event_type for n in nodes]


def test_tier3_barrier_does_not_commit():
    """A TIER_3_BARRIER decision must hold the transaction: no commit, effects compensated."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            # ".env" in the path drives the real gate evaluator to TIER_3_BARRIER
            # (structural risk 1.0 -> escalation score ~0.75 >= tier2_max 0.70).
            # The file does not exist, so SwarmProof verification is skipped.
            res = f"file:{tmpdir}/.env"
            hypervisor = _make_hypervisor(tmpdir)

            # Isolate the pending-decision store: it defaults to ~/.swarmgate.
            pending_file = Path(tmpdir) / "pending_decisions.json"
            orig_pending = swarmgate.bridge.PENDING_FILE
            swarmgate.bridge.PENDING_FILE = pending_file
            try:
                comp_log = []
                with pytest.raises(RuntimeError, match="Tier3Barrier"):
                    async with hypervisor.transaction(
                        resource=res, agent_id="risky_bot", task_id="T3-1"
                    ) as tx:
                        tx.register_step(
                            name="step_1",
                            forward_fn=lambda ctx: ("done", {"undo": "ok"}),
                            compensate_fn=lambda p: comp_log.append("step_1_undone"),
                        )

                # Forward effects were compensated, not left applied.
                assert comp_log == ["step_1_undone"]

                # The saga was aborted, never committed.
                saga_record = hypervisor.journal.get_saga(tx.tx_id)
                assert saga_record["state"] == "ABORTED"

                # The Merkle DAG span has MUTATE + ABORT nodes, and no COMMIT node.
                event_types = _span_event_types(hypervisor, tx.span_id)
                assert EventType.COMMIT not in event_types
                assert EventType.ABORT in event_types

                # The decision is queued for human sign-off.
                stored = swarmgate.bridge.PendingDecisionStore.load_all()
                assert len(stored) == 1
                decision = next(iter(stored.values()))
                assert decision["tier"] == AttentionTier.TIER_3_BARRIER.value
            finally:
                swarmgate.bridge.PENDING_FILE = orig_pending

    asyncio.run(_run())


def test_concurrent_transactions_serialize_on_same_resource():
    """Two overlapping transactions on one resource: exactly one wins, the other conflicts."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:shared-doc"
            entered = asyncio.Event()
            release_first = asyncio.Event()
            outcomes = []

            async def first():
                try:
                    async with hypervisor.transaction(resource=res, agent_id="bot_a"):
                        entered.set()
                        await release_first.wait()
                    outcomes.append("committed")
                except RuntimeError as e:
                    outcomes.append(f"failed: {e}")

            async def second():
                await entered.wait()
                try:
                    async with hypervisor.transaction(resource=res, agent_id="bot_b"):
                        pass
                    outcomes.append("committed")
                except RuntimeError as e:
                    outcomes.append(f"failed: {e}")
                finally:
                    release_first.set()

            await asyncio.gather(first(), second())

            assert outcomes.count("committed") == 1
            assert sum("ConcurrencyConflict" in o for o in outcomes) == 1

            # After both finish, the lease is fully released.
            conflict = hypervisor.lock_engine.check_conflict(
                ResourceKey.parse(res), LockMode.X, "bot_c"
            )
            assert conflict is None

    asyncio.run(_run())


def test_setup_failure_after_lease_acquire_releases_lease():
    """If journal/ledger setup blows up after the lock is granted, the lease must not leak."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:leak-check"

            def boom(tx_id, agent_id, metadata=None):
                raise RuntimeError("simulated journal outage")

            hypervisor.journal.begin_saga = boom

            with pytest.raises(RuntimeError, match="simulated journal outage"):
                async with hypervisor.transaction(resource=res, agent_id="bot_a"):
                    pass  # pragma: no cover - setup fails before the body

            # The lease was released: another agent can acquire immediately.
            conflict = hypervisor.lock_engine.check_conflict(
                ResourceKey.parse(res), LockMode.X, "bot_b"
            )
            assert conflict is None

    asyncio.run(_run())


def test_invalid_lock_mode_rejected():
    """A typo'd lock mode must fail loudly, not silently degrade to exclusive."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            with pytest.raises(ValueError, match="Invalid lock mode"):
                async with hypervisor.transaction(resource="swarm:x", mode="bogus"):
                    pass  # pragma: no cover

            # Valid modes still work.
            async with hypervisor.transaction(resource="swarm:x", mode="S") as tx:
                assert tx.mode == "S"

    asyncio.run(_run())


def test_denied_lock_emits_no_gateway_mirror_signals():
    """On ConcurrencyConflict, no phantom lock_acquired signal may be mirrored."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, mirror_to_gateway=True)
            res = "swarm:contended"

            # Pre-hold the resource with another agent.
            hypervisor.lock_engine.acquire_lock(
                lock_id="lck_holder",
                holder="bot_holder",
                resource=ResourceKey.parse(res),
                mode=LockMode.X,
                fence_token=1,
                ttl_seconds=60.0,
            )
            try:
                acquired = []
                released = []
                signals = []

                class DummyMgr:
                    def acquire(self, resource_id, agent_id, metadata=None):
                        acquired.append((resource_id, agent_id, metadata))

                    def release(self, resource_id, agent_id):
                        released.append((resource_id, agent_id))

                def fake_signal(**kwargs):
                    signals.append(kwargs)

                orig_mgr = kernel_mod._get_lock_manager
                orig_signal = kernel_mod.record_agent_signal
                kernel_mod._get_lock_manager = lambda: DummyMgr()
                kernel_mod.record_agent_signal = fake_signal
                try:
                    with pytest.raises(RuntimeError, match="ConcurrencyConflict"):
                        async with hypervisor.transaction(
                            resource=res, agent_id="bot_b"
                        ):
                            pass  # pragma: no cover
                finally:
                    kernel_mod._get_lock_manager = orig_mgr
                    kernel_mod.record_agent_signal = orig_signal

                # Nothing was mirrored: no phantom acquire, no release, no signals.
                assert acquired == []
                assert released == []
                assert signals == []
            finally:
                hypervisor.lock_engine.release_lock(lock_id="lck_holder")

    asyncio.run(_run())


def test_commit_seal_failure_does_not_compensate():
    """Once the journal records COMMITTED, a seal failure must not undo committed work."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:seal-check"
            comp_log = []

            real_append = hypervisor.ledger.append_node

            def flaky_append(*args, **kwargs):
                if kwargs.get("event_type") == EventType.COMMIT:
                    raise RuntimeError("simulated ledger disk-full")
                return real_append(*args, **kwargs)

            hypervisor.ledger.append_node = flaky_append

            with pytest.raises(RuntimeError, match="simulated ledger disk-full"):
                async with hypervisor.transaction(resource=res, agent_id="bot_a") as tx:
                    tx.register_step(
                        name="step_1",
                        forward_fn=lambda ctx: ("done", {}),
                        compensate_fn=lambda p: comp_log.append("step_1_undone"),
                    )

            # Committed in the journal; compensation must NOT have run.
            assert hypervisor.journal.get_saga(tx.tx_id)["state"] == "COMMITTED"
            assert comp_log == []

            # The failure was recorded honestly in the span.
            payloads = [n.payload for n in hypervisor.ledger.get_span_nodes(tx.span_id)]
            assert any(p.get("status") == "COMMIT_SEAL_FAILED" for p in payloads)

    asyncio.run(_run())


def test_abort_node_failure_does_not_mask_original_error():
    """If the ABORT ledger write fails, the caller still sees the real failure."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:abort-check"

            real_append = hypervisor.ledger.append_node

            def flaky_append(*args, **kwargs):
                if kwargs.get("event_type") == EventType.ABORT:
                    raise RuntimeError("simulated ledger outage")
                return real_append(*args, **kwargs)

            hypervisor.ledger.append_node = flaky_append

            # NOTE: swarmsaga's coordinator wraps the step failure: it unwinds
            # internally and re-raises RuntimeError("Saga ... aborted and
            # compensated. Reason: original failure"). The property under test
            # is that the ABORT-node ledger failure does not mask THAT error.
            with pytest.raises(RuntimeError, match="original failure"):
                async with hypervisor.transaction(resource=res, agent_id="bot_a") as tx:
                    tx.register_step(
                        name="step_1",
                        forward_fn=lambda ctx: (_ for _ in ()).throw(
                            ValueError("original failure")
                        ),
                    )

            # Rollback still completed despite the ledger write failing.
            assert hypervisor.journal.get_saga(tx.tx_id)["state"] == "ABORTED"

    asyncio.run(_run())


def test_fence_tokens_are_monotonic():
    """Fence tokens must never go backwards, even across wall-clock adjustments."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            tokens = []
            for i in range(3):
                async with hypervisor.transaction(
                    resource=f"swarm:tok-{i}", agent_id="bot_a"
                ) as tx:
                    tokens.append(tx.fence_token)
            assert all(t > 0 for t in tokens)
            assert tokens == sorted(tokens)

    asyncio.run(_run())


def test_no_hardcoded_task_id_in_gateway_mirror():
    """The gateway mirror must not leak a developer-specific default task id."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, mirror_to_gateway=True)
            captured = {}

            class DummyMgr:
                def acquire(self, resource_id, agent_id, metadata=None):
                    captured.update(metadata or {})

                def release(self, resource_id, agent_id):
                    pass

            orig_mgr = kernel_mod._get_lock_manager
            kernel_mod._get_lock_manager = lambda: DummyMgr()
            try:
                async with hypervisor.transaction(
                    resource="swarm:meta-check", agent_id="bot_a"
                ):
                    pass
            finally:
                kernel_mod._get_lock_manager = orig_mgr

            assert captured.get("task_id") == ""
            assert "GRO-3319" not in str(captured.values())

    asyncio.run(_run())


def test_stale_expected_version_rejected_and_released():
    """A version mismatch fails closed and releases the just-acquired lease."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:versioned"

            async with hypervisor.transaction(resource=res, agent_id="bot_a"):
                pass

            with pytest.raises(RuntimeError, match="StaleReadConflict"):
                async with hypervisor.transaction(
                    resource=res, agent_id="bot_b", expected_version=1
                ):
                    pass  # pragma: no cover - version check fails first

            # The failed attempt released its lease; the resource is free.
            conflict = hypervisor.lock_engine.check_conflict(
                ResourceKey.parse(res), LockMode.X, "bot_c"
            )
            assert conflict is None

    asyncio.run(_run())


def test_gateway_mirror_failure_is_fail_open():
    """A broken gateway mirror must not fail the transaction (observability only)."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, mirror_to_gateway=True)

            def boom():
                raise RuntimeError("gateway down")

            orig_mgr = kernel_mod._get_lock_manager
            kernel_mod._get_lock_manager = boom
            try:
                async with hypervisor.transaction(
                    resource="swarm:mirror-fail", agent_id="bot_a"
                ) as tx:
                    assert tx.tx_id.startswith("tx_")
            finally:
                kernel_mod._get_lock_manager = orig_mgr

            # Lease lifecycle on the real lock engine is unaffected.
            conflict = hypervisor.lock_engine.check_conflict(
                ResourceKey.parse("swarm:mirror-fail"), LockMode.X, "bot_b"
            )
            assert conflict is None

    asyncio.run(_run())


def test_gateway_mirror_release_failure_is_fail_open():
    """A failing mirror release in `finally` must not mask the transaction outcome."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, mirror_to_gateway=True)

            class FlakyMgr:
                def acquire(self, resource_id, agent_id, metadata=None):
                    pass

                def release(self, resource_id, agent_id):
                    raise RuntimeError("gateway release down")

            orig_mgr = kernel_mod._get_lock_manager
            kernel_mod._get_lock_manager = lambda: FlakyMgr()
            try:
                async with hypervisor.transaction(
                    resource="swarm:mirror-rel", agent_id="bot_a"
                ):
                    pass
            finally:
                kernel_mod._get_lock_manager = orig_mgr

    asyncio.run(_run())


def test_commit_seal_abort_node_failure_still_raises_original():
    """Even when the COMMIT_SEAL_FAILED node can't be written, the seal error surfaces."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:seal-outage"
            comp_log = []

            real_append = hypervisor.ledger.append_node

            def outage_append(*args, **kwargs):
                if kwargs.get("event_type") in (EventType.COMMIT, EventType.ABORT):
                    raise RuntimeError("ledger fully down")
                return real_append(*args, **kwargs)

            hypervisor.ledger.append_node = outage_append

            with pytest.raises(RuntimeError, match="ledger fully down"):
                async with hypervisor.transaction(resource=res, agent_id="bot_a") as tx:
                    tx.register_step(
                        name="step_1",
                        forward_fn=lambda ctx: ("done", {}),
                        compensate_fn=lambda p: comp_log.append("step_1_undone"),
                    )

            assert hypervisor.journal.get_saga(tx.tx_id)["state"] == "COMMITTED"
            assert comp_log == []

    asyncio.run(_run())


def test_unwinder_failure_does_not_mask_original_error():
    """If compensation itself blows up, the original failure still propagates."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:unwind-outage"

            class BoomUnwinder:
                def __init__(self, journal):
                    pass

                async def unwind(self, tx_id, step_handlers):
                    raise RuntimeError("unwinder exploded")

            orig_unwinder = kernel_mod.TopologicalUnwinder
            kernel_mod.TopologicalUnwinder = BoomUnwinder
            try:
                # NOTE: swarmsaga's coordinator unwinds internally first and
                # re-raises RuntimeError("Saga ... Reason: step blew up").
                with pytest.raises(RuntimeError, match="step blew up"):
                    async with hypervisor.transaction(
                        resource=res, agent_id="bot_a"
                    ) as tx:
                        tx.register_step(
                            name="step_1",
                            forward_fn=lambda ctx: (_ for _ in ()).throw(
                                ValueError("step blew up")
                            ),
                        )
            finally:
                kernel_mod.TopologicalUnwinder = orig_unwinder

    asyncio.run(_run())


def test_setup_failure_with_broken_release_still_raises_original():
    """Even if the cleanup release blows up, the setup error is what surfaces."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            res = "swarm:release-broken"

            def boom(tx_id, agent_id, metadata=None):
                raise RuntimeError("simulated journal outage")

            def broken_release(**kwargs):
                raise RuntimeError("release exploded")

            hypervisor.journal.begin_saga = boom
            hypervisor.lock_engine.release_lock = broken_release

            with pytest.raises(RuntimeError, match="simulated journal outage"):
                async with hypervisor.transaction(resource=res, agent_id="bot_a"):
                    pass  # pragma: no cover - setup fails before the body

    asyncio.run(_run())


# ── Mirror denial, release-failure semantics, and input validation ──


def test_mirror_acquire_denied_returns_false_without_success_signal(
    tmp_path, monkeypatch
):
    """A denied gateway mirror must not emit a phantom lock_acquired signal."""
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))

    class DenyingMgr:
        def acquire(self, **kwargs):
            return False

        def release(self, **kwargs):
            return True

    orig = kernel_mod._get_lock_manager
    kernel_mod._get_lock_manager = lambda: DenyingMgr()
    try:
        hypervisor = _make_hypervisor(str(tmp_path), mirror_to_gateway=True)
        mirrored = hypervisor._mirror_lease_acquire(
            "mem://mirror-denied", "sig_bot", "tx_1", "span_1", "T-1", "title"
        )
        assert mirrored is False
    finally:
        kernel_mod._get_lock_manager = orig

    signal_file = tmp_path / "state" / "agent_signal_stream.jsonl"
    if signal_file.exists():
        assert "lock_acquired" not in signal_file.read_text()


class ExplodingReleaseEngine(HierarchyLockEngine):
    def release_lock(self, **kwargs):
        raise RuntimeError("release exploded")


def _make_exploding_hypervisor(tmpdir):
    return PrismaticHypervisor(
        journal_db_path=str(Path(tmpdir) / "journal.db"),
        ledger_db_path=str(Path(tmpdir) / "ledger.db"),
        lock_engine=ExplodingReleaseEngine(),
        mirror_to_gateway=False,
    )


def test_release_failure_on_successful_tx_is_surfaced():
    """A lease that cannot be released on success must raise, not leak silently."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_exploding_hypervisor(tmpdir)
            with pytest.raises(RuntimeError, match="release exploded"):
                async with hypervisor.transaction(
                    resource="mem://rel-ok", agent_id="rel_bot"
                ):
                    pass

    asyncio.run(_run())


def test_release_failure_does_not_mask_tx_error():
    """When the transaction already failed, a release failure must not replace it."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_exploding_hypervisor(tmpdir)
            with pytest.raises(ValueError, match="original boom"):
                async with hypervisor.transaction(
                    resource="mem://rel-bad", agent_id="rel_bot"
                ):
                    raise ValueError("original boom")

    asyncio.run(_run())


@pytest.mark.parametrize(
    "override",
    [
        {"resource": ""},
        {"resource": "   "},
        {"resource": None},
        {"agent_id": ""},
        {"agent_id": 123},
        {"task_id": "  "},
        {"tx_id": ""},
        {"span_id": None.__class__},  # a non-str type sentinel
        {"ttl_seconds": 0},
        {"ttl_seconds": -5},
        {"ttl_seconds": True},
        {"ttl_seconds": "60"},
        {"metadata": "not-a-dict"},
    ],
)
def test_transaction_rejects_invalid_input(tmp_path, override):
    """Blank/wrong-typed transaction arguments fail fast with a clear error."""
    kwargs = {"resource": "mem://valid", "agent_id": "valid_bot"}
    kwargs.update(override)

    async def _run():
        hypervisor = _make_hypervisor(str(tmp_path))
        with pytest.raises((ValueError, TypeError)):
            async with hypervisor.transaction(**kwargs):
                pass  # pragma: no cover - validation runs before the body

    asyncio.run(_run())


def test_transaction_accepts_valid_optional_inputs(tmp_path):
    """Sane explicit inputs (ids, metadata, ttl) still pass validation."""

    async def _run():
        hypervisor = _make_hypervisor(str(tmp_path))
        async with hypervisor.transaction(
            resource="mem://valid2",
            agent_id="valid_bot",
            span_id="span_ok",
            tx_id="tx_ok",
            task_id="T-9",
            task_title="a title",
            metadata={"k": "v"},
            ttl_seconds=5,
        ) as tx:
            assert tx.tx_id == "tx_ok"

    asyncio.run(_run())
