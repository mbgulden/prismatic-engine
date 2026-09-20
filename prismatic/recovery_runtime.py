"""Read-only runtime evidence for the dashboard recovery surface.

The recovery API must not infer a service outage from an absent JSON field. This
adapter reports systemd state explicitly and keeps heartbeat/pool evidence
unavailable until a durable producer actually supplies it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

SERVICE_NAME = "prismatic-consumer.service"
HEARTBEAT_STALE_SECONDS = 120


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )


def _heartbeat_status(path: Path, *, now: float) -> dict[str, Any]:
    """Return secret-safe heartbeat metadata without exposing a local path."""
    if not path.is_file():
        return {
            "available": False,
            "exists": False,
            "status": "unavailable",
            "fresh": None,
            "age_seconds": None,
            "source": "durable_heartbeat_not_present",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(payload.get("timestamp", payload.get("updated_at_epoch")))
        age = max(0, int(now - timestamp))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "available": False,
            "exists": True,
            "status": "unreadable",
            "fresh": None,
            "age_seconds": None,
            "source": "durable_heartbeat_unreadable",
        }
    fresh = age <= HEARTBEAT_STALE_SECONDS
    return {
        "available": True,
        "exists": True,
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "age_seconds": age,
        "source": "durable_heartbeat_file",
    }


def consumer_runtime_status(
    *,
    service_name: str = SERVICE_NAME,
    heartbeat_path: Path | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _default_runner,
    now: float | None = None,
) -> dict[str, Any]:
    """Return explicit, fail-soft consumer runtime evidence for the public UI."""
    try:
        result = runner(["systemctl", "is-active", service_name])
        service_state = (result.stdout or "").strip().lower() or "unknown"
        active = result.returncode == 0 and service_state == "active"
        systemd_available = True
    except (OSError, subprocess.SubprocessError):
        service_state = "unknown"
        active = False
        systemd_available = False

    if heartbeat_path is None:
        state_dir = Path(
            os.environ.get("PRISMATIC_STATE_DIR", "~/.prismatic/state")
        ).expanduser()
        heartbeat_path = state_dir / "consumer-heartbeat.json"

    return {
        "runtime_source": "systemd_read_only+durable_heartbeat",
        "service_name": service_name,
        "systemd_available": systemd_available,
        "systemd_active": active,
        "systemd_state": service_state,
        "heartbeat": _heartbeat_status(
            heartbeat_path, now=time.time() if now is None else now
        ),
        "pool_stats": {
            "available": False,
            "status": "unavailable",
            "live_count": None,
            "total_skipped_dlq": None,
            "source": "no_durable_pool_snapshot",
        },
    }
