"""tests/test_native_cron_events.py — WI-4: live crons tab event emission.

``NativeCronStore.mutate()`` must emit ``cron.mutated`` and the WI-1
run-recorder writeback (``record_cron_run``) must emit ``cron.run_recorded``,
so the dashboard crons tab can refresh without a manual reload. Emission is
best-effort: it must never raise and must never change the mutation/writeback
outcome, even when the gateway or the IPC bridge is unavailable.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_importable() -> None:
    """Make ``prismatic.native_crons`` importable on machines without the
    full gateway extras.

    ``prismatic/__init__.py`` pulls in the dispatcher (needs ``swarmlock``
    etc.), which is unavailable on minimal/dev machines. On a full install
    the plain import below succeeds and this is a no-op; otherwise stub the
    parent packages so the submodule loads from its file path directly.
    """
    try:
        import prismatic.native_crons  # noqa: F401

        return
    except ImportError:
        pass
    for name in ("prismatic.gateway", "prismatic"):
        sys.modules.pop(name, None)
    pkg = types.ModuleType("prismatic")
    pkg.__path__ = [str(REPO_ROOT / "prismatic")]
    sys.modules["prismatic"] = pkg
    gw = types.ModuleType("prismatic.gateway")
    gw.__path__ = [str(REPO_ROOT / "prismatic" / "gateway")]
    sys.modules["prismatic.gateway"] = gw


_ensure_importable()

from prismatic import native_crons  # noqa: E402
from prismatic.gateway import ipc_bridge  # noqa: E402
from prismatic.gateway.event_bus import SwarmEvent, set_event_bus  # noqa: E402
from prismatic.native_crons import (  # noqa: E402
    NativeCron,
    NativeCronStore,
    create_native_cron,
    record_cron_run,
)


def _store_with_cron(tmp_path: Path, **overrides) -> tuple[NativeCronStore, NativeCron]:
    store = NativeCronStore(path=tmp_path / "native_crons.json")
    kwargs: dict = {
        "id": "test.event-cron",
        "name": "Event test cron",
        "schedule": "*/5 * * * *",
        "command": ["true"],
    }
    kwargs.update(overrides)
    cron = NativeCron(**kwargs)
    store.save([cron])
    return store, cron


@pytest.fixture()
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Capture (_emit_cron_event) calls instead of broadcasting."""
    calls: list[tuple[str, dict]] = []

    def _fake(event_type: str, payload: dict) -> None:
        calls.append((event_type, payload))

    monkeypatch.setattr(native_crons, "_emit_cron_event", _fake)
    return calls


# ── mutate() emits cron.mutated ───────────────────────────────


def test_mutate_pause_emits_cron_mutated(tmp_path: Path, emitted) -> None:
    store, cron = _store_with_cron(tmp_path)
    result = store.mutate(cron.id, "pause")
    assert result["success"] is True
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.mutated"
    assert payload["cron_id"] == cron.id
    assert payload["action"] == "pause"
    assert payload["state"] == "paused"
    # The mutation itself is unaffected.
    assert store.get(cron.id).state == "paused"


def test_mutate_run_emits_cron_mutated_with_status(tmp_path: Path, emitted) -> None:
    store, cron = _store_with_cron(tmp_path)
    result = store.mutate(cron.id, "run")
    assert result["success"] is True
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.mutated"
    assert payload["action"] == "run"
    assert payload["status"] == "success"
    assert payload["exit_code"] == 0


def test_mutate_recover_emits_cron_mutated(tmp_path: Path, emitted) -> None:
    store, cron = _store_with_cron(tmp_path)
    result = store.mutate(cron.id, "recover")
    assert result["success"] is True
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.mutated"
    assert payload["action"] == "recover"
    assert payload["replays_count"] >= 1


def test_mutate_delete_emits_cron_mutated(tmp_path: Path, emitted) -> None:
    store, cron = _store_with_cron(tmp_path)
    result = store.mutate(cron.id, "delete")
    assert result["success"] is True
    assert emitted[0][0] == "cron.mutated"
    assert emitted[0][1]["action"] == "delete"
    assert emitted[0][1]["state"] == "deleted"


def test_mutate_unknown_cron_raises_without_emit(tmp_path: Path, emitted) -> None:
    store, _ = _store_with_cron(tmp_path)
    with pytest.raises(KeyError):
        store.mutate("does-not-exist", "pause")
    assert emitted == []


def test_create_native_cron_emits_cron_mutated(tmp_path: Path, emitted) -> None:
    store, _ = _store_with_cron(tmp_path)
    created = create_native_cron(
        {
            "id": "test.created-cron",
            "name": "Created cron",
            "schedule": "manual",
            "command": ["true"],
        },
        store=store,
    )
    assert created["id"] == "test.created-cron"
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.mutated"
    assert payload["cron_id"] == "test.created-cron"
    assert payload["action"] == "create"


