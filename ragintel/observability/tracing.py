"""OpenTelemetry tracing yardımcıları (İP-2.2).

Langfuse erişimi yoksa veya exporter hata verirse uygulama akışı durmaz.
Bu modül, tracing'i yatay bir katman olarak tutar; pipeline ona bağımlı değildir.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode

from ..config.settings import LangfuseSettings
from .logging import bind_context, get_logger


class _SafeExporter:
    """Exporter istisnalarını yutar; log'lar ve FAILURE döndürür."""

    def __init__(self, inner, *, logger=None):
        self.inner = inner
        self.log = logger or get_logger("observability.tracing")

    def export(self, spans) -> SpanExportResult:
        try:
            return self.inner.export(spans)
        except Exception as exc:
            self.log.warning("tracing_export_failed", error=str(exc), span_count=len(spans))
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.inner.shutdown()
        except Exception as exc:
            self.log.warning("tracing_shutdown_failed", error=str(exc))

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        inner = getattr(self.inner, "force_flush", None)
        if inner is None:
            return True
        try:
            return bool(inner(timeout_millis=timeout_millis))
        except Exception as exc:
            self.log.warning("tracing_force_flush_failed", error=str(exc))
            return False


@dataclass
class _TracingState:
    provider: TracerProvider | None = None
    processor: Any | None = None
    configured: bool = False
    enabled: bool = False


_LOCK = threading.Lock()
_STATE = _TracingState()


def _normalize_host(host: str) -> str:
    return host.rstrip("/")


def _otlp_endpoint(host: str) -> str:
    return f"{_normalize_host(host)}/api/public/otel/v1/traces"


def _otlp_headers(public_key: str, secret_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def configure_tracing(*, exporter=None, span_processor=None, force: bool = False) -> None:
    """Tracing'i kurar.

    Varsayılan yol: `RAGINTEL_LANGFUSE_*` ENV'lerinden OTLP HTTP exporter.
    Testler `exporter`/`span_processor` enjekte ederek deterministik davranır.
    """
    with _LOCK:
        if _STATE.configured and not force:
            return

        provider = None
        processor = None
        enabled = False

        if exporter is not None:
            provider = TracerProvider(resource=Resource.create({"service.name": "ragintel-test"}))
            processor = span_processor or SimpleSpanProcessor(_SafeExporter(exporter))
            provider.add_span_processor(processor)
            enabled = True
        else:
            settings = LangfuseSettings()
            if settings.enabled:
                provider = TracerProvider(
                    resource=Resource.create(
                        {
                            "service.name": settings.service_name,
                            "deployment.environment": "dev",
                        }
                    )
                )
                wrapped = _SafeExporter(
                    OTLPSpanExporter(
                        endpoint=_otlp_endpoint(settings.host),
                        headers=_otlp_headers(settings.public_key, settings.secret_key),
                        timeout=settings.export_timeout_ms / 1000.0,
                    )
                )
                processor = BatchSpanProcessor(
                    wrapped,
                    schedule_delay_millis=settings.schedule_delay_ms,
                    max_queue_size=settings.max_queue_size,
                    max_export_batch_size=settings.max_export_batch_size,
                    export_timeout_millis=settings.export_timeout_ms,
                )
                provider.add_span_processor(processor)
                enabled = True

        _STATE.provider = provider
        _STATE.processor = processor
        _STATE.enabled = enabled
        _STATE.configured = True


def ensure_tracing() -> None:
    if not _STATE.configured:
        configure_tracing()


def force_flush_tracing(timeout_millis: int = 30000) -> bool:
    processor = _STATE.processor
    if processor is None:
        return True
    try:
        return bool(processor.force_flush(timeout_millis))
    except Exception as exc:
        get_logger("observability.tracing").warning(
            "tracing_force_flush_failed", error=str(exc)
        )
        return False


def _tracer(name: str):
    ensure_tracing()
    provider = _STATE.provider
    if provider is not None:
        return provider.get_tracer(name)
    return trace.get_tracer(name)


def _bind_ids(span) -> None:
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return
    bind_context(trace_id=f"{ctx.trace_id:032x}", span_id=f"{ctx.span_id:016x}")


def set_span_attributes(**attrs: Any) -> None:
    span = trace.get_current_span()
    if span is None:
        return
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)


def add_event(name: str, **attrs: Any) -> None:
    span = trace.get_current_span()
    if span is None:
        return
    payload = {k: v for k, v in attrs.items() if v is not None}
    span.add_event(name, payload)


@contextmanager
def start_span(name: str, **attrs: Any):
    tracer = _tracer("ragintel")
    with tracer.start_as_current_span(name) as span:
        set_span_attributes(**attrs)
        _bind_ids(span)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


def _reset_tracing_for_tests() -> None:
    with _LOCK:
        processor = _STATE.processor
        if processor is not None:
            try:
                processor.shutdown()
            except Exception:
                pass
        _STATE.provider = None
        _STATE.processor = None
        _STATE.configured = False
        _STATE.enabled = False
