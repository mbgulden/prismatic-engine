"""
prismatic/telemetry/metrics.py — Prometheus Metrics Catalog

Thread-safe metric registry for the Prismatic Engine observability stack.
All metrics are registered at module import time via prometheus_client.

Phase 4.2 — Prometheus Metrics Endpoint (GRO-1581)

Metrics:
- prismatic_agent_launches_total    (Counter, agent_type)
- prismatic_cycle_duration_seconds  (Histogram, step)
- prismatic_active_locks            (Gauge, repo)
- prismatic_credit_consumption_total (Counter, project)
- prismatic_circuit_breakers_tripped (Gauge, breaker_name)
- prismatic_agent_duration_seconds  (Histogram, agent_id, step)

Usage:
    from prismatic.telemetry.metrics import AGENT_LAUNCHES_TOTAL
    AGENT_LAUNCHES_TOTAL.labels(agent_type="ned").inc()
"""

from prometheus_client import Counter, Gauge, Histogram

# ── 1. Agent launches — counter by agent type ──────────────────
AGENT_LAUNCHES_TOTAL = Counter(
    "prismatic_agent_launches_total",
    "Total number of agent runs dispatched, labelled by agent type.",
    ["agent_type"],
)

# ── 2. Cycle duration — histogram by step ─────────────────────
CYCLE_DURATION_SECONDS = Histogram(
    "prismatic_cycle_duration_seconds",
    "Duration of each step in the dispatch cycle (poll, route, launch).",
    ["step"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, float("inf")),
)

# ── 3. Active file locks — gauge by repo ──────────────────────
ACTIVE_LOCKS = Gauge(
    "prismatic_active_locks",
    "Number of currently leased file locks in the swarm registry.",
    ["repo"],
)

# ── 4. Credit consumption — counter by project ─────────────────
CREDIT_CONSUMPTION_TOTAL = Counter(
    "prismatic_credit_consumption_total",
    "Cumulative credits spent, labelled by project.",
    ["project"],
)

# ── 5. Agent invocation duration — histogram by agent_id + step
#    (kept from the original enterprise-observability-plan spec)
AGENT_DURATION_SECONDS = Histogram(
    "prismatic_agent_duration_seconds",
    "Time spent by each agent in each of the dispatch steps.",
    ["agent_id", "step"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, float("inf")),
)

# ── 6. Circuit breaker status — gauge by breaker name ─────────
CIRCUIT_BREAKERS_TRIPPED = Gauge(
    "prismatic_circuit_breakers_tripped",
    "Binary indicator (0 or 1) of whether each circuit breaker is tripped.",
    ["breaker_name"],
)

# ── Convenience: all metrics for generate_latest() scanning ────
_ALL_METRICS = [
    AGENT_LAUNCHES_TOTAL,
    CYCLE_DURATION_SECONDS,
    ACTIVE_LOCKS,
    CREDIT_CONSUMPTION_TOTAL,
    AGENT_DURATION_SECONDS,
    CIRCUIT_BREAKERS_TRIPPED,
]


def start_metrics_server(port: int = 9000, addr: str = "0.0.0.0") -> None:
    """Start a standalone Prometheus HTTP metrics server in a daemon thread.

    Uses ``prometheus_client.start_http_server`` which spawns its own
    daemon thread serving ``/metrics``.  Safe to call multiple times
    (subsequent calls are no-ops).

    Args:
        port: TCP port to bind (default 9000, per enterprise-observability-plan).
        addr: Bind address (default 0.0.0.0).
    """
    from prometheus_client import start_http_server as _start_http_server

    _start_http_server(port, addr=addr)


__all__ = [
    "AGENT_LAUNCHES_TOTAL",
    "CYCLE_DURATION_SECONDS",
    "ACTIVE_LOCKS",
    "CREDIT_CONSUMPTION_TOTAL",
    "AGENT_DURATION_SECONDS",
    "CIRCUIT_BREAKERS_TRIPPED",
    "start_metrics_server",
]
