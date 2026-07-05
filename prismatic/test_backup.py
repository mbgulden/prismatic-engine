from __future__ import annotations

import tarfile

from prismatic.backup import create_backup


def test_create_backup_archives_repo_and_home_state(tmp_path):
    state_dir = tmp_path / "repo_state"
    state_dir.mkdir()
    (state_dir / "overview.txt").write_text("alive\n")

    home = tmp_path / "home" / ".prismatic"
    db_dir = home / "db"
    db_dir.mkdir(parents=True)
    (db_dir / "event_router.db").write_bytes(b"not really sqlite, fallback copy\n")

    out_dir = tmp_path / "backups"
    archive = create_backup(
        output_dir=out_dir,
        state_dir=state_dir,
        prismatic_home=home,
        timestamp="20260705-101010",
    )

    assert archive == out_dir / "prismatic-state-20260705-101010.tar.gz"
    assert archive.exists()

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())

    assert "prismatic-state/manifest.json" in names
    assert "prismatic-state/repo/repo_state/overview.txt" in names
    assert "prismatic-state/home/db/event_router.db" in names


def test_create_backup_rejects_missing_sources(tmp_path):
    missing_state = tmp_path / "missing-state"
    missing_home = tmp_path / "missing-home"

    try:
        create_backup(
            output_dir=tmp_path / "backups",
            state_dir=missing_state,
            prismatic_home=missing_home,
            timestamp="20260705-101010",
        )
    except FileNotFoundError as exc:
        assert "No Prismatic state sources found" in str(exc)
    else:
        raise AssertionError("create_backup should reject empty state roots")
