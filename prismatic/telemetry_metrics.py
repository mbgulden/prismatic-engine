"""
prismatic/telemetry_metrics.py — Prometheus-style metrics endpoint for plugin gauges.

Provides a lightweight ``/metrics`` HTTP server that can run as a background
thread alongside the dispatcher.

Usage in dispatcher::

    from prismatic.telemetry_metrics import start_metrics_server
    start_metrics_server(port=9090)

Then ``curl localhost:9090/metrics`` returns:

    # HELP plugin_start_count Total starts per plugin
    # TYPE plugin_start_count gauge
    plugin_start_count{plugin="my-plugin"} 5
    # HELP plugin_crash_count Total crashes per plugin
    # TYPE plugin_crash_count gauge
    plugin_crash_count{plugin="my-plugin"} 2
    ...
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from datetime import datetime, timezone
from typing import Any

from .telemetry import get_collector

logger = logging.getLogger("prismatic.telemetry_metrics")

METRICS_PORT = int(os.environ.get("PRISMATIC_METRICS_PORT", "9090"))


def _gauge(name: str, help_text: str, labels: dict[str, str], value: float | int) -> str:
    """Format a single Prometheus gauge line."""
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} gauge\n"
        f"{name}{{{label_str}}} {value}\n"
    )


def _render_metrics() -> str:
    """Render all plugin metrics as Prometheus text format.

    Queries the TelemetryCollector and formats every metric
    as a set of Prometheus gauge lines.
    """
    collector = get_collector()
    all_metrics = collector.list_plugin_metrics()
    lines: list[str] = []

    # ── Plugin start count (cumulative) ──────────────────────────
    help_start = "Total starts per plugin"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_start_count", help_start,
            {"plugin": m["plugin_name"]},
            m["total_starts"],
        ))

    # ── Plugin crash count (cumulative) ──────────────────────────
    help_crash = "Total crashes per plugin"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_crash_count", help_crash,
            {"plugin": m["plugin_name"]},
            m["total_crashes"],
        ))

    # ── Plugin execution time (ms) ───────────────────────────────
    help_exec = "Average execution time per plugin in milliseconds"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_execution_time_ms", help_exec,
            {"plugin": m["plugin_name"]},
            m["avg_execution_time_ms"],
        ))

    # ── Plugin memory (bytes) ────────────────────────────────────
    help_mem = "Average memory usage per plugin in bytes"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_memory_bytes", help_mem,
            {"plugin": m["plugin_name"]},
            m["avg_memory_bytes"],
        ))

    # ── Plugin CPU (seconds) ─────────────────────────────────────
    help_cpu = "Average CPU seconds per plugin"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_cpu_seconds", help_cpu,
            {"plugin": m["plugin_name"]},
            m["avg_cpu_seconds"],
        ))

    # ── Plugin uptime (seconds) ──────────────────────────────────
    help_uptime = "Current uptime in seconds for running plugins"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_uptime_seconds", help_uptime,
            {"plugin": m["plugin_name"], "state": m["current_state"]},
            m["uptime_seconds"],
        ))

    # ── Plugin event count (total observations) ──────────────────
    help_events = "Total telemetry events recorded per plugin"
    for m in all_metrics:
        lines.append(_gauge(
            "plugin_event_count", help_events,
            {"plugin": m["plugin_name"]},
            m["event_count"],
        ))

    if not lines:
        lines.append("# No plugin metrics available\n")

    return "\n" + "".join(lines) + "\n"


def _metrics_http_server(port: int) -> None:
    """Simple single-threaded HTTP server for Prometheus scraping.

    Handles ``GET /metrics`` and returns Prometheus text format.
    All other paths return 404.
    """
    # ── Only run in background; socket errors are acceptable ─────
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_sock.bind(("0.0.0.0", port))
        server_sock.listen(5)
        server_sock.settimeout(1.0)
    except OSError as exc:
        logger.warning("Cannot start metrics server on port %d: %s", port, exc)
        return

    logger.info("Prometheus metrics endpoint listening on :%d/metrics", port)

    while True:
        try:
            client_sock, addr = server_sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            client_sock.settimeout(5.0)
            data = client_sock.recv(4096)
            if data:
                request_line = data.split(b"\r\n")[0].decode("utf-8", errors="replace")
                method, path, _ = request_line.split(" ", 2)

                if method == "GET" and path == "/metrics":
                    body = _render_metrics().encode()
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/plain; version=0.0.4\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"\r\n"
                        + body
                    )
                else:
                    body = b"404 Not Found\n"
                    response = (
                        b"HTTP/1.1 404 Not Found\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"\r\n"
                        + body
                    )

                client_sock.sendall(response)
        except Exception as exc:
            logger.debug("Metrics HTTP request error: %s", exc)
        finally:
            try:
                client_sock.close()
            except OSError:
                pass


def start_metrics_server(port: int | None = None) -> threading.Thread | None:
    """Start the Prometheus metrics HTTP server in a daemon thread.

    Args:
        port: TCP port (default: ``PRISMATIC_METRICS_PORT`` env or 9090).

    Returns:
        The daemon thread handle, or ``None`` if the port is already bound.
    """
    actual_port = port if port is not None else METRICS_PORT

    # Quick port check before spawning thread
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        test_sock.bind(("0.0.0.0", actual_port))
        test_sock.close()
    except OSError:
        logger.warning("Port %d already in use — metrics server skipped", actual_port)
        return None

    thread = threading.Thread(
        target=_metrics_http_server,
        args=(actual_port,),
        daemon=True,
        name="metrics-server",
    )
    thread.start()
    logger.info("Prometheus metrics server started on :%d/metrics", actual_port)
    return thread
