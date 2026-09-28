"""tests/test_native_cron_auto_reexport.py — WI-2: mutate() re-exports the managed block.

Pausing/deactivating/deleting a cron must remove its line from the
PRISMATIC_NATIVE_CRONS crontab block; resuming/activating must restore it —
without a human re-running install_native_crons.py. The re-export is
best-effort: a missing/unreadable crontab or a write failure must never fail
the dashboard mutation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismatic import native_crons
from prismatic.native_crons import (
    CRONTAB_BLOCK_BEGIN,
    CRONTAB_BLOCK_END,
    NativeCron,
    NativeCronStore,
    create_native_cron,
)


@pytest.fixture()
def fake_crontab(monkeypatch: pytest.MonkeyPatch) -> dict:
    """In-memory stand-in for the user crontab (no `crontab` binary here)."""
    state: dict = {"content": "", "writes": 0}

    def _read():
        return state["content"]

    def _write(content: str) -> None:
        state["writes"] += 1
        state["content"] = content

    monkeypatch.setattr(native_crons, "read_user_crontab", _read)
    monkeypatch.setattr(native_crons, "write_user_crontab", _write)
    return state


def _active_store(tmp_path: Path) -> NativeCronStore:
    store = NativeCronStore(path=tmp_path / "native_crons.json")
    store.save([
        NativeCron(id="test.pause-me", name="Pause me", schedule="*/5 * * * *",
                   command=["echo", "hi"]),
    ])
    return store


def _install_current(store: NativeCronStore, fake_crontab: dict) -> None:
    """Seed the fake crontab with the current managed block, like install_native_crons.py."""
    assert native_crons.refresh_system_crontab(store) is True
    assert "test.pause-me" in fake_crontab["content"]


def test_pause_removes_line_from_crontab(tmp_path: Path, fake_crontab: dict) -> None:
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)

    result = store.mutate("test.pause-me", "pause")
    assert result["success"] is True

    content = fake_crontab["content"]
    assert CRONTAB_BLOCK_BEGIN in content and CRONTAB_BLOCK_END in content
    assert "test.pause-me" not in content


def test_resume_restores_line_in_crontab(tmp_path: Path, fake_crontab: dict) -> None:
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)
    store.mutate("test.pause-me", "pause")
    assert "test.pause-me" not in fake_crontab["content"]

    store.mutate("test.pause-me", "resume")
    assert "test.pause-me" in fake_crontab["content"]


def test_deactivate_removes_line_from_crontab(tmp_path: Path, fake_crontab: dict) -> None:
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)
    store.mutate("test.pause-me", "deactivate")
    assert "test.pause-me" not in fake_crontab["content"]


def test_delete_removes_line_from_crontab(tmp_path: Path, fake_crontab: dict) -> None:
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)
    store.mutate("test.pause-me", "delete")
    assert "test.pause-me" not in fake_crontab["content"]


def test_run_action_does_not_rewrite_crontab(tmp_path: Path, fake_crontab: dict) -> None:
    """A manual `run` changes no scheduling state — no crontab churn."""
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)
    writes_before = fake_crontab["writes"]
    store.mutate("test.pause-me", "run")
    assert fake_crontab["writes"] == writes_before


def test_no_crontab_means_no_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev machine with no readable crontab: mutation still succeeds."""
    writes = []

    monkeypatch.setattr(native_crons, "read_user_crontab", lambda: None)
    monkeypatch.setattr(native_crons, "write_user_crontab", writes.append)

    store = _active_store(tmp_path)
    result = store.mutate("test.pause-me", "pause")
    assert result["success"] is True
    assert store.get("test.pause-me").state == "paused"
    assert writes == []


def test_crontab_write_failure_does_not_fail_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native_crons, "read_user_crontab", lambda: "old\n")

    def _boom(content: str) -> None:
        raise RuntimeError("crontab exploded")

    monkeypatch.setattr(native_crons, "write_user_crontab", _boom)

    store = _active_store(tmp_path)
    result = store.mutate("test.pause-me", "pause")
    assert result["success"] is True
    assert store.get("test.pause-me").state == "paused"


def test_create_triggers_reexport(tmp_path: Path, fake_crontab: dict) -> None:
    store = _active_store(tmp_path)
    _install_current(store, fake_crontab)

    create_native_cron(
        {"id": "test.brand-new", "name": "New", "schedule": "0 1 * * *",
         "command": ["echo", "new"]},
        store=store,
    )
    assert "test.brand-new" in fake_crontab["content"]
    assert "test.pause-me" in fake_crontab["content"]


def test_replace_managed_block_round_trip() -> None:
    """The extracted block helper keeps install_native_crons.py semantics."""
    existing = "MAILTO=ops@example.com\n"
    block = f"{CRONTAB_BLOCK_BEGIN}\n* * * * * echo hi\n{CRONTAB_BLOCK_END}\n"
    result = native_crons.replace_crontab_managed_block(existing, block)
    assert result.startswith("MAILTO=ops@example.com")
    assert result.count(CRONTAB_BLOCK_BEGIN) == 1
    assert "echo hi" in result

    again = native_crons.replace_crontab_managed_block(result, block.replace("hi", "yo"))
    assert "echo hi" not in again
    assert "echo yo" in again
    assert again.count(CRONTAB_BLOCK_BEGIN) == 1