def test_create_native_cron_duplicate_emits_nothing(
    tmp_path: Path, emitted
) -> None:
    store, cron = _store_with_cron(tmp_path)
    from prismatic.native_crons import DuplicateCronIdError

    with pytest.raises(DuplicateCronIdError):
        create_native_cron(
            {
                "id": cron.id,
                "name": "Dup",
                "schedule": "manual",
                "command": ["true"],
            },
            store=store,
        )
    assert emitted == []


# ── record_cron_run() emits cron.run_recorded ─────────────────


def test_record_cron_run_emits_run_recorded(tmp_path: Path, emitted) -> None:
    store, cron = _store_with_cron(tmp_path)
    exit_code = record_cron_run(cron.id, "true", store=store)
    assert exit_code == 0
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.run_recorded"
    assert payload["cron_id"] == cron.id
    assert payload["status"] == "success"
    assert payload["exit_code"] == 0
    # Writeback itself still happened.
    assert store.get(cron.id).last_status == "success"


def test_record_cron_run_failure_emits_failed_status(
    tmp_path: Path, emitted
) -> None:
    store, cron = _store_with_cron(tmp_path)
    exit_code = record_cron_run(cron.id, "exit 3", store=store)
    assert exit_code == 3
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "cron.run_recorded"
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 3


def test_record_cron_run_unknown_id_emits_nothing(
    tmp_path: Path, emitted, capsys: pytest.CaptureFixture
) -> None:
    store, _ = _store_with_cron(tmp_path)
    exit_code = record_cron_run("nope", "true", store=store)
    assert exit_code == 0  # job's exit code propagates, unmasked
    assert emitted == []


# ── emission never breaks cron ops (negative paths) ──────────


def test_emit_never_raises_without_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_crons, "_HAS_IPC", False)
    native_crons._emit_cron_event("cron.mutated", {"cron_id": "x"})  # must not raise


def test_emit_never_raises_when_socket_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kwargs) -> bool:
        raise OSError("socket gone")

    monkeypatch.setattr(native_crons, "send_event_via_socket", _boom)
    native_crons._emit_cron_event("cron.mutated", {"cron_id": "x"})  # must not raise


def test_emit_uses_socket_when_no_loop_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    def _fake(**kwargs) -> bool:
        seen.append(kwargs)
        return True

    monkeypatch.setattr(native_crons, "send_event_via_socket", _fake)
    native_crons._emit_cron_event("cron.run_recorded", {"cron_id": "x"})
    assert len(seen) == 1
    assert seen[0]["event_type"] == "cron.run_recorded"
    assert seen[0]["source"] == "native-crons"
    assert seen[0]["payload"] == {"cron_id": "x"}


def test_emit_publishes_to_inprocess_bus_when_loop_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, str, dict]] = []

    class _FakeBus:
        async def publish(self, event_type: str, source: str, payload=None):
            published.append((event_type, source, payload or {}))

    set_event_bus(_FakeBus())  # type: ignore[arg-type]
    socket_calls: list[dict] = []
    monkeypatch.setattr(
        native_crons, "send_event_via_socket", lambda **kw: socket_calls.append(kw) or True
    )
    try:

        async def _main() -> None:
            native_crons._emit_cron_event("cron.mutated", {"cron_id": "x"})
            await asyncio.sleep(0.05)  # let the created task run

        asyncio.run(_main())
    finally:
        set_event_bus(None)  # type: ignore[arg-type]

    assert len(published) == 1
    assert published[0][0] == "cron.mutated"
    assert published[0][1] == "native-crons"
    assert socket_calls == []  # in-process path wins; no duplicate socket push


# ── IPC bridge accepts the new event types ────────────────────


@pytest.mark.parametrize("event_type", ["cron.mutated", "cron.run_recorded"])
def test_ipc_bridge_validates_cron_event_types(event_type: str) -> None:
    ok, reason = ipc_bridge.validate_event(
        {"type": event_type, "source": "native-crons", "payload": {}}
    )
    assert ok, reason


def test_cron_event_ws_forwarder_forwards_only_cron_events() -> None:
    collected: list[dict] = []

    async def _broadcast(message: dict) -> None:
        collected.append(message)

    forwarder = ipc_bridge.cron_event_ws_forwarder(_broadcast)

    async def _main() -> None:
        await forwarder(SwarmEvent("cron.mutated", "native-crons", {"a": 1}))
        await forwarder(SwarmEvent("cron.run_recorded", "native-crons", {"b": 2}))
        await forwarder(SwarmEvent("deploy.started", "deploy-receiver", {}))
        await forwarder(SwarmEvent("signal.emitted", "x", {}))

    asyncio.run(_main())
    assert [m["type"] for m in collected] == ["cron.mutated", "cron.run_recorded"]


def test_cron_event_ws_forwarder_never_raises_on_broadcast_failure() -> None:
    async def _bad_broadcast(message: dict) -> None:
        raise RuntimeError("ws gone")

    forwarder = ipc_bridge.cron_event_ws_forwarder(_bad_broadcast)

    async def _main() -> None:
        await forwarder(SwarmEvent("cron.mutated", "native-crons", {}))

    asyncio.run(_main())  # must not raise
