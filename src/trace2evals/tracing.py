"""Local OTel JSONL export. Schema v2 is specific to this demo adapter.

Providers are owned locally: never replace a host application's global provider.
Synthetic content is recorded in full; real traces require caller-side redaction.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

DEFAULT_SPANS_PATH = Path("data/traces/spans.jsonl")


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                for span in spans:
                    ctx = span.get_span_context()
                    stream.write(
                        json.dumps(
                            {
                                "trace_id": format(ctx.trace_id, "032x"),
                                "span_id": format(ctx.span_id, "016x"),
                                "parent_span_id": format(span.parent.span_id, "016x")
                                if span.parent
                                else None,
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

    def shutdown(self):
        pass


def init_tracing(service_name: str = "trace2evals-agent", spans_path: Path | str | None = None):
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        SimpleSpanProcessor(JsonlSpanExporter(Path(spans_path or DEFAULT_SPANS_PATH)))
    )
    return provider.get_tracer("trace2evals", "0.2.0")
