"""
prismatic/telemetry/tracer.py — OpenTelemetry Distributed Tracing

Provides tracer initialization, W3C TraceContext propagation,
and ergonomic context managers for instrumenting the Prismatic
dispatcher and agent subprocesses.

Configuration is entirely via environment variables:

    OTEL_EXPORTER_OTLP_ENDPOINT   OTLP collector endpoint
                                  (default: http://localhost:4317)
    OTEL_SERVICE_NAME             Service name for traces
                                  (default: prismatic-engine)
    OTEL_TRACES_ENABLED           Set to "0" / "false" to disable
                                  (default: enabled)

When tracing is disabled, all functions become no-ops so the
dispatcher can run with zero overhead.
"""

from __future__ import annotations

import os
from typing import Any

# ── Lazy imports to avoid import-time crashes when packages
#    are not installed — the dispatcher must start even if
#    OpenTelemetry is unavailable. ────────────────────────────

_TRACING_AVAILABLE = False
_ENABLED = True


def _check_availability() -> bool:
    """Check if OpenTelemetry packages are importable."""
    global _TRACING_AVAILABLE
    if _TRACING_AVAILABLE:
        return True
    try:
        import opentelemetry  # noqa: F401
        import opentelemetry.sdk.trace  # noqa: F401
        _TRACING_AVAILABLE = True
        return True
    except ImportError:
        return False


def _is_enabled() -> bool:
    """Check whether tracing is enabled via env var."""
    val = os.environ.get("OTEL_TRACES_ENABLED", "1")
    return val.lower() not in ("0", "false", "no", "off")


# ── Main API ─────────────────────────────────────────────────


def init_tracer(
    service_name: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Initialise the OpenTelemetry tracer provider.

    Sets the global ``TracerProvider`` with an OTLP gRPC span
    exporter.  Call once at dispatcher startup.  Subsequent calls
    are no-ops.

    Args:
        service_name: Service name override.  Defaults to
            ``OTEL_SERVICE_NAME`` or ``"prismatic-engine"``.
        endpoint: OTLP collector endpoint.  Defaults to
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` or
            ``"http://localhost:4317"``.

    Returns:
        A ``opentelemetry.trace.Tracer`` instance, or ``None``
        if OpenTelemetry is not available or tracing is disabled.
    """
    global _ENABLED
    _ENABLED = _is_enabled()

    if not _ENABLED:
        return None
    if not _check_availability():
        return None

    import opentelemetry.trace as trace_api  # noqa: F811
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    # Allow re-init (e.g., for service_name changes)
    name = service_name or os.environ.get(
        "OTEL_SERVICE_NAME", "prismatic-engine"
    )
    target = endpoint or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
    )

    provider = TracerProvider()

    try:
        exporter = OTLPSpanExporter(endpoint=target, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:
        # If the exporter can't connect (e.g., no collector running),
        # install a no-op processor so spans don't queue indefinitely.
        pass

    trace_api.set_tracer_provider(provider)
    return trace_api.get_tracer(name)


def inject_trace_context(carrier: dict[str, str]) -> None:
    """Inject W3C TraceContext headers into a carrier dict.

    The carrier dict is mutated in-place.  Use this to propagate
    trace context into subprocess environments or HTTP headers.

    Args:
        carrier: A mutable dictionary (e.g., ``os.environ`` copy).
    """
    if not _ENABLED or not _check_availability():
        return
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
    TraceContextTextMapPropagator().inject(carrier)


def extract_trace_context(
    carrier: dict[str, str],
) -> Any | None:
    """Extract W3C TraceContext from a carrier dict.

    Args:
        carrier: A dictionary containing traceparent/tracestate
            keys (e.g., from environment variables or HTTP headers).

    Returns:
        An OpenTelemetry ``Context`` object, or ``None`` if no
        trace context was found or tracing is disabled.
    """
    if not _ENABLED or not _check_availability():
        return None
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
    return TraceContextTextMapPropagator().extract(carrier)


def get_current_span_context() -> dict[str, str] | None:
    """Get the current span's W3C trace context as a dict.

    Returns a dict with ``traceparent`` and optionally ``tracestate``
    keys, suitable for injection into subprocess environments or
    HTTP headers.  Returns ``None`` if there is no active span or
    tracing is disabled.
    """
    if not _ENABLED or not _check_availability():
        return None
    from opentelemetry import trace as trace_api
    span = trace_api.get_current_span()
    if span is None or not span.get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    inject_trace_context(carrier)
    return carrier


def get_span(name: str) -> Any | None:
    """Return a span context manager for the given name.

    Usage::

        with get_span("my-operation"):
            do_work()

    Args:
        name: Span name.

    Returns:
        A context manager that starts/ends a span, or ``None`` if
        tracing is disabled.
    """
    if not _ENABLED or not _check_availability():
        from contextlib import nullcontext
        return nullcontext()
    from opentelemetry import trace as trace_api
    tracer = trace_api.get_tracer("prismatic-engine")
    return tracer.start_as_current_span(name)


def set_span_attribute(key: str, value: Any) -> None:
    """Set an attribute on the currently active span.

    No-op if there is no active span or tracing is disabled.
    """
    if not _ENABLED or not _check_availability():
        return
    from opentelemetry import trace as trace_api
    span = trace_api.get_current_span()
    if span is not None:
        span.set_attribute(key, value)


def add_span_event(name: str, attributes: dict[str, Any] | None = None) -> None:
    """Add a timestamped event to the currently active span.

    Args:
        name: Event name.
        attributes: Optional dict of event attributes.
    """
    if not _ENABLED or not _check_availability():
        return
    from opentelemetry import trace as trace_api
    span = trace_api.get_current_span()
    if span is not None:
        span.add_event(name, attributes=attributes)


def record_span_exception(exception: Exception) -> None:
    """Record an exception on the currently active span."""
    if not _ENABLED or not _check_availability():
        return
    from opentelemetry import trace as trace_api
    span = trace_api.get_current_span()
    if span is not None:
        span.record_exception(exception)


def shutdown_tracer() -> None:
    """Gracefully shut down the tracer provider, flushing all spans."""
    if not _ENABLED or not _check_availability():
        return
    from opentelemetry import trace as trace_api
    provider = trace_api.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
