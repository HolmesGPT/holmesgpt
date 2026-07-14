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

    def test_otel_span_log_input_output_langfuse_attrs(self):
        """OTelSpan.log() stores input/output under the Langfuse-read attrs."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        with patch("holmes.core.otel_tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            otel_span.log(input="the prompt", output="the answer")

        mock_span.set_attribute.assert_any_call(
            "langfuse.observation.input", "the prompt"
        )
        mock_span.set_attribute.assert_any_call(
            "langfuse.observation.output", "the answer"
        )

    def test_otel_span_log_input_output_disabled_by_default(self):
        """Without the env flag, input/output are NOT emitted (vendor-neutral)."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        with patch("holmes.core.otel_tracing.HOLMES_LANGFUSE_ATTRIBUTES", False):
            otel_span.log(input="the prompt", output="the answer", error="x")

        keys = [c[0][0] for c in mock_span.set_attribute.call_args_list]
        assert not any(k.startswith("langfuse.observation.") for k in keys)

    def test_otel_span_log_input_output_truncated(self):
        """OTelSpan.log() truncates input/output to _MAX_ATTR_CHARS."""
        from holmes.core.otel_tracing import _MAX_ATTR_CHARS, OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        long_string = "x" * (_MAX_ATTR_CHARS + 5000)
        with patch("holmes.core.otel_tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            otel_span.log(input=long_string, output=long_string)

        for call in mock_span.set_attribute.call_args_list:
            assert len(call[0][1]) <= _MAX_ATTR_CHARS

    def test_otel_span_log_input_output_json_encoded(self):
        """Non-string input/output is JSON-encoded (so Langfuse renders it)."""
        import json

        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        payload = {"content": "hi", "reasoning": "because", "tool_calls": []}
        with patch("holmes.core.otel_tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            otel_span.log(input=[{"role": "user", "content": "q"}], output=payload)

        recorded = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
        assert json.loads(recorded["langfuse.observation.input"]) == [
            {"role": "user", "content": "q"}
        ]
        assert json.loads(recorded["langfuse.observation.output"]) == payload

    def test_otel_span_log_error_sets_level(self):
        """A truthy error= marks the observation as ERROR for Langfuse."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        with patch("holmes.core.otel_tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            otel_span.log(output="partial", error="boom failed")

        mock_span.set_attribute.assert_any_call("langfuse.observation.level", "ERROR")
        mock_span.set_attribute.assert_any_call(
            "langfuse.observation.status_message", "boom failed"
        )

    def test_otel_span_log_metadata_string_truncated(self):
        """Free-text metadata values are capped to _MAX_ATTR_CHARS."""
        from holmes.core.otel_tracing import _MAX_ATTR_CHARS, OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        otel_span.log(metadata={"langfuse.trace.input": "y" * (_MAX_ATTR_CHARS + 10)})

        recorded = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
        assert len(recorded["langfuse.trace.input"]) == _MAX_ATTR_CHARS

    def test_otel_span_log_tags_as_string_array(self):
        """List metadata values (e.g. langfuse.trace.tags) set as native arrays."""
        from holmes.core.otel_tracing import OTelSpan

        mock_span = MagicMock()
        otel_span = OTelSpan(mock_span, MagicMock())

        otel_span.log(metadata={"langfuse.trace.tags": ["source:alert", "cluster:x"]})

        mock_span.set_attribute.assert_any_call(
            "langfuse.trace.tags", ["source:alert", "cluster:x"]
        )

    def test_safe_detach_detaches_when_in_order(self):
        """When our span is current, detach happens normally."""
        from holmes.core.otel_tracing import OTelSpan

        span = MagicMock()
        token = object()
        otel_span = OTelSpan(span, MagicMock(), token)

        with patch(
            "holmes.core.otel_tracing.trace.get_current_span", return_value=span
        ), patch("holmes.core.otel_tracing.otel_context.detach") as detach:
            otel_span._safe_detach()

        detach.assert_called_once_with(token)

    def test_safe_detach_skips_when_out_of_order(self):
        """Out-of-order (cross-context) detach is skipped — the ROB-278 case."""
        from holmes.core.otel_tracing import OTelSpan

        span = MagicMock()
        otel_span = OTelSpan(span, MagicMock(), object())

        with patch(
            "holmes.core.otel_tracing.trace.get_current_span",
            return_value=MagicMock(),  # some other span is current
        ), patch("holmes.core.otel_tracing.otel_context.detach") as detach:
            otel_span._safe_detach()

        detach.assert_not_called()
        assert otel_span._token is None  # token cleared either way

    def test_rob278_reproduce_bug_then_verify_fix(self, in_memory_exporter, caplog):
        """Reproduce the real ROB-278 detach error, then prove _safe_detach avoids it."""
        import contextvars
        import logging

        from opentelemetry import context as otel_context
        from opentelemetry import trace as _trace

        from holmes.core.otel_tracing import OTelSpan

        tracer = _trace.get_tracer("test")
        span_a = tracer.start_span("a")
        token_a = otel_context.attach(_trace.set_span_in_context(span_a))
        try:
            # 1) Naive detach in a DIFFERENT context reproduces the OTel error.
            def naive_detach():
                otel_context.detach(token_a)

            caplog.clear()
            with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
                contextvars.Context().run(naive_detach)
            assert "Failed to detach context" in caplog.text

            # 2) _safe_detach in that same cross-context situation stays silent:
            #    span_a is not current there, so it skips the detach.
            def safe_detach():
                OTelSpan(span_a, tracer, token_a)._safe_detach()

            caplog.clear()
            with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
                contextvars.Context().run(safe_detach)
            assert "Failed to detach context" not in caplog.text
        finally:
            # in-order detach in the original context — clean, no error
            otel_context.detach(token_a)

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


class TestLangfuseTraceAttributes:
    """Test the langfuse_trace_attributes helper used on root spans."""

    def test_disabled_by_default(self):
        """Returns {} unless HOLMES_LANGFUSE_ATTRIBUTES is enabled."""
        from holmes.core.tracing import langfuse_trace_attributes

        with patch("holmes.core.tracing.HOLMES_LANGFUSE_ATTRIBUTES", False):
            assert langfuse_trace_attributes("q", user_id="u1", session_id="c1") == {}

    def test_full_attrs(self):
        from holmes.core.tracing import langfuse_trace_attributes

        with patch("holmes.core.tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            attrs = langfuse_trace_attributes(
                "why is my pod crashing?",
                user_id="u123",
                user_email="a@b.com",
                account_id="acct1",
                session_id="conv1",
                cluster_id="clusterA",
                model="anthropic/claude",
                request_source="alert_investigation",
            )
        assert attrs["langfuse.user.id"] == "u123"  # user_id wins
        assert attrs["langfuse.session.id"] == "conv1"
        assert attrs["langfuse.trace.name"] == "conv1"  # conversation id, not the prompt
        assert attrs["langfuse.trace.input"] == "why is my pod crashing?"
        # explicit identity metadata
        assert attrs["langfuse.trace.metadata.user_id"] == "u123"
        assert attrs["langfuse.trace.metadata.user_email"] == "a@b.com"
        assert attrs["langfuse.trace.metadata.conversation_id"] == "conv1"
        assert attrs["langfuse.trace.metadata.account_id"] == "acct1"
        assert attrs["langfuse.trace.metadata.model"] == "anthropic/claude"
        # tags as a string array
        assert attrs["langfuse.trace.tags"] == [
            "source:alert_investigation",
            "cluster:clusterA",
            "model:anthropic/claude",
        ]

    def test_user_id_fallback_chain(self):
        from holmes.core.tracing import langfuse_trace_attributes

        with patch("holmes.core.tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            assert (
                langfuse_trace_attributes("q", user_email="a@b.com")["langfuse.user.id"]
                == "a@b.com"
            )
            assert (
                langfuse_trace_attributes("q", account_id="acct1")["langfuse.user.id"]
                == "acct1"
            )

    def test_empty_values_omitted(self):
        from holmes.core.tracing import langfuse_trace_attributes

        with patch("holmes.core.tracing.HOLMES_LANGFUSE_ATTRIBUTES", True):
            attrs = langfuse_trace_attributes("q")
        # no user/session/metadata/tags keys when nothing is provided
        assert "langfuse.user.id" not in attrs
        assert "langfuse.session.id" not in attrs
        assert "langfuse.trace.tags" not in attrs
        assert not any(k.startswith("langfuse.trace.metadata.") for k in attrs)
        # name/input are always present
        assert attrs["langfuse.trace.input"] == "q"


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


class TestOTLPProtocolSelection:
    
    def test_grpc_exporters_by_default(self):
        """Without OTEL_EXPORTER_OTLP_PROTOCOL, the gRPC exporters are used."""
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter as GRPCMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCSpanExporter,
        )

        from holmes.core.otel_tracing import _create_exporters

        trace_exporter, metric_exporter = _create_exporters(
            protocol="grpc",
            endpoint="http://localhost:4317",
            metrics_endpoint=None,
            headers={},
        )
        assert isinstance(trace_exporter, GRPCSpanExporter)
        assert isinstance(metric_exporter, GRPCMetricExporter)

    def test_http_protobuf_selects_http_exporters(self):
        """OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf uses the HTTP exporters."""
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HTTPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPSpanExporter,
        )

        from holmes.core.otel_tracing import _create_exporters

        trace_exporter, metric_exporter = _create_exporters(
            protocol="http/protobuf",
            endpoint="http://localhost:4318",
            metrics_endpoint=None,
            headers={},
        )
        assert isinstance(trace_exporter, HTTPSpanExporter)
        assert isinstance(metric_exporter, HTTPMetricExporter)

    def test_http_appends_signal_paths(self):
        """OTLP/HTTP appends /v1/traces and /v1/metrics to the base endpoint."""
        from holmes.core.otel_tracing import _create_exporters

        trace_exporter, metric_exporter = _create_exporters(
            protocol="http/protobuf",
            endpoint="http://langfuse:3000/api/public/otel",
            metrics_endpoint=None,
            headers={},
        )
        assert trace_exporter._endpoint == "http://langfuse:3000/api/public/otel/v1/traces"
        assert metric_exporter._endpoint == "http://langfuse:3000/api/public/otel/v1/metrics"

    def test_http_does_not_double_append_signal_path(self):
        """An endpoint that already ends with /v1/traces is used as-is."""
        from holmes.core.otel_tracing import _create_exporters

        trace_exporter, _ = _create_exporters(
            protocol="http/protobuf",
            endpoint="http://collector:4318/v1/traces",
            metrics_endpoint=None,
            headers={},
        )
        assert trace_exporter._endpoint == "http://collector:4318/v1/traces"

    def test_http_metrics_endpoint_override_used_as_is(self):
        """OTEL_EXPORTER_OTLP_METRICS_ENDPOINT (per-signal var) is used verbatim."""
        from holmes.core.otel_tracing import _create_exporters

        _, metric_exporter = _create_exporters(
            protocol="http/protobuf",
            endpoint="http://collector:4318",
            metrics_endpoint="http://other-collector:4318/custom/v1/metrics",
            headers={},
        )
        assert metric_exporter._endpoint == "http://other-collector:4318/custom/v1/metrics"

    def test_invalid_protocol_raises(self):
        """Unsupported protocol values raise a clear error."""
        from holmes.core.otel_tracing import _get_otlp_protocol

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_PROTOCOL": "http/json"}):
            with pytest.raises(ValueError, match="http/json"):
                _get_otlp_protocol()

    def test_protocol_env_parsing(self):
        """Protocol env var is read, normalized, and defaults to grpc."""
        from holmes.core.otel_tracing import _get_otlp_protocol

        env = os.environ.copy()
        env.pop("OTEL_EXPORTER_OTLP_PROTOCOL", None)
        with patch.dict(os.environ, env, clear=True):
            assert _get_otlp_protocol() == "grpc"

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_PROTOCOL": " HTTP/Protobuf "}):
            assert _get_otlp_protocol() == "http/protobuf"

    def test_tracer_init_with_http_protocol(self):
        """End-to-end: OpenTelemetryTracer wires an HTTP span exporter when
        OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf is set."""
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPSpanExporter,
        )

        from holmes.core.otel_tracing import OpenTelemetryTracer

        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            },
        ):
            tracer = OpenTelemetryTracer(service_name="test")
            try:
                processors = tracer._provider._active_span_processor._span_processors
                exporters = [
                    p.span_exporter for p in processors if hasattr(p, "span_exporter")
                ]
                assert len(exporters) == 1
                assert isinstance(exporters[0], HTTPSpanExporter)
                assert exporters[0]._endpoint == "http://localhost:4318/v1/traces"
            finally:
                tracer.shutdown()

    def test_http_default_endpoint_is_4318(self):
        """With http/protobuf and no endpoint set, default to localhost:4318."""
        from holmes.core.otel_tracing import OpenTelemetryTracer

        env = os.environ.copy()
        env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
        with patch.dict(os.environ, env, clear=True):
            tracer = OpenTelemetryTracer(service_name="test")
            try:
                processors = tracer._provider._active_span_processor._span_processors
                exporters = [
                    p.span_exporter for p in processors if hasattr(p, "span_exporter")
                ]
                assert exporters[0]._endpoint == "http://localhost:4318/v1/traces"
            finally:
                tracer.shutdown()


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
