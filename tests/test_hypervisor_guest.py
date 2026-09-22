"""Hermetic tests for the hypervisor guest spawn path (Containment Phase A).

All tests use the stdlib backend explicitly: no docker, no network, no
swarmsandbox required. Async follows the repo convention: plain test
functions driving coroutines with ``asyncio.run()``.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from prismatic.hypervisor.guest import (
    GuestManager,
    GuestSpec,
    GuestSpecError,
    TrustTier,
    _SwarmsandboxRunner,
)
from prismatic.hypervisor.ledger import HypervisorLedger


def _make_manager(tmpdir) -> tuple[GuestManager, HypervisorLedger]:
    ledger = HypervisorLedger(db_path=str(Path(tmpdir) / "ledger.db"))
    return GuestManager(ledger=ledger), ledger


def _actions(ledger: HypervisorLedger, guest_id: str) -> dict:
    return {
        entry.action: entry for entry in ledger.list_events(task_id=guest_id, limit=50)
    }


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# ---------------------------------------------------------------------------
# 1. basic spawn
# ---------------------------------------------------------------------------


def test_spawn_echo_hello_records_ledger():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, ledger = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(entrypoint=["echo", "hello"], backend="stdlib")
            )
            result = await handle.wait()
            assert result.exit_code == 0
            assert result.stdout.strip() == "hello"
            assert result.end_reason == "exit"
            assert result.isolation == "policy-enforced-runner"
            actions = _actions(ledger, handle.guest_id)
            assert set(actions) == {"guest_spawned", "guest_terminated"}
            assert actions["guest_spawned"].payload["tier"] == "untrusted"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. env scrubbing (canary: never a real secret)
# ---------------------------------------------------------------------------


def test_env_scrubbing_keeps_secrets_out_of_guest():
    canary = "canary-secret-9f8e7d6c5b4a1"
    os.environ["PRISMATIC_TEST_FAKE_API_KEY"] = canary
    try:

        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, _ = _make_manager(tmpdir)
                handle = await manager.spawn_guest(
                    GuestSpec(
                        entrypoint=_py(
                            "import os;"
                            "print('KEY_PRESENT' if 'PRISMATIC_TEST_FAKE_API_KEY'"
                            " in os.environ else 'KEY_ABSENT');"
                            "print(os.environ.get('PRISMATIC_TEST_PUBLIC_NOTE',"
                            " 'NO_NOTE'))"
                        ),
                        backend="stdlib",
                        env={"PRISMATIC_TEST_PUBLIC_NOTE": f"note-{canary}-tail"},
                    )
                )
                result = await handle.wait()
                assert result.exit_code == 0
                # The blocked key never reaches the guest environment...
                assert "KEY_ABSENT" in result.stdout
                # ...and the secret value is redacted from captured output
                # even when it arrives via an allowed channel.
                assert canary not in result.stdout
                assert canary not in result.stderr
                assert "***" in result.stdout

        asyncio.run(_run())
    finally:
        del os.environ["PRISMATIC_TEST_FAKE_API_KEY"]


# ---------------------------------------------------------------------------
# 3. timeout kills the guest
# ---------------------------------------------------------------------------


def test_timeout_kills_guest_and_records_failure():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, ledger = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=_py("import time; time.sleep(30)"),
                    backend="stdlib",
                    timeout_seconds=2.0,
                )
            )
            result = await handle.wait()
            assert result.end_reason == "timeout"
            assert result.duration_seconds < 15  # killed, did not sleep 30s
            actions = _actions(ledger, handle.guest_id)
            assert "guest_failed" in actions
            assert actions["guest_failed"].payload["reason"] == "timeout"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. lease expiry is fail-closed
# ---------------------------------------------------------------------------


def test_lease_expiry_kills_guest_fail_closed():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, ledger = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=_py("import time; time.sleep(30)"),
                    backend="stdlib",
                    timeout_seconds=60.0,
                    lease_ttl_seconds=1.0,
                )
            )
            # Never heartbeat: the monitor must kill the guest fail-closed.
            result = await asyncio.wait_for(handle.wait(), timeout=15.0)
            assert result.end_reason == "lease_expired"
            # The guest was SIGKILLed (non-zero exit), not exited on its own.
            assert result.exit_code is not None and result.exit_code != 0
            # The process is really dead.
            assert handle._running._proc.returncode is not None
            actions = _actions(ledger, handle.guest_id)
            assert "guest_lease_expired" in actions
            # Exactly one terminal event: no duplicate guest_failed/terminated.
            assert "guest_failed" not in actions
            assert "guest_terminated" not in actions
            payload = actions["guest_lease_expired"].payload
            assert payload["lease_ttl_seconds"] == 1.0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. output cap
# ---------------------------------------------------------------------------


def test_output_cap_truncates_with_marker():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=_py("import sys; sys.stdout.write('x' * 5000000)"),
                    backend="stdlib",
                    max_output_bytes=100_000,
                )
            )
            result = await asyncio.wait_for(handle.wait(), timeout=30.0)
            assert result.exit_code == 0
            assert result.truncated is True
            assert "[prismatic: guest output truncated at 100000 bytes]" in (
                result.stdout
            )
            assert len(result.stdout.encode("utf-8")) <= 100_000 + 256

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 6. tier honesty
# ---------------------------------------------------------------------------


def test_trusted_tier_never_claims_sandboxing():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, ledger = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=["echo", "hi"],
                    tier=TrustTier.TRUSTED,
                    backend="stdlib",
                )
            )
            result = await handle.wait()
            assert result.exit_code == 0
            status = handle.status()
            assert status["isolation"] == "none"
            assert "sandbox" not in json.dumps(status).lower()
            for entry in ledger.list_events(task_id=handle.guest_id, limit=50):
                assert "sandbox" not in entry.action.lower()
                assert "sandbox" not in json.dumps(entry.payload).lower()

    asyncio.run(_run())


def test_untrusted_stdlib_isolation_label_is_honest():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, ledger = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=["echo", "hi"],
                    tier=TrustTier.UNTRUSTED,
                    backend="stdlib",
                )
            )
            await handle.wait()
            actions = _actions(ledger, handle.guest_id)
            isolation = actions["guest_spawned"].payload["isolation"]
            assert isolation == "policy-enforced-runner"
            assert "jail" not in isolation

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7. Phase D / Phase C fields
# ---------------------------------------------------------------------------


def test_snapshot_from_rejected_with_phase_d_error():
    with pytest.raises(GuestSpecError, match="Phase D"):
        GuestSpec(entrypoint=["echo", "hi"], snapshot_from="snap-123")


def test_budget_usd_stored_not_enforced():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(
                    entrypoint=["echo", "hi"],
                    backend="stdlib",
                    budget_usd=25.0,
                )
            )
            assert handle.budget_usd == 25.0
            result = await handle.wait()
            assert result.exit_code == 0  # ran fine: budget is not enforced
            assert result.budget_usd == 25.0

    asyncio.run(_run())


def test_invalid_backend_rejected():
    with pytest.raises(GuestSpecError, match="backend"):
        GuestSpec(entrypoint=["echo", "hi"], backend="bogus")


def test_explicit_unavailable_backend_raises_not_silent_fallback(monkeypatch):
    """An explicit isolation request must never silently downgrade."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            with pytest.raises(GuestSpecError, match="unavailable"):
                await manager.spawn_guest(
                    GuestSpec(entrypoint=["echo", "hi"], backend="namespace")
                )

    monkeypatch.setattr(_SwarmsandboxRunner, "_namespace_available", lambda: False)
    asyncio.run(_run())


