"""State backup helpers for Prismatic Engine.

The daily ``Prismatic Engine State Backup`` cron imports
``prismatic.backup.create_backup`` from a live Hermes profile.  Keep this
module dependency-light: it must run from a cron script with only the engine
checkout on ``sys.path``.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
DEFAULT_BACKUP_ROOT = Path(
    os.environ.get("PRISMATIC_BACKUP_DIR")
    or Path.home() / ".prismatic" / "backups" / "state"
)
DEFAULT_STATE_CANDIDATES = (
    DEFAULT_REPO_ROOT / "prismatic_state",
    Path(os.environ.get("PRISMATIC_STATE_DIR", ""))
    if os.environ.get("PRISMATIC_STATE_DIR")
    else None,
    DEFAULT_PRISMATIC_HOME / ".antigravity" / "swarm_locks.json",
)


def _existing_paths(paths: Iterable[Path | None]) -> list[Path]:
    """Return existing paths, preserving order and removing duplicates."""

    seen: set[Path] = set()
    existing: list[Path] = []
    for raw_path in paths:
        if raw_path is None:
            continue
        path = raw_path.expanduser().resolve()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        existing.append(path)
    return existing


def _arcname(path: Path) -> str:
    """Build a stable archive name that avoids absolute path extraction."""

    try:
        return str(path.relative_to(DEFAULT_REPO_ROOT))
    except ValueError:
        return "external/" + str(path).lstrip("/").replace("/", "__")


def create_backup(
    backup_root: str | os.PathLike[str] | None = None,
    state_paths: Iterable[str | os.PathLike[str]] | None = None,
) -> Path:
    """Create a compressed archive of Prismatic Engine runtime state.

    Parameters
    ----------
    backup_root:
        Directory that receives the ``prismatic-state-YYYYMMDD-HHMMSS.tar.gz``
        archive. Defaults to ``$PRISMATIC_BACKUP_DIR`` when set, otherwise
        ``$HOME/.prismatic/backups/state`` to match the existing cron contract.
    state_paths:
        Optional explicit files/directories to include. When omitted, the
        backup captures the repository ``prismatic_state/`` directory and the
        shared swarm lock registry when present.

    Returns
    -------
    pathlib.Path
        The completed archive path.

    Raises
    ------
    FileNotFoundError
        If no state source exists. This is an actionable cron failure instead
        of silently writing an empty archive.
    """

    root = Path(backup_root) if backup_root is not None else DEFAULT_BACKUP_ROOT
    root.mkdir(parents=True, exist_ok=True)

    if state_paths is None:
        candidates = list(DEFAULT_STATE_CANDIDATES)
    else:
        candidates = [Path(p) for p in state_paths]
    sources = _existing_paths(candidates)
    if not sources:
        raise FileNotFoundError("no Prismatic state files found to back up")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_path = root / f"prismatic-state-{timestamp}.tar.gz"

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.", suffix=".tmp", dir=str(root)
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with tarfile.open(temp_path, "w:gz") as archive:
            for source in sources:
                archive.add(source, arcname=_arcname(source), recursive=True)
        temp_path.replace(archive_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return archive_path


__all__ = ["create_backup"]
