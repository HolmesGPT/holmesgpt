"""Tests for OpenTelemetry tracing infrastructure.

Tests the tracing abstractions in holmes/core/tracing.py and
experimental/otel/ modules (attributes, tracing helpers).
"""

import os
from unittest.mock import patch

import pytest

from holmes.core.tracing import (
    CompositeSpan,
    DummySpan,
    DummyTracer,
    OTELSpan,
    OTELTracer,
    SpanType,
    TracingFactory,
)


# ============ DummySpan Tests ============


class TestDummySpan:
    def test_start_span_returns_dummy_span(self):
        span = DummySpan()
        child = span.start_span(name="child", span_type=SpanType.TOOL)
        assert isinstance(child, DummySpan)

    def test_log_is_noop(self):
        span = DummySpan()
        # Should not raise
        span.log(input="test input", output="test output", metadata={"key": "val"})

    def test_set_attributes_is_noop(self):
        span = DummySpan()
        span.set_attributes(name="test", type="tool", span_attributes={"k": "v"})

    def test_end_is_noop(self):
        span = DummySpan()
        span.end()

    def test_context_manager(self):
        with DummySpan() as span:
            assert isinstance(span, DummySpan)
            span.log(input="inside context")

    def test_context_manager_with_exception(self):
        """DummySpan.__exit__ should not suppress exceptions."""
        with pytest.raises(ValueError, match="test error"):
            with DummySpan():
                raise ValueError("test error")


# ============ DummyTracer Tests ============


class TestDummyTracer:
    def test_start_experiment_returns_none(self):
        tracer = DummyTracer()
        result = tracer.start_experiment(experiment_name="test")
        assert result is None

    def test_start_trace_returns_dummy_span(self):
        tracer = DummyTracer()
        span = tracer.start_trace("test_trace", SpanType.TASK)
        assert isinstance(span, DummySpan)

    def test_get_trace_url_returns_none(self):
        tracer = DummyTracer()
        assert tracer.get_trace_url() is None

    def test_wrap_llm_returns_module_unchanged(self):
        tracer = DummyTracer()
        module = object()
        assert tracer.wrap_llm(module) is module


# ============ TracingFactory Tests ============


class TestTracingFactory:
    def test_returns_dummy_tracer_when_trace_type_is_none(self):
        tracer = TracingFactory.create_tracer(None)
        assert isinstance(tracer, DummyTracer)

    def test_returns_dummy_tracer_when_trace_type_is_empty(self):
        tracer = TracingFactory.create_tracer("")
        assert isinstance(tracer, DummyTracer)

    @patch.dict(os.environ, {}, clear=False)
    def test_returns_dummy_tracer_when_otel_not_enabled(self):
        # Ensure OTEL_ENABLED is not set
        os.environ.pop("OTEL_ENABLED", None)
        tracer = TracingFactory.create_tracer("otel")
        assert isinstance(tracer, DummyTracer)

    def test_returns_dummy_tracer_for_unknown_type(self):
        tracer = TracingFactory.create_tracer("unknown_backend")
        assert isinstance(tracer, DummyTracer)

    def test_init_otel_returns_false_when_experimental_unavailable(self):
        """TracingFactory.init_otel should return False when experimental modules aren't available."""
        TracingFactory._otel_initialized = False  # Reset state
        with patch("holmes.core.tracing.OTEL_EXPERIMENTAL_AVAILABLE", False):
            result = TracingFactory.init_otel()
            assert result is False
        TracingFactory._otel_initialized = False  # Clean up


# ============ CompositeSpan Tests ============


class TestCompositeSpan:
    def test_start_span_delegates_to_all_children(self):
        spans = [DummySpan(), DummySpan(), DummySpan()]
        composite = CompositeSpan(spans)
        child = composite.start_span(name="child", span_type=SpanType.TOOL)
        assert isinstance(child, CompositeSpan)

    def test_log_delegates_to_all_children(self):
        spans = [DummySpan(), DummySpan()]
        composite = CompositeSpan(spans)
        # Should not raise
        composite.log(input="test", output="result")

    def test_set_attributes_delegates_to_all(self):
        spans = [DummySpan(), DummySpan()]
        composite = CompositeSpan(spans)
        composite.set_attributes(name="test")

    def test_end_delegates_to_all(self):
        spans = [DummySpan(), DummySpan()]
        composite = CompositeSpan(spans)
        composite.end()

    def test_context_manager(self):
        spans = [DummySpan(), DummySpan()]
        with CompositeSpan(spans) as span:
            assert isinstance(span, CompositeSpan)

    def test_context_manager_with_exception(self):
        """CompositeSpan should propagate exceptions from context manager."""
        spans = [DummySpan(), DummySpan()]
        with pytest.raises(RuntimeError, match="composite error"):
            with CompositeSpan(spans):
                raise RuntimeError("composite error")


# ============ OTELTracer Tests ============


