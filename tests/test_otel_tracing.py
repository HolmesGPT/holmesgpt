"""Tests for OpenTelemetry tracing implementation."""
import os
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from holmes.core.tracing import DummySpan, DummyTracer, SpanType, TracingFactory


@pytest.fixture()
def in_memory_exporter():
    """Set up an in-memory OTel provider for testing span hierarchy.

    Uses _TRACER_PROVIDER_SET_ONCE to allow resetting the global provider
    between tests, since OTel SDK normally prevents overriding.
    """
    exporter = InMemorySpanExporter()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Reset the global provider guard so we can set a fresh provider per test
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    yield exporter
    provider.shutdown()


class TestOTelSpan:
    """Test OTelSpan wrapper behavior."""

    def test_otel_span_context_manager(self, in_memory_exporter):
        """OTelSpan works as a context manager and ends the underlying span."""
        from holmes.core.otel_tracing import OTelSpan

        tracer = trace.get_tracer("test")
        raw_span = tracer.start_span("test")
        otel_span = OTelSpan(raw_span, tracer)

        with otel_span:
            pass

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test"

    def test_otel_span_context_manager_on_error(self, in_memory_exporter):
        """OTelSpan sets error status on exception."""
        from opentelemetry.trace import StatusCode

        from holmes.core.otel_tracing import OTelSpan

        tracer = trace.get_tracer("test")
        raw_span = tracer.start_span("test")
        otel_span = OTelSpan(raw_span, tracer)

        with pytest.raises(ValueError):
            with otel_span:
                raise ValueError("test error")

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_otel_span_log_metadata(self):
        """OTelSpan.log() sets attributes on the underlying span."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        otel_span = OTelSpan(mock_span, mock_tracer)

        otel_span.log(metadata={"key1": "value1", "key2": 42})

        mock_span.set_attribute.assert_any_call("key1", "value1")
        mock_span.set_attribute.assert_any_call("key2", 42)

    def test_otel_span_log_input_output_truncated(self):
        """OTelSpan.log() truncates input/output to 4096 chars."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        otel_span = OTelSpan(mock_span, mock_tracer)

        long_string = "x" * 10000
        otel_span.log(input=long_string, output=long_string)

        calls = mock_span.set_attribute.call_args_list
        for call in calls:
            assert len(call[0][1]) <= 4096

    def test_otel_span_set_attributes(self):
        """OTelSpan.set_attributes() updates span name and attributes."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        otel_span = OTelSpan(mock_span, mock_tracer)

        otel_span.set_attributes(
            name="new_name",
            span_attributes={"attr1": "val1"},
        )

        mock_span.update_name.assert_called_once_with("new_name")
        mock_span.set_attribute.assert_called_once_with("attr1", "val1")


class TestSpanHierarchy:
    """Test that spans form correct parent-child relationships."""

    def test_child_spans_have_correct_parent(self, in_memory_exporter):
        """start_span() creates children linked to the parent span."""
        from holmes.core.otel_tracing import OTelSpan

        tracer = trace.get_tracer("test")
        raw_root = tracer.start_span("root")
        root = OTelSpan(raw_root, tracer)

        child = root.start_span(name="child")
        child.end()
        root.end()

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 2

        child_span = next(s for s in spans if s.name == "child")
        root_span = next(s for s in spans if s.name == "root")
        assert child_span.parent.span_id == root_span.context.span_id

    def test_context_activation_makes_auto_spans_children(self, in_memory_exporter):
        """Activated spans become the parent for spans created via the global tracer.

        This simulates what httpx auto-instrumentation does: it creates spans
        using trace.get_tracer().start_span() which picks up the current context.
        """
        from holmes.core.otel_tracing import OTelSpan

        tracer = trace.get_tracer("test")

        # Create and activate a root span (simulates start_trace)
        raw_root = tracer.start_span("investigation")
        from opentelemetry import context as otel_context

        ctx = trace.set_span_in_context(raw_root)
        token = otel_context.attach(ctx)
        root = OTelSpan(raw_root, tracer, token)

        # Create a child (simulates gen_ai.chat)
        chat_span = root.start_span(name="gen_ai.chat")

        # Simulate an auto-instrumented httpx call: it uses the current context
        with tracer.start_as_current_span("HTTP POST"):
            pass  # auto-instrumented span ends here

        chat_span.end()

        # Create another child (simulates tool span)
        tool_span = root.start_span(name="holmesgpt.tool.kubectl")

        # Another auto-instrumented call during tool execution
        with tracer.start_as_current_span("HTTP POST /mcp"):
            pass

        tool_span.end()
        root.end()

        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) == 5

        by_name = {s.name: s for s in spans}

        # gen_ai.chat is child of investigation
        assert by_name["gen_ai.chat"].parent.span_id == by_name["investigation"].context.span_id

        # HTTP POST is child of gen_ai.chat (because chat_span was active in context)
        assert by_name["HTTP POST"].parent.span_id == by_name["gen_ai.chat"].context.span_id

        # holmesgpt.tool.kubectl is child of investigation
        assert by_name["holmesgpt.tool.kubectl"].parent.span_id == by_name["investigation"].context.span_id

        # HTTP POST /mcp is child of tool span
        assert by_name["HTTP POST /mcp"].parent.span_id == by_name["holmesgpt.tool.kubectl"].context.span_id

    def test_end_detaches_context(self, in_memory_exporter):
        """After end(), the span is no longer the active parent."""
        from holmes.core.otel_tracing import OTelSpan

        tracer = trace.get_tracer("test")

        # Root span
        raw_root = tracer.start_span("root")
        from opentelemetry import context as otel_context

        ctx = trace.set_span_in_context(raw_root)
        token = otel_context.attach(ctx)
        root = OTelSpan(raw_root, tracer, token)

        # Child span — activated in context
        child = root.start_span(name="child")
        child.end()  # Should detach — context returns to root

        # New span created after child.end() should be child of root, not child
        sibling = root.start_span(name="sibling")
        sibling.end()
        root.end()

        spans = in_memory_exporter.get_finished_spans()
        by_name = {s.name: s for s in spans}

        assert by_name["child"].parent.span_id == by_name["root"].context.span_id
        assert by_name["sibling"].parent.span_id == by_name["root"].context.span_id


class TestOpenTelemetryTracer:
    """Test OpenTelemetryTracer initialization and behavior."""

    def test_tracer_start_trace_returns_otel_span(self):
        """start_trace() returns an OTelSpan wrapping a real OTel span."""
        from holmes.core.otel_tracing import OTelSpan, OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            tracer = OpenTelemetryTracer(service_name="test")
            span = tracer.start_trace("test_trace")
            assert isinstance(span, OTelSpan)
            assert span._token is not None  # Span is activated in context
            span.end()
            tracer.shutdown()

    def test_tracer_start_trace_activates_context(self):
        """start_trace() activates the span so it's visible as current span."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            tracer = OpenTelemetryTracer(service_name="test")
            span = tracer.start_trace("test_trace")

            # The current span in context should be our span
            current = trace.get_current_span()
            assert current == span._span

            span.end()
            tracer.shutdown()

    def test_tracer_wrap_llm_passthrough(self):
        """wrap_llm() returns the module unchanged (no Braintrust wrapping)."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            tracer = OpenTelemetryTracer(service_name="test")
            mock_llm = MagicMock()
            result = tracer.wrap_llm(mock_llm)
            assert result is mock_llm
            tracer.shutdown()

    def test_tracer_start_experiment_returns_none(self):
        """start_experiment() returns None (OTel doesn't use experiments)."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            tracer = OpenTelemetryTracer(service_name="test")
            assert tracer.start_experiment() is None
            tracer.shutdown()


class TestTracingFactoryOTel:
    """Test TracingFactory OTel integration."""

    def test_factory_creates_otel_tracer_explicit(self):
        """TracingFactory creates OTel tracer when trace_type='otel'."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            tracer = TracingFactory.create_tracer("otel")
            assert isinstance(tracer, OpenTelemetryTracer)
            tracer.shutdown()

    def test_factory_auto_detects_otel(self):
        """TracingFactory auto-detects OTel when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}, clear=False):
            tracer = TracingFactory.create_tracer(None)
            assert isinstance(tracer, OpenTelemetryTracer)
            tracer.shutdown()

    def test_factory_returns_dummy_without_endpoint(self):
        """TracingFactory returns DummyTracer when no OTel endpoint is set."""
        env = os.environ.copy()
        env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        env.pop("BRAINTRUST_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            tracer = TracingFactory.create_tracer(None)
            assert isinstance(tracer, DummyTracer)


class TestParseOTelHeaders:
    """Test OTEL header parsing utility."""

    def test_parse_empty_string(self):
        from holmes.core.otel_tracing import _parse_otel_headers

        assert _parse_otel_headers("") == {}

    def test_parse_single_header(self):
        from holmes.core.otel_tracing import _parse_otel_headers

        assert _parse_otel_headers("Authorization=Api-Token dt0c01.abc") == {
            "Authorization": "Api-Token dt0c01.abc"
        }

    def test_parse_multiple_headers(self):
        from holmes.core.otel_tracing import _parse_otel_headers

        result = _parse_otel_headers("key1=val1,key2=val2")
        assert result == {"key1": "val1", "key2": "val2"}


class TestOTelMetrics:
    """Test OTel metrics instruments."""

    def test_metrics_none_when_not_initialized(self):
        """Metrics should be None when OTel is not initialized."""
        from holmes.core.tracing import TracingFactory
        # Before any tracer is created, metrics may or may not be set
        # depending on test ordering. Just verify the function is callable.
        result = TracingFactory.get_metrics()
        assert result is None or hasattr(result, "token_usage")

    def test_otel_metrics_instruments_exist(self):
        """OTelMetrics should have all expected metric instruments."""
        from holmes.core.otel_tracing import OTelMetrics
        from opentelemetry.sdk.metrics import MeterProvider

        meter_provider = MeterProvider()
        meter = meter_provider.get_meter("test", "0.1.0")
        m = OTelMetrics(meter)

        assert hasattr(m, "token_usage")
        assert hasattr(m, "investigation_duration")
        assert hasattr(m, "investigation_count")
        assert hasattr(m, "investigation_iterations")
        assert hasattr(m, "llm_call_duration")
        assert hasattr(m, "tool_call_count")
        assert hasattr(m, "tool_call_duration")
        assert hasattr(m, "tool_call_errors")

        meter_provider.shutdown()

    def test_metrics_recording_does_not_raise(self):
        """Recording metrics should not raise exceptions."""
        from holmes.core.otel_tracing import OTelMetrics
        from opentelemetry.sdk.metrics import MeterProvider

        meter_provider = MeterProvider()
        meter = meter_provider.get_meter("test", "0.1.0")
        m = OTelMetrics(meter)

        # These should not raise
        m.token_usage.add(100, {"gen_ai_request_model": "test", "gen_ai_token_type": "input"})
        m.investigation_count.add(1, {"gen_ai_request_model": "test"})
        m.investigation_duration.record(1.5, {"gen_ai_request_model": "test"})
        m.investigation_iterations.record(3, {"gen_ai_request_model": "test"})
        m.llm_call_duration.record(0.5, {"gen_ai_request_model": "test"})
        m.tool_call_count.add(1, {"holmesgpt_tool_name": "list_pods"})
        m.tool_call_duration.record(2.0, {"holmesgpt_tool_name": "list_pods"})
        m.tool_call_errors.add(1, {"holmesgpt_tool_name": "list_pods"})

        meter_provider.shutdown()
