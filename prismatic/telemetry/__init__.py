"""
prismatic/telemetry — Observability subpackage.

Combines:
- Non-blocking SQLite telemetry collector (``_collector.py`` — migrated from
  the original ``prismatic/telemetry.py`` single-file module).
- OpenTelemetry distributed tracing (``tracer.py``).

All exports from the original module are re-exported here so existing
imports (``from prismatic.telemetry import get_collector``) continue to work.
"""

# ── Re-export everything from the original collector module ─────
from ._collector import (  # noqa: F401, E402
    TelemetryCollector,
    get_collector,
    BREAKER_MICRO_MAX,
    BREAKER_MACRO_MAX,
    DEFAULT_DB_PATH,
)

# ── Re-export the OTel tracer API ──────────────────────────────
from .tracer import (  # noqa: F401, E402
    init_tracer,
    inject_trace_context,
    extract_trace_context,
    get_current_span_context,
    get_span,
    set_span_attribute,
    add_span_event,
    record_span_exception,
    shutdown_tracer,
)

# ── Re-export Prometheus metrics (Phase 4.2 / GRO-1581) ────────
from .metrics import (  # noqa: F401, E402
    AGENT_LAUNCHES_TOTAL,
    CYCLE_DURATION_SECONDS,
    ACTIVE_LOCKS,
    CREDIT_CONSUMPTION_TOTAL,
    AGENT_DURATION_SECONDS,
    CIRCUIT_BREAKERS_TRIPPED,
    start_metrics_server,
)
