"""tests/test_native_crons.py — Unit tests for PE Native Crons engine, DAG validation, and recovery."""

import pytest
from prismatic.native_crons import (
    NativeCron,
    NativeCronStore,
    validate_cron_dag_cycles,
    mutate_native_cron,
    list_native_crons,
    CRON_STATE_ACTIVE,
    CRON_STATE_PAUSED,
)


def test_native_cron_instantiation() -> None:
    cron = NativeCron(
        id="test-cron-1",
        name="Test Native Cron",
        schedule="*/5 * * * *",
        command=["python3", "script.py"],
    )
    assert cron.id == "test-cron-1"
    assert cron.enabled is True
    assert cron.queue_state == "queued"
    d = cron.to_dict()
    assert d["display_command"] == "python3 script.py"


def test_validate_cron_dag_cycles() -> None:
    c1 = NativeCron(id="cron-a", name="A", schedule="manual", command=["true"], depends_on=["cron-b"])
    c2 = NativeCron(id="cron-b", name="B", schedule="manual", command=["true"], depends_on=["cron-a"])
    cycles = validate_cron_dag_cycles([c1, c2])
    assert len(cycles) >= 1
    assert "cron-a" in cycles[0] and "cron-b" in cycles[0]


def test_recover_native_cron_action(tmp_path) -> None:
    store_file = tmp_path / "native_crons.json"
    store = NativeCronStore(path=store_file)
    c1 = NativeCron(
        id="cron-rec-1",
        name="Recoverable Cron",
        schedule="manual",
        command=["python3", "-c", "print('recovered')"],
    )
    store.save([c1])

    res = store.mutate("cron-rec-1", "recover")
    assert res["success"] is True
    assert res["replays_count"] == 3
    assert res["cron"]["last_status"] == "success"
