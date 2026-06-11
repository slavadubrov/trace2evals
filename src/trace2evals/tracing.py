"""OpenTelemetry setup: GenAI-semconv spans to a local JSONL file, optionally OTLP.

The JSONL exporter exists so the mining pipeline works offline and the demo has
no hard dependency on a running Langfuse/Phoenix instance. Set
OTEL_EXPORTER_OTLP_ENDPOINT to also ship the same spans to a real backend.

The OTel global tracer provider can only be installed once per process, so the
exporter keeps a mutable target path: repeated init_tracing() calls (CLI
commands, the unit tests, and the regression suite in one pytest session) just
redirect where spans land.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

DEFAULT_SPANS_PATH = Path("data/traces/spans.jsonl")


class JsonlSpanExporter(SpanExporter):
    """Append every finished span as one JSON line — the simplest portable trace store."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for span in spans:
                ctx = span.get_span_context()
                parent = span.parent
                fh.write(
                    json.dumps(
                        {
                            "trace_id": format(ctx.trace_id, "032x"),
                            "span_id": format(ctx.span_id, "016x"),
                            "parent_span_id": format(parent.span_id, "016x") if parent else None,
                            "name": span.name,
                            "start_ns": span.start_time,
                            "end_ns": span.end_time,
                            "status": span.status.status_code.name,
                            "attributes": dict(span.attributes or {}),
                        }
                    )
                    + "\n"
                )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover
        pass


_exporter: JsonlSpanExporter | None = None


def init_tracing(
    service_name: str = "trace2evals-agent", spans_path: Path | str | None = None
) -> trace.Tracer:
    global _exporter
    path = Path(spans_path or os.environ.get("TRACE2EVALS_SPANS_PATH", DEFAULT_SPANS_PATH))
    if _exporter is None:
        _exporter = JsonlSpanExporter(path)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(SimpleSpanProcessor(_exporter))
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
    else:
        _exporter.path = path
    return trace.get_tracer("trace2evals")
