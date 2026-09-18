from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from opentelemetry import context as otel_context, propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import server
from holmes.core.otel_tracing import OTelSpan


@pytest.fixture()
def client():
    return TestClient(server.app)


class _TestServerTracer:
    def __init__(self):
        self._tracer = trace.get_tracer("test-server")

    def start_trace(self, name: str, span_type=None):
        span = self._tracer.start_span(name)
        ctx = trace.set_span_in_context(span)
        token = otel_context.attach(ctx)
        return OTelSpan(span, self._tracer, token)


@pytest.fixture()
def in_memory_trace_exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    monkeypatch.setattr(server, "server_tracer", _TestServerTracer())
    monkeypatch.setattr(server.TracingFactory, "get_metrics", lambda: None)

    yield exporter
    provider.shutdown()


def _mock_llm():
    mock_ai = MagicMock()
    mock_ai.call.return_value = MagicMock(
        result="This is a traced response.",
        tool_calls=[],
        messages=[{"role": "assistant", "content": "This is a traced response."}],
        metadata={},
        num_llm_calls=1,
    )
    return mock_ai


def _chat_payload():
    return {
        "ask": "What can you do?",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "model": "gpt-4.1",
    }


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_uses_inbound_traceparent(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
    in_memory_trace_exporter,
):
    mock_create_toolcalling_llm.return_value = _mock_llm()
    mock_get_global_instructions.return_value = []

    headers = {}
    caller_tracer = trace.get_tracer("test-caller")
    with caller_tracer.start_as_current_span("upstream-request") as upstream_span:
        propagate.inject(headers)
        upstream_context = upstream_span.get_span_context()

    response = client.post("/api/chat", json=_chat_payload(), headers=headers)

    assert response.status_code == 200

    investigation_span = next(
        span
        for span in in_memory_trace_exporter.get_finished_spans()
        if span.name == "holmesgpt.investigation"
    )
    assert investigation_span.context.trace_id == upstream_context.trace_id
    assert investigation_span.parent is not None
    assert investigation_span.parent.is_remote
    assert investigation_span.parent.span_id == upstream_context.span_id


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_detaches_trace_context_after_request(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
    in_memory_trace_exporter,
):
    mock_create_toolcalling_llm.return_value = _mock_llm()
    mock_get_global_instructions.return_value = []

    headers = {}
    caller_tracer = trace.get_tracer("test-caller")
    with caller_tracer.start_as_current_span("upstream-request") as upstream_span:
        propagate.inject(headers)
        upstream_context = upstream_span.get_span_context()

    response = client.post("/api/chat", json=_chat_payload(), headers=headers)
    assert response.status_code == 200

    first_investigation_span = next(
        span
        for span in in_memory_trace_exporter.get_finished_spans()
        if span.name == "holmesgpt.investigation"
    )
    assert first_investigation_span.parent is not None
    assert first_investigation_span.parent.span_id == upstream_context.span_id

    in_memory_trace_exporter.clear()

    response = client.post("/api/chat", json=_chat_payload())
    assert response.status_code == 200

    second_investigation_span = next(
        span
        for span in in_memory_trace_exporter.get_finished_spans()
        if span.name == "holmesgpt.investigation"
    )
    assert second_investigation_span.parent is None