def test_auto_falls_back_to_stdlib_when_no_strong_backend(monkeypatch):
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            handle = await manager.spawn_guest(
                GuestSpec(entrypoint=["echo", "hi"], backend="auto")
            )
            result = await handle.wait()
            assert result.exit_code == 0
            assert result.backend == "stdlib"
            assert result.isolation == "policy-enforced-runner"

    monkeypatch.setattr(_SwarmsandboxRunner, "_namespace_available", lambda: False)
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 8. supervised() auto-heartbeat + terminate_all
# ---------------------------------------------------------------------------


def test_supervised_auto_heartbeat_keeps_lease_alive():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            async with manager.supervised(
                GuestSpec(
                    entrypoint=_py("import time; time.sleep(3)"),
                    backend="stdlib",
                    timeout_seconds=30.0,
                    lease_ttl_seconds=1.0,
                )
            ) as handle:
                # 3x the TTL with no manual heartbeat: auto-heartbeat must
                # keep the guest alive.
                await asyncio.sleep(2.5)
                assert not handle.done
                result = await handle.wait()
                assert result.end_reason == "exit"
                assert result.exit_code == 0
            # Exiting the context terminates the guest.
            assert handle.done

    asyncio.run(_run())


def test_terminate_all_kills_live_guests():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _ = _make_manager(tmpdir)
            handles = [
                await manager.spawn_guest(
                    GuestSpec(
                        entrypoint=_py("import time; time.sleep(30)"),
                        backend="stdlib",
                    )
                )
                for _ in range(2)
            ]
            assert await manager.active_guests() != []
            await manager.terminate_all()
            for handle in handles:
                result = await handle.wait()
                assert result.end_reason == "terminated"
            assert await manager.active_guests() == []

    asyncio.run(_run())