class TestOTELTracer:
    def test_ensure_initialized_without_experimental(self):
        """OTELTracer should handle missing experimental modules gracefully."""
        with patch("holmes.core.tracing.OTEL_EXPERIMENTAL_AVAILABLE", False):
            tracer = OTELTracer()
            tracer._ensure_initialized()
            assert tracer._initialized is True
            assert tracer._native_tracer is None

    def test_start_trace_returns_dummy_when_no_native_tracer(self):
        """OTELTracer.start_trace should return DummySpan when native tracer unavailable."""
        with patch("holmes.core.tracing.OTEL_EXPERIMENTAL_AVAILABLE", False):
            tracer = OTELTracer()
            span = tracer.start_trace("test", SpanType.TASK)
            assert isinstance(span, DummySpan)

    def test_get_trace_url_returns_none(self):
        tracer = OTELTracer()
        assert tracer.get_trace_url() is None

    def test_wrap_llm_returns_module_unchanged(self):
        tracer = OTELTracer()
        module = object()
        assert tracer.wrap_llm(module) is module

    def test_get_otel_span_name_with_span_type(self):
        tracer = OTELTracer()
        name = tracer._get_otel_span_name("my_tool", SpanType.TOOL)
        assert "execute_tool" in name
        assert "my_tool" in name

    def test_get_otel_span_name_without_span_type(self):
        tracer = OTELTracer()
        name = tracer._get_otel_span_name("raw_name", None)
        assert name == "raw_name"


# ============ Attribute Tests ============


class TestOTELAttributes:
    def test_truncate_none_returns_empty(self):
        from experimental.otel.attributes import truncate

        assert truncate(None) == ""

    def test_truncate_short_string_unchanged(self):
        from experimental.otel.attributes import truncate

        short = "hello world"
        assert truncate(short) == short

    def test_truncate_exact_limit_unchanged(self):
        from experimental.otel.attributes import MAX_ATTRIBUTE_SIZE, truncate

        exact = "a" * MAX_ATTRIBUTE_SIZE
        assert truncate(exact) == exact
        assert len(truncate(exact)) == MAX_ATTRIBUTE_SIZE

    def test_truncate_over_limit(self):
        from experimental.otel.attributes import (
            MAX_ATTRIBUTE_SIZE,
            TRUNCATION_MARKER,
            truncate,
        )

        over = "x" * (MAX_ATTRIBUTE_SIZE + 100)
        result = truncate(over)
        assert len(result) <= MAX_ATTRIBUTE_SIZE
        assert result.endswith(TRUNCATION_MARKER)

    def test_key_constants_are_non_empty_strings(self):
        from experimental.otel.attributes import (
            AGENT_NAME,
            CONVERSATION_ID,
            INPUT_TOKENS,
            MODEL,
            OPERATION_NAME,
            OUTPUT_TOKENS,
            TOOL_CALL_ID,
            TOOL_NAME,
        )

        for const in [
            OPERATION_NAME,
            CONVERSATION_ID,
            MODEL,
            INPUT_TOKENS,
            OUTPUT_TOKENS,
            TOOL_NAME,
            TOOL_CALL_ID,
            AGENT_NAME,
        ]:
            assert isinstance(const, str)
            assert len(const) > 0


# ============ LoggingSpanExporter Tests ============


class TestLoggingSpanExporter:
    def test_inherits_span_exporter(self):
        from opentelemetry.sdk.trace.export import SpanExporter

        from experimental.otel.tracing import LoggingSpanExporter

        assert issubclass(LoggingSpanExporter, SpanExporter)


# ============ AWS Auth Helper Tests ============


class TestAWSAuthHelpers:
    def test_needs_aws_auth_osis_endpoint(self):
        from experimental.otel.tracing import needs_aws_auth

        assert needs_aws_auth("https://pipeline.us-east-1.osis.amazonaws.com/v1/traces") is True

    def test_needs_aws_auth_es_endpoint(self):
        from experimental.otel.tracing import needs_aws_auth

        assert needs_aws_auth("https://domain.us-west-2.es.amazonaws.com") is True

    def test_needs_aws_auth_standard_endpoint(self):
        from experimental.otel.tracing import needs_aws_auth

        assert needs_aws_auth("https://otel-collector.example.com:4318/v1/traces") is False

    @patch.dict(os.environ, {"OTEL_AWS_PROFILE": "my-profile"}, clear=False)
    def test_needs_aws_auth_with_profile_set(self):
        from experimental.otel.tracing import needs_aws_auth

        assert needs_aws_auth("https://otel-collector.example.com:4318") is True

    def test_region_extraction_from_osis_endpoint(self):
        from experimental.otel.tracing import _extract_region_from_endpoint

        region = _extract_region_from_endpoint(
            "https://pipeline-abc.us-west-2.osis.amazonaws.com/v1/traces"
        )
        assert region == "us-west-2"

    def test_region_extraction_fallback_for_non_osis(self):
        from experimental.otel.tracing import _extract_region_from_endpoint

        region = _extract_region_from_endpoint("https://otel-collector.example.com:4318")
        assert region == "us-east-1"

    def test_region_extraction_fallback_for_invalid_url(self):
        from experimental.otel.tracing import _extract_region_from_endpoint

        region = _extract_region_from_endpoint("not-a-url")
        assert region == "us-east-1"


# ============ Metrics No-op Tests ============


class TestMetricsNoop:
    def test_metric_functions_are_noop_when_not_initialized(self):
        """All metric recording functions should be no-ops when instruments are None."""
        from experimental.otel.metrics import (
            increment_errors,
            increment_iterations,
            increment_tool_calls,
            record_operation_duration,
            record_token_usage,
            record_tool_duration,
        )

        # These should all complete without error even when metrics are not initialized
        record_token_usage(tokens=100, token_type="input", model="test", operation_name="chat")
        record_operation_duration(
            duration_seconds=1.5, operation_name="test", model="test", agent_name="test", success=True
        )
        record_tool_duration(duration_seconds=0.5, tool_name="test", success=True)
        increment_iterations(count=1, model="test", agent_name="test")
        increment_tool_calls(count=1, tool_name="test", model="test")
        increment_errors(count=1, error_type="test", operation_name="test")
