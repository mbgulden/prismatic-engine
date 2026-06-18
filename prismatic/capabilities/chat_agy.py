"""
Prismatic Engine — ChatAGYCapability
====================================

Read-only capability wrapper for AGY chat sessions. Probes the local
AGY installation (binary on PATH or OAuth token in ``~/.antigravity/``)
and exposes session listing primitives for the Schedule Observatory
and Command Center.

This is the additive half of GRO-1955. Mutation paths (sending a
prompt, follow-up, transcript retrieval) are deferred to a separate
GRO-1955 follow-up issue; this module only does observation and
minimal typed shape.

Design contract:
- Pure Python, urllib.request only — no agent harness imports.
- No subprocess calls to AGY for session listing (we don't have a
  reliable ``agy sessions list`` command yet; the live data path
  is stubbed). When AGY exposes a stable session listing API, this
  module is the place to wire it.
- list_sessions() returns an empty list until the live path exists;
  this is the documented v0.1 contract.
- get_session() returns None until the live path exists.
- check_status() reflects whether AGY itself is reachable.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ChatSession:
    """Typed shape for a single AGY chat session.

    Fields are minimal in v0.1; mutation events from the engine
    (start, progress, paused, killed, summarized, completed) will
    populate ``last_event_at`` and ``status`` in follow-up work.
    """

    id: str
    agent: str = "agy"
    status: str = "unknown"  # "running" | "paused" | "completed" | "failed" | "unknown"
    started_at: str = ""
    last_event_at: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("id", "agent", "status", "started_at")}


# Default search paths for the AGY OAuth token. The token is the
# canonical signal that AGY is installed and authenticated on this
# host — we don't need to invoke AGY to know it can be invoked.
_DEFAULT_AGY_OAUTH_PATHS = [
    Path("~/.antigravity/antigravity-oauth-token").expanduser(),
    Path("~/.gemini/antigravity-cli/antigravity-oauth-token").expanduser(),
    Path("~/.hermes/profiles/orchestrator/home/.antigravity/antigravity-oauth-token").expanduser(),
]


class ChatAGYCapability:
    """Read-only chat capability for AGY.

    Usage:
        cap = ChatAGYCapability()
        ok, msg = cap.check_status()  # True if AGY is reachable
        sessions = cap.list_sessions()  # [] in v0.1
        session = cap.get_session("id")  # None in v0.1
    """

    def __init__(self, agy_path: Optional[str] = None) -> None:
        self._agy_path = agy_path or os.environ.get("AGY_PATH") or shutil.which("agy")

    def check_status(self) -> tuple[bool, str]:
        """Check whether AGY is reachable on this host.

        Returns ``(True, "ok")`` if either:
          - ``agy`` is on PATH (via ``AGY_PATH`` env var or PATH lookup), or
          - The AGY OAuth token file exists at any of the known paths.
        Returns ``(False, reason)`` otherwise.
        """
        if self._agy_path and Path(self._agy_path).exists():
            return True, f"ok (agy binary at {self._agy_path})"
        for p in _DEFAULT_AGY_OAUTH_PATHS:
            if p.exists():
                return True, f"ok (OAuth token at {p})"
        return False, (
            "AGY is not reachable: no AGY_PATH env, no 'agy' on PATH, "
            "and no OAuth token at any known location."
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return the list of known AGY chat sessions.

        v0.1 contract: returns an empty list. The live data path
        (file-based session index or AGY CLI ``sessions list``) is
        not yet wired. This is by design — we ship the typed shape
        and gateway endpoint first, then layer in the live data
        in a follow-up without changing the API.
        """
        return []

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single AGY chat session by id, or None if not found.

        v0.1 contract: returns None for any id (no live data yet).
        """
        return None
