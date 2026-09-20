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

import json
import os
import re
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
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
            return True, f"ok (agy binary at {self._agy_path}; OAuth token available)"
        for p in _DEFAULT_AGY_OAUTH_PATHS:
            if p.exists():
                return True, f"ok (OAuth token at {p})"
        return False, (
            "AGY is not reachable: no AGY_PATH env, no 'agy' on PATH, "
            "and no OAuth token at any known location."
        )

    @classmethod
    def _get_brain_dirs(cls) -> list[Path]:
        if "AGY_BRAIN_DIR" in os.environ:
            p = Path(os.environ["AGY_BRAIN_DIR"]).expanduser()
            return [p] if p.is_dir() else []
        candidates = [
            Path("~/.gemini/antigravity-cli/brain").expanduser(),
            Path("~/.antigravity/brain").expanduser(),
        ]
        return [c for c in candidates if c and c.is_dir()]

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the list of known AGY chat sessions discovered from local brain archives."""
        brain_dirs = self._get_brain_dirs()
        if not brain_dirs:
            return []

        entries: list[tuple[float, Path]] = []
        for bdir in brain_dirs:
            try:
                for entry in bdir.iterdir():
                    if entry.is_dir() and not entry.name.startswith("."):
                        try:
                            entries.append((entry.stat().st_mtime, entry))
                        except OSError:
                            continue
            except OSError:
                continue

        entries.sort(key=lambda item: item[0], reverse=True)

        sessions: list[dict[str, Any]] = []
        for mtime_ts, entry in entries[:limit]:
            mtime = datetime.fromtimestamp(mtime_ts, timezone.utc).isoformat()
            try:
                ctime_ts = entry.stat().st_ctime
                ctime = datetime.fromtimestamp(ctime_ts, timezone.utc).isoformat()
            except OSError:
                ctime = mtime

            label = None
            turn_count = 0
            transcript_file = entry / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript_file.exists():
                try:
                    with open(transcript_file, "r", encoding="utf-8", errors="ignore") as f:
                        first_line = f.readline()
                        if first_line:
                            turn_count = 1 + sum(1 for _ in f)
                            try:
                                first_data = json.loads(first_line)
                                raw_content = first_data.get("content") or ""
                                clean = re.sub(r"<[^>]+>", " ", raw_content).strip()
                                label = clean[:80] if clean else None
                            except Exception:
                                pass
                except Exception:
                    pass

            session = ChatSession(
                id=entry.name,
                agent="agy",
                status="completed",
                started_at=ctime,
                last_event_at=mtime,
                label=label or entry.name,
            )
            data = session.to_dict()
            data["turn_count"] = turn_count
            sessions.append(data)

        return sessions

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single AGY chat session by id with full metadata, or None if not found."""
        clean_id = session_id.strip()
        for bdir in self._get_brain_dirs():
            cand = bdir / clean_id
            if cand.is_dir():
                try:
                    mtime = datetime.fromtimestamp(cand.stat().st_mtime, timezone.utc).isoformat()
                    ctime = datetime.fromtimestamp(cand.stat().st_ctime, timezone.utc).isoformat()
                except OSError:
                    mtime = ""
                    ctime = ""

                label = None
                turn_count = 0
                transcript_file = cand / ".system_generated" / "logs" / "transcript.jsonl"
                first_prompt = ""
                last_response = ""
                if transcript_file.exists():
                    try:
                        with open(transcript_file, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                            turn_count = len(lines)
                            if lines:
                                try:
                                    first_data = json.loads(lines[0])
                                    first_prompt = re.sub(r"<[^>]+>", " ", first_data.get("content") or "").strip()[:200]
                                    label = first_prompt[:80]
                                except Exception:
                                    pass
                                try:
                                    last_data = json.loads(lines[-1])
                                    last_response = (last_data.get("content") or "")[:200]
                                except Exception:
                                    pass
                    except Exception:
                        pass

                return {
                    "id": clean_id,
                    "agent": "agy",
                    "status": "completed",
                    "started_at": ctime,
                    "last_event_at": mtime,
                    "label": label or clean_id,
                    "turn_count": turn_count,
                    "first_prompt": first_prompt,
                    "last_response": last_response,
                    "session_path": str(cand),
                }

        return None
