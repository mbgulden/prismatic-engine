"""Prismatic Engine state backup helpers.

The orchestrator cron job ``prismatic_backup.py`` imports ``create_backup``
from this module. Keep the module dependency-light so it can run from a cron
script that only prepends the repository root to ``sys.path``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_STATE_DIR = _REPO_ROOT / "prismatic_state"
_DEFAULT_PRISMATIC_HOME = Path.home() / ".prismatic"
_DEFAULT_BACKUP_DIR = _DEFAULT_PRISMATIC_HOME / "backups" / "state"


def _resolve_path(value: str | os.PathLike[str] | None, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default
    return Path(value).expanduser().resolve()


def _iter_sources(
    state_dir: Path, prismatic_home: Path, backup_dir: Path
) -> list[Path]:
    """Return existing state roots to include in the backup archive."""
    candidates = [
        state_dir,
        prismatic_home / "db",
        prismatic_home / "bus",
        prismatic_home / "curator",
        prismatic_home / "prismatic_state",
        prismatic_home / "quota_state.db",
    ]
    sources: list[Path] = []
    seen: set[Path] = set()
    backup_dir_resolved = backup_dir.resolve()
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if not path.exists() or path in seen:
            continue
        # Never back up the backup output directory into itself.
        try:
            if path == backup_dir_resolved or backup_dir_resolved.is_relative_to(path):
                continue
        except ValueError:
            pass
        seen.add(path)
        sources.append(path)
    return sources


def _is_sqlite_file(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    return name.endswith((".db", ".sqlite", ".sqlite3")) or ".db." in name


def _copy_sqlite_database(src: Path, dst: Path) -> bool:
    """Copy a live SQLite database using the backup API.

    Returns ``True`` when the SQLite backup succeeded. Callers fall back to a
    byte-for-byte file copy for non-SQLite files that merely happen to use a
    database-looking suffix.
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15.0)
        target = sqlite3.connect(str(dst), timeout=15.0)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return True
    except sqlite3.DatabaseError:
        return False


def _copy_tree_for_archive(
    source: Path, staging_root: Path, arc_prefix: str
) -> list[dict[str, object]]:
    """Copy source into staging and return manifest entries."""
    entries: list[dict[str, object]] = []
    if source.is_file():
        files: Iterable[Path] = [source]
        base = source.parent
    else:
        files = (p for p in source.rglob("*") if p.is_file())
        base = source

    for src in files:
        rel = src.relative_to(base)
        arc_rel = (
            Path(arc_prefix) / source.name / rel
            if source.is_dir()
            else Path(arc_prefix) / source.name
        )
        dst = staging_root / arc_rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        copied_as_sqlite = False
        if _is_sqlite_file(src):
            copied_as_sqlite = _copy_sqlite_database(src, dst)
        if not copied_as_sqlite:
            shutil.copy2(src, dst)

        entries.append(
            {
                "path": str(arc_rel),
                "source": str(src),
                "bytes": dst.stat().st_size,
                "sqlite_backup_api": copied_as_sqlite,
            }
        )
    return entries


def create_backup(
    output_dir: str | os.PathLike[str] | None = None,
    *,
    state_dir: str | os.PathLike[str] | None = None,
    prismatic_home: str | os.PathLike[str] | None = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Create a compressed archive of Prismatic Engine state.

    Parameters are optional so the orchestrator cron script can simply call
    ``create_backup()``. Runtime overrides are available via:

    - ``PRISMATIC_STATE_BACKUP_DIR`` for the archive destination.
    - ``PRISMATIC_STATE_DIR`` for the repo-local state directory.
    - ``PRISMATIC_HOME`` for the canonical ``~/.prismatic`` state root.

    The function returns the created ``.tar.gz`` path and raises a clear
    exception if no state source exists.
    """
    resolved_state_dir = _resolve_path(
        state_dir or os.environ.get("PRISMATIC_STATE_DIR"), _DEFAULT_STATE_DIR
    )
    resolved_home = _resolve_path(
        prismatic_home or os.environ.get("PRISMATIC_HOME"), _DEFAULT_PRISMATIC_HOME
    )
    resolved_output_dir = _resolve_path(
        output_dir or os.environ.get("PRISMATIC_STATE_BACKUP_DIR"), _DEFAULT_BACKUP_DIR
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    sources = _iter_sources(resolved_state_dir, resolved_home, resolved_output_dir)
    if not sources:
        raise FileNotFoundError(
            "No Prismatic state sources found; checked "
            f"{resolved_state_dir} and {resolved_home}"
        )

    stamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    archive_path = resolved_output_dir / f"prismatic-state-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="prismatic-backup-") as tmp:
        staging = Path(tmp) / "archive"
        manifest_entries: list[dict[str, object]] = []
        for source in sources:
            if source == resolved_state_dir:
                prefix = "repo"
            else:
                prefix = "home"
            manifest_entries.extend(_copy_tree_for_archive(source, staging, prefix))

        manifest = {
            "created_at_epoch": int(time.time()),
            "repo_root": str(_REPO_ROOT),
            "state_dir": str(resolved_state_dir),
            "prismatic_home": str(resolved_home),
            "source_count": len(sources),
            "file_count": len(manifest_entries),
            "files": manifest_entries,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(staging, arcname="prismatic-state")

    return archive_path


__all__ = ["create_backup"]
