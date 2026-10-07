"""Opik observability — presence-based OpenTelemetry tracing integration.

Configures logfire + OTLP exporter to stream execution traces, tool calls,
and token costs to Opik (cloud or self-hosted).
When OPIK_API_KEY is unset, tracing is a silent no-op with zero performance overhead.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
import logging
from typing import Any

import logfire
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from proto_harness.config.settings import settings
from proto_harness.observability.cost import compute_batch_cost

logger = logging.getLogger(__name__)

# Official Comet / Opik cloud OTLP ingest endpoint
_CLOUD_OTLP_BASE = "https://www.comet.com/opik/api/v1/private/otel"

OPIK_COST_ATTRIBUTE = "gen_ai.usage.cost"
REQUEST_MODEL_ATTRIBUTE = "gen_ai.request.model"
INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.output_tokens"

_active = False


class CostAnnotatingExporter(SpanExporter):
    """Wraps an exporter to stamp gen_ai.usage.cost onto model spans before export."""

    def __init__(self, wrapped: SpanExporter) -> None:
        self._wrapped = wrapped

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._wrapped.export([_with_cost(span) for span in spans])

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def _with_cost(span: ReadableSpan) -> ReadableSpan:
    """Add gen_ai.usage.cost attribute to leaf LLM model spans."""
    attributes = span.attributes or {}
    if OPIK_COST_ATTRIBUTE in attributes:
        return span

    # Only stamp cost on leaf model spans that carry a request model
    model = attributes.get(REQUEST_MODEL_ATTRIBUTE)
    if not model or not isinstance(model, str):
        return span

    inp = attributes.get(INPUT_TOKENS_ATTRIBUTE, 0)
    out = attributes.get(OUTPUT_TOKENS_ATTRIBUTE, 0)
    if not isinstance(inp, int) or not isinstance(out, int) or (inp == 0 and out == 0):
        return span

    usage = compute_batch_cost(model_name=model, input_tokens=inp, output_tokens=out)

    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent,
        resource=span.resource,
        attributes={**attributes, OPIK_COST_ATTRIBUTE: usage.cost_usd},
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def init_tracing() -> bool:
    """Initialize Opik OTLP tracing if OPIK_API_KEY is configured in settings.

    Returns True if tracing is active, False otherwise.
    """
    global _active
    if _active:
        return True

    key = settings.opik_api_key.get_secret_value() if settings.opik_api_key else ""
    if not key:
        logger.debug("Opik tracing disabled: OPIK_API_KEY is not set.")
        return False

    base = (settings.opik_url_override or _CLOUD_OTLP_BASE).rstrip("/")
    headers = {
        "Authorization": key,
        "projectName": settings.opik_project_name,
        "Comet-Workspace": settings.opik_workspace or "default",
    }

    exporter = OTLPSpanExporter(
        endpoint=f"{base}/v1/traces",
        headers=headers,
    )

    # Configure logfire without cloud egress (send_to_logfire=False)
    # and without console spam (console=False so TUI stdout is not flooded)
    logfire.configure(
        send_to_logfire=False,
        console=False,
        additional_span_processors=[BatchSpanProcessor(CostAnnotatingExporter(exporter))],
    )
    logfire.instrument_pydantic_ai()
    _active = True

    logger.info("Opik tracing active — project='%s' endpoint='%s'", settings.opik_project_name, base)
    return True


def is_tracing_active() -> bool:
    """Return whether tracing is currently active."""
    return _active


def root_span(
    name: str,
    *,
    thread_id: str | None = None,
    input: str | None = None,
) -> AbstractContextManager[Any]:
    """Open a root span grouping a conversation turn or task."""
    if not _active:
        return nullcontext()
    attrs: dict[str, Any] = {}
    if thread_id:
        attrs["thread_id"] = thread_id
    if input:
        attrs["input"] = input
    return logfire.span(name, **attrs)


def record_output(span: Any, output: object) -> None:
    """Attach final answer output to the root span."""
    if span is None or not _active or not isinstance(output, str) or not output:
        return
    try:
        span.set_attribute("output", output)
    except Exception as exc:
        logger.debug("Failed to set span output attribute: %s", exc)


def flush_tracing(timeout_s: float = 5.0) -> None:
    """Force flush all buffered spans to Opik."""
    if _active:
        try:
            logfire.force_flush(timeout_millis=int(timeout_s * 1000))
        except Exception as exc:
            logger.debug("Failed to flush logfire spans: %s", exc)
