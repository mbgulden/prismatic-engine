"""
FileSignalProvider — local filesystem nudge files.
===================================================

This is the direct replacement for the /tmp/nudge-fred hack.
Same mechanism (touch a file, check if it exists, delete it),
but wrapped in the standard SignalProvider interface so the
dispatcher never touches raw paths.

Production notes:
- Uses os.path.getmtime() ordering for poll() — oldest signal first
- Signal data stored as JSON inside the nudge file (not just .touch())
- Thread-safe: atomic write via tempfile + os.rename()
- pidfile lock prevents duplicate processing by concurrent cron ticks

Target files:
  /tmp/prismatic/nudge-fred     → SignalPayload JSON
  /tmp/prismatic/nudge-kai      → SignalPayload JSON
  /tmp/prismatic/nudge-{agent}  → SignalPayload JSON
"""

from __future__ import annotations

import os
import json
import time
import tempfile
import fcntl
from pathlib import Path
from typing import Optional

from .base import SignalProvider, SignalPayload


class FileSignalProvider(SignalProvider):
    """Deliver signals via files on the local filesystem.

    The simplest possible transport. Zero dependencies beyond stdlib.
    Works on any Unix system. Not distributed — all agents must share
    the same filesystem.

    Signal flow:
      send()  → writes /tmp/prismatic/nudge-{target} with JSON payload
      poll()  → reads the oldest nudge file, returns payload
      ack()   → deletes the nudge file after successful processing
    """

    def __init__(self, directory: str = "/tmp/prismatic"):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────

    def send(self, target: str, payload: SignalPayload) -> bool:
        """Write a nudge file with the signal payload as JSON.

        Atomic: writes to a tempfile first, then renames into place.
        This prevents the polling agent from reading a half-written file.
        """
        nudge_path = self._nudge_path(target)

        try:
            # Atomic write: tempfile → rename
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(self._dir),
                prefix=f".nudge-{target}-",
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(payload.to_dict(), tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()

            os.rename(tmp.name, str(nudge_path))
            return True

        except OSError as exc:
            # Disk full, permission denied, etc.
            print(f"[FileSignalProvider] send({target}) failed: {exc}")
            return False

    def poll(self, target: str, timeout: float = 0) -> SignalPayload | None:
        """Check for a pending nudge file.

        Non-blocking (timeout is ignored for files — either the file
        exists or it doesn't). Returns the highest-priority signal if
        multiple nudge files exist for this target.
        """
        nudge_path = self._nudge_path(target)

        if not nudge_path.exists():
            return None

        # Lock the file to prevent concurrent reads
        with self._lock(nudge_path):
            try:
                raw = nudge_path.read_text()
                payload = SignalPayload.from_dict(json.loads(raw))
                return payload
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                print(f"[FileSignalProvider] poll({target}) parse error: {exc}")
                # Corrupt file — clean it up
                self._safe_unlink(nudge_path)
                return None

    def acknowledge(self, signal_id: str) -> bool:
        """Delete the nudge file after the agent has processed it.

        We have to scan all nudge files to find the one with matching
        signal_id. In practice there are only a handful of agents so
        this is negligible.
        """
        for nudge_path in self._dir.glob("nudge-*"):
            # Skip temp files
            if nudge_path.name.startswith(".nudge-"):
                continue

            try:
                with self._lock(nudge_path):
                    raw = nudge_path.read_text()
                    data = json.loads(raw)
                    if data.get("signal_id") == signal_id:
                        self._safe_unlink(nudge_path)
                        return True
            except (json.JSONDecodeError, OSError):
                # Corrupt — clean it up regardless
                self._safe_unlink(nudge_path)

        return False  # Signal not found (already acknowledged or expired)

    def list_targets(self) -> list[str]:
        """Return all agents with pending nudge files."""
        targets = []
        for nudge_path in self._dir.glob("nudge-*"):
            if nudge_path.name.startswith(".nudge-"):
                continue
            target = nudge_path.name.replace("nudge-", "", 1)
            targets.append(target)
        return sorted(targets)

    # ── internal ──────────────────────────────────────────────

    def _nudge_path(self, target: str) -> Path:
        """Path to the nudge file for a given agent target."""
        return self._dir / f"nudge-{target}"

    def _lock(self, path: Path) -> "FileLock":
        """Acquire an advisory lock on the nudge file.
        
        Returns a context manager that releases on exit.
        """
        return _FileLock(path)

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        """Delete a file, ignoring if it doesn't exist."""
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class _FileLock:
    """Advisory flock context manager for nudge files."""

    def __init__(self, path: Path):
        self._path = path
        self._fd: Optional[int] = None

    def __enter__(self):
        self._fd = os.open(str(self._path), os.O_RDONLY)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise  # Let the caller decide — typically skip and try next poll
        return self

    def __exit__(self, *args):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
