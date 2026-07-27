"""Regression tests for inbound W3C trace-context extraction on FastAPI (#2268)."""

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_inbound_traceparent_parents_investigation_span():
    """A request with traceparent must share the caller's trace ID.

    FastAPIInstrumentor extracts W3C headers so spans started during the
    request (including holmesgpt.investigation) become children of that
    inbound context instead of new root traces.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Allow resetting the global provider between tests (same pattern as
    # tests/test_otel_tracing.py).
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    upstream = trace.get_tracer("upstream")
    with upstream.start_as_current_span("upstream-call") as parent:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        parent_trace_id = parent.get_span_context().trace_id
        parent_span_id = parent.get_span_context().span_id

    app = FastAPI()

    @app.post("/api/chat")
    def chat():
        # Mirror OpenTelemetryTracer.start_trace(): start a span under the
        # *current* OTel context (set by FastAPIInstrumentor from the request).
        span = trace.get_tracer("holmesgpt").start_span("holmesgpt.investigation")
        token = otel_context.attach(trace.set_span_in_context(span))
        try:
            return {"ok": True}
        finally:
            otel_context.detach(token)
            span.end()

    FastAPIInstrumentor.instrument_app(app)
    try:
        client = TestClient(app)
        response = client.post("/api/chat", headers=carrier)
        assert response.status_code == 200

        finished = exporter.get_finished_spans()
        investigation = [s for s in finished if s.name == "holmesgpt.investigation"]
        assert len(investigation) == 1
        inv = investigation[0]
        assert inv.context.trace_id == parent_trace_id
        assert inv.parent is not None

        # HTTP instrumentation span should sit between remote parent and investigation.
        http_spans = [
            s
            for s in finished
            if s is not inv and s.context.trace_id == parent_trace_id
        ]
        assert http_spans, "expected a FastAPI/ASGI span in the same trace"
        assert any(
            s.parent is not None and s.parent.span_id == parent_span_id for s in http_spans
        )
        assert any(s.context.span_id == inv.parent.span_id for s in http_spans)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()
