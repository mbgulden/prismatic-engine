"""Plugin health helpers for Prismatic Engine.

This module converts plugin lifecycle-manager state into stable health payloads
for gateway/API callers. It intentionally treats telemetry as optional: plugin
lifecycle state is authoritative, and metrics are included only when a collector
with ``report_plugin_metrics`` is available.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from prismatic.plugins.lifecycle_manager import PluginLifecycleSandboxManager
from prismatic.telemetry import get_collector

logger = logging.getLogger("prismatic.plugin_health")


class _LifecycleManager(Protocol):
    def get_plugin_status(self, name: str) -> dict[str, Any]: ...


_EMPTY_METRICS = {
    "total_starts": 0,
    "total_crashes": 0,
    "avg_execution_time_ms": 0.0,
    "avg_memory_bytes": 0,
    "avg_cpu_seconds": 0.0,
}


def _normalize_state(raw_state: Any) -> str:
    if hasattr(raw_state, "value"):
        return str(raw_state.value)
    return str(raw_state or "")


def _overall_status(state: str) -> str:
    if state in {"RUNNING", "STARTING"}:
        return "healthy"
    if state in {"STOPPED", "STOPPING"}:
        return "stopped"
    if state == "FAILED":
        return "unhealthy"
    if state == "PURGED":
        return "removed"
    return "unknown"


def _plugin_metrics(plugin_name: str) -> dict[str, Any]:
    """Return optional telemetry metrics if the active collector supports them."""
    try:
        collector = get_collector()
        reporter = getattr(collector, "report_plugin_metrics", None)
        if reporter is None:
            return {}
        metrics = reporter(plugin_name)
        return metrics if isinstance(metrics, dict) else {}
    except Exception as exc:  # pragma: no cover - defensive observability path
        logger.warning("plugin health: telemetry lookup failed for %s: %s", plugin_name, exc)
        return {}


def get_plugin_health(
    plugin_name: str,
    lifecycle_manager: _LifecycleManager | None = None,
) -> dict[str, Any]:
    """Return a health snapshot for one plugin.

    Args:
        plugin_name: Plugin identifier.
        lifecycle_manager: Optional manager. If omitted, the default
            ``PluginLifecycleSandboxManager`` reads persisted state from
            ``$PRISMATIC_STATE_DIR/plugin_lifecycle.db``.

    Returns:
        A JSON-serializable health payload. Unknown plugins return
        ``{"status": "NOT_FOUND", "plugin_name": plugin_name}``.
    """
    manager = lifecycle_manager or PluginLifecycleSandboxManager()
    status: dict[str, Any]
    try:
        status = manager.get_plugin_status(plugin_name)
    except Exception as exc:
        logger.warning("plugin health: lifecycle lookup failed for %s: %s", plugin_name, exc)
        status = {"state": "NOT_FOUND", "name": plugin_name}

    state = _normalize_state(status.get("state"))
    metrics = _plugin_metrics(plugin_name)

    if state in {"", "NOT_FOUND"} and not metrics:
        return {"status": "NOT_FOUND", "plugin_name": plugin_name}

    if not state or state == "NOT_FOUND":
        state = _normalize_state(metrics.get("current_state"))

    started_at = float(status.get("started_at") or 0.0)
    uptime = 0.0
    if state in {"RUNNING", "STARTING"} and started_at > 0:
        uptime = max(0.0, time.time() - started_at)
    elif metrics.get("uptime_seconds") is not None:
        uptime = float(metrics.get("uptime_seconds") or 0.0)

    metric_payload = dict(_EMPTY_METRICS)
    for key in metric_payload:
        if key in metrics:
            metric_payload[key] = metrics[key]

    return {
        "status": _overall_status(state),
        "plugin_name": plugin_name,
        "state": state,
        "container_id": status.get("container_id", ""),
        "runtime": status.get("runtime", ""),
        "uptime_seconds": round(uptime, 1),
        "last_error": status.get("last_error", metrics.get("last_error", "")) or "",
        "metrics": metric_payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
