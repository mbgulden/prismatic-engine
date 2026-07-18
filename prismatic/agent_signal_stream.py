"""Durable assigned-agent signal stream for dashboard/operator visibility.

This module is intentionally small and dependency-light. It records compact
wake/execute/result breadcrumbs plus short transcript/log excerpts so the
Prismatic dashboard can show the same operational "cockpit" stream that Michael
sees in Telegram while Linear remains the durable source of truth.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 6000
TOKEN_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|bearer|cookie|session[_-]?key)\s*[:=]\s*([^\s`'\"]+)"
)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))


def signal_stream_path() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_AGENT_SIGNAL_STREAM_LOG",
            _state_dir() / "agent_signal_stream.jsonl",
        )
    )


def _redact(value: Any) -> str:
    text = str(value or "")
    text = TOKEN_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return text[-MAX_TEXT_CHARS:]


def record_agent_signal(
    *,
    agent: str,
    event_type: str,
    issue_id: str = "",
    status: str = "",
    message: str = "",
    run_id: str = "",
    source: str = "assigned-agent",
    severity: str = "info",
    log_path: str = "",
    transcript: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "id": f"sig-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "timestamp": utc_now(),
        "agent": str(agent or "unknown").lower(),
        "event_type": str(event_type or status or "event"),
        "status": str(status or event_type or "event"),
        "issue_id": str(issue_id or ""),
        "run_id": str(run_id or ""),
        "source": str(source or "assigned-agent"),
        "severity": str(severity or "info"),
        "message": _redact(message),
        "log_path": _redact(log_path),
        "transcript": _redact(transcript),
        "metadata": metadata or {},
    }
    path = signal_stream_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True, default=str) + "\n")
    return item


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            items.append(raw)
    return items


def _tail_file(path_text: str, *, max_chars: int = 2400) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return ""
    allowed = [Path("/tmp"), _state_dir(), Path.cwd()]
    try:
        resolved = path.resolve()
        if not any(
            str(resolved).startswith(str(base.resolve()))
            for base in allowed
            if base.exists()
        ):
            return ""
    except OSError:
        return ""
    try:
        return _redact(path.read_text(encoding="utf-8", errors="replace")[-max_chars:])
    except OSError:
        return ""


def list_agent_signals(
    *, limit: int = 200, agent: str | None = None, include_log_tails: bool = True
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 200), 1000))
    agent_filter = (agent or "").strip().lower()
    items = _read_jsonl(signal_stream_path())
    if agent_filter and agent_filter != "all":
        items = [
            item
            for item in items
            if str(item.get("agent") or "").lower() == agent_filter
        ]
    items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    items = items[:safe_limit]
    if include_log_tails:
        for item in items:
            if item.get("log_path") and not item.get("transcript"):
                item["transcript"] = _tail_file(str(item.get("log_path") or ""))
    by_agent: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for item in items:
        a = str(item.get("agent") or "unknown").lower()
        by_agent.setdefault(a, []).append(item)
        counts[a] = counts.get(a, 0) + 1
    return {
        "source": "prismatic.agent_signal_stream",
        "generated_at": utc_now(),
        "count": len(items),
        "limit": safe_limit,
        "agents": sorted(by_agent),
        "counts": counts,
        "items": items,
        "by_agent": by_agent,
    }
