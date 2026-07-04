"""
prismatic/plugin_health.py — Plugin health check endpoint and helper.

Provides:
  - :func:`get_plugin_health` — Returns plugin health dict.
  - :func:`health_http_handler` — CGI/WSGI-style HTTP handler for
    ``/api/v1/plugins/<name>/health``.

Designed to work with the existing gateway server or with a minimal
HTTP server started alongside the dispatcher.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .plugins.lifecycle_manager import PluginLifecycleSandboxManager
from .telemetry import get_collector

logger = logging.getLogger("prismatic.plugin_health")


def get_plugin_health(
    plugin_name: str,
    lifecycle_manager: PluginLifecycleSandboxManager | None = None,
) -> dict[str, Any]:
    """Return a health check response for a single plugin.

    Combines the lifecycle state with telemetry metrics to produce
    a comprehensive health snapshot.

    Args:
        plugin_name: Name of the plugin to check.
        lifecycle_manager: Optional lifecycle manager instance.
                           If None, only telemetry data is returned.

    Returns:
        Dict with ``status``, ``state``, ``uptime_seconds``,
        ``last_error``, ``metrics``, and ``timestamp``.
        Returns ``{"status": "NOT_FOUND", "plugin_name": plugin_name}``
        if no data exists for the plugin.
    """
    collector = get_collector()
    metrics = collector.report_plugin_metrics(plugin_name)

    state = ""
    raw_state: str | None = None
    uptime = 0.0
    last_error = ""

    if lifecycle_manager is not None:
        try:
            status = lifecycle_manager.get_plugin_status(plugin_name)
            raw_state = status.get("state", "")
            if raw_state and raw_state != "NOT_FOUND":
                state = raw_state
                last_error = status.get("last_error", "")
                started_at = status.get("started_at", 0)
                if state in ("RUNNING", "STARTING") and started_at:
                    uptime = time.time() - started_at
        except Exception as exc:
            logger.warning("health: lifecycle lookup failed for %s: %s", plugin_name, exc)

    # Fall back to telemetry-derived state
    if not state and metrics:
        state = metrics.get("current_state", "")
        last_error = metrics.get("last_error", "")
        uptime = metrics.get("uptime_seconds", 0.0)

    if not state and not metrics:
        return {"status": "NOT_FOUND", "plugin_name": plugin_name}

    # Determine overall health status
    if state in ("RUNNING", "STARTING"):
        overall = "healthy"
    elif state == "STOPPED":
        overall = "stopped"
    elif state == "FAILED":
        overall = "unhealthy"
    elif state == "PURGED":
        overall = "removed"
    else:
        overall = "unknown"

    return {
        "status": overall,
        "plugin_name": plugin_name,
        "state": state,
        "uptime_seconds": round(uptime, 1),
        "last_error": last_error,
        "metrics": {
            "total_starts": metrics["total_starts"] if metrics else 0,
            "total_crashes": metrics["total_crashes"] if metrics else 0,
            "avg_execution_time_ms": metrics["avg_execution_time_ms"] if metrics else 0.0,
            "avg_memory_bytes": metrics["avg_memory_bytes"] if metrics else 0,
            "avg_cpu_seconds": metrics["avg_cpu_seconds"] if metrics else 0.0,
        } if metrics else {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def health_http_handler(
    environ: dict[str, Any],
    start_response: Callable,
    lifecycle_manager: PluginLifecycleSandboxManager | None = None,
) -> list[bytes]:
    """WSGI-style HTTP handler for ``GET /api/v1/plugins/<name>/health``.

    Usage with a gateway server:

    .. code-block:: python

        from prismatic.plugin_health import health_http_handler

        def app(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path.startswith("/api/v1/plugins/") and path.endswith("/health"):
                return health_http_handler(environ, start_response, mgr)
            # ... other routes ...
    """
    path = environ.get("PATH_INFO", "")
    parts = path.strip("/").split("/")

    # Expected: api/v1/plugins/<name>/health
    plugin_name = parts[3] if len(parts) >= 4 else ""

    if environ.get("REQUEST_METHOD", "GET") != "GET":
        start_response("405 Method Not Allowed", [("Content-Type", "application/json")])
        return [json.dumps({"error": "Method not allowed"}).encode()]

    if not plugin_name:
        start_response("400 Bad Request", [("Content-Type", "application/json")])
        return [json.dumps({"error": "Missing plugin name"}).encode()]

    result = get_plugin_health(plugin_name, lifecycle_manager)
    status_code = "200 OK"
    if result.get("status") == "NOT_FOUND":
        status_code = "404 Not Found"
    elif result.get("status") == "unhealthy":
        status_code = "503 Service Unavailable"

    start_response(status_code, [("Content-Type", "application/json")])
    return [json.dumps(result, indent=2).encode()]


# Import time at module level for uptime calculation
import time
