from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from prismatic.backup import create_backup


def test_create_backup_archives_existing_state_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "fleet_watchdog_state.json").write_text(
        '{"ok": true}', encoding="utf-8"
    )
    lock_file = tmp_path / "swarm_locks.json"
    lock_file.write_text("[]", encoding="utf-8")

    archive = create_backup(
        backup_root=tmp_path / "backups",
        state_paths=[state_dir, lock_file],
    )

    assert archive.exists()
    assert archive.name.startswith("prismatic-state-")
    assert archive.suffixes[-2:] == [".tar", ".gz"]

    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())

    assert any(name.endswith("fleet_watchdog_state.json") for name in names)
    assert any(name.endswith("swarm_locks.json") for name in names)
    assert all(not name.startswith("/") for name in names)


def test_create_backup_refuses_empty_source_set(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no Prismatic state files"):
        create_backup(
            backup_root=tmp_path / "backups",
            state_paths=[tmp_path / "missing"],
        )
