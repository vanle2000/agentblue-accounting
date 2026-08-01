"""OpenTelemetry-compatible tracing hooks.

Optional tracing that degrades safely when disabled or when the
tracing backend is unavailable.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Module-level tracing state
_tracing_enabled: bool = False
_tracer: Any = None


def configure_tracing(
    *,
    enabled: bool = False,
    service_name: str = "agentblue-accounting",
    endpoint: str = "",
) -> None:
    """Configure tracing. Safe to call even if OpenTelemetry is not installed.

    Args:
        enabled: Whether to enable tracing.
        service_name: Service name for traces.
        endpoint: OTLP endpoint URL.
    """
    global _tracing_enabled, _tracer

    if not enabled:
        _tracing_enabled = False
        _tracer = None
        logger.info("tracing_disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(service_name=service_name)

        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception as exc:
                logger.warning(
                    "tracing_exporter_failed",
                    error=str(exc)[:200],
                    fallback="no-export",
                )

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        _tracing_enabled = True
        logger.info("tracing_enabled", service=service_name, endpoint=endpoint)

    except ImportError:
        _tracing_enabled = False
        _tracer = None
        logger.info("tracing_unavailable", reason="opentelemetry-not-installed")
    except Exception as exc:
        _tracing_enabled = False
        _tracer = None
        logger.warning("tracing_setup_failed", error=str(exc)[:200])


@contextmanager
def trace_operation(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[Any, None, None]:
    """Context manager for tracing an operation.

    Yields a span-like object. If tracing is disabled, yields a no-op object.

    Args:
        name: Operation name.
        attributes: Span attributes (must not contain secrets).
    """
    if not _tracing_enabled or _tracer is None:
        yield _NoOpSpan()
        return

    try:
        with _tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    if isinstance(value, str | int | float | bool):
                        span.set_attribute(key, value)
            yield _SpanWrapper(span)
    except Exception as exc:
        logger.warning("tracing_span_error", operation=name, error=str(exc)[:100])
        yield _NoOpSpan()


def get_trace_id() -> str:
    """Get the current trace ID, or empty string if tracing is disabled."""
    if not _tracing_enabled:
        return ""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""


def is_tracing_enabled() -> bool:
    """Check if tracing is currently enabled."""
    return _tracing_enabled


class _NoOpSpan:
    """No-op span for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass


class _SpanWrapper:
    """Wrapper around an OpenTelemetry span."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        with suppress(Exception):
            self._span.set_attribute(key, value)

    def set_status(self, status: Any) -> None:
        with suppress(Exception):
            self._span.set_status(status)

    def record_exception(self, exc: Exception) -> None:
        with suppress(Exception):
            self._span.record_exception(exc)
