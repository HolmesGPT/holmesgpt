#!/usr/bin/env python3
"""Quick verification script for OTEL module - can be converted to pytest later."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_imports():
    """Test that all exports are importable."""
    print("Testing imports...")
    from experimental.otel import (
        # Tracing
        init_otel_tracer,
        get_tracer,
        shutdown_otel_tracer,
        set_span_error,
        # Metrics
        init_otel_metrics,
        get_meter,
        shutdown_otel_metrics,
        record_token_usage,
        record_operation_duration,
        record_tool_duration,
        increment_iterations,
        increment_tool_calls,
        increment_errors,
        # Logging
        OTELContextFormatter,
        setup_otel_logging,
        get_otel_logger,
        log_llm_call,
        log_tool_execution,
        log_agent_start,
        log_agent_complete,
        log_agent_error,
        # Attributes
        REQUEST_ID,
        CONVERSATION_ID,
        OPERATION_NAME,
        PROVIDER_NAME,
        MODEL,
        INPUT_TOKENS,
        OUTPUT_TOKENS,
        TOTAL_TOKENS,
        TOOL_NAME,
        TOOL_CALL_ID,
        TOOL_INPUT,
        TOOL_OUTPUT,
        TOOL_DURATION_MS,
        AGENT_TYPE,
        AGENT_NAME,
        AGENT_ITERATION,
        RESULT_SUCCESS,
        COST_USD,
        # Span names
        SPAN_AGENT_RUN,
        SPAN_TOOL_EXECUTE,
        SPAN_INVOKE_AGENT,
        SPAN_CHAT,
        SPAN_EXECUTE_TOOL,
        # Metric names
        METRIC_TOKEN_USAGE,
        METRIC_OPERATION_DURATION,
        METRIC_TOOL_DURATION,
        METRIC_AGENT_ITERATIONS,
        METRIC_AGENT_TOOL_CALLS,
        METRIC_AGENT_ERRORS,
        # Token type
        TOKEN_TYPE,
        TOKEN_TYPE_INPUT,
        TOKEN_TYPE_OUTPUT,
        # Utilities
        truncate,
        MAX_ATTRIBUTE_SIZE,
    )
    print("  ✅ All imports successful")


def test_truncate_function():
    """Test truncate function handles various inputs."""
    print("Testing truncate function...")
    from experimental.otel.attributes import truncate, MAX_ATTRIBUTE_SIZE

    # Test None input
    result = truncate(None)
    assert result == "", f"Expected '', got {result!r}"
    print("  ✅ truncate(None) returns ''")

    # Test short string (no truncation)
    result = truncate("short string")
    assert result == "short string", f"Expected 'short string', got {result!r}"
    print("  ✅ truncate('short string') returns unchanged")

    # Test exact limit
    exact = "x" * MAX_ATTRIBUTE_SIZE
    result = truncate(exact)
    assert result == exact, "Expected no truncation at exact limit"
    print(f"  ✅ truncate(string of {MAX_ATTRIBUTE_SIZE} chars) returns unchanged")

    # Test over limit - result should stay within max_size including marker
    over = "x" * (MAX_ATTRIBUTE_SIZE + 100)
    result = truncate(over)
    assert len(result) <= MAX_ATTRIBUTE_SIZE, f"Expected result <= {MAX_ATTRIBUTE_SIZE}, got {len(result)}"
    assert result.endswith("...[TRUNCATED]"), f"Expected truncation marker, got {result[-20:]!r}"
    print(f"  ✅ truncate(string of {MAX_ATTRIBUTE_SIZE + 100} chars) truncates correctly (result len: {len(result)})")

    # Test empty string
    result = truncate("")
    assert result == "", f"Expected '', got {result!r}"
    print("  ✅ truncate('') returns ''")


def test_extract_region_from_endpoint():
    """Test region extraction from OSIS endpoints."""
    print("Testing region extraction...")
    from experimental.otel.tracing import _extract_region_from_endpoint

    # Valid OSIS endpoint
    result = _extract_region_from_endpoint("https://xxx.us-west-2.osis.amazonaws.com/v1/traces")
    assert result == "us-west-2", f"Expected 'us-west-2', got {result!r}"
    print("  ✅ Extracts 'us-west-2' from valid OSIS endpoint")

    # Different region
    result = _extract_region_from_endpoint("https://pipeline.eu-central-1.osis.amazonaws.com/v1/traces")
    assert result == "eu-central-1", f"Expected 'eu-central-1', got {result!r}"
    print("  ✅ Extracts 'eu-central-1' from valid OSIS endpoint")

    # Invalid endpoint (no osis)
    result = _extract_region_from_endpoint("https://localhost:4318/v1/traces")
    assert result == "us-east-1", f"Expected fallback 'us-east-1', got {result!r}"
    print("  ✅ Falls back to 'us-east-1' for non-OSIS endpoint")

    # Malformed URL (should not crash)
    result = _extract_region_from_endpoint("not-a-url")
    assert result == "us-east-1", f"Expected fallback 'us-east-1', got {result!r}"
    print("  ✅ Falls back to 'us-east-1' for malformed URL")

    # Empty string
    result = _extract_region_from_endpoint("")
    assert result == "us-east-1", f"Expected fallback 'us-east-1', got {result!r}"
    print("  ✅ Falls back to 'us-east-1' for empty string")


def test_tracer_disabled_by_default():
    """Test that tracer is disabled when OTEL_ENABLED is not set."""
    print("Testing tracer initialization (disabled)...")

    # Ensure OTEL is disabled
    os.environ.pop("OTEL_ENABLED", None)
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    # Need to reset the module state for clean test
    from experimental.otel import tracing
    tracing._initialized = False
    tracing._tracer_provider = None

    from experimental.otel.tracing import init_otel_tracer, get_tracer

    result = init_otel_tracer()
    assert result is False, f"Expected False (disabled), got {result}"
    print("  ✅ init_otel_tracer() returns False when OTEL_ENABLED not set")

    # Should still be able to get a tracer (no-op)
    tracer = get_tracer("test")
    assert tracer is not None, "Expected a tracer instance"
    print("  ✅ get_tracer() returns a no-op tracer when disabled")

    # Tracer should have start_span method
    span = tracer.start_span("test-span")
    assert span is not None, "Expected a span instance"
    span.end()
    print("  ✅ No-op tracer can create and end spans without error")


def test_set_span_error():
    """Test set_span_error function."""
    print("Testing set_span_error...")
    from experimental.otel.tracing import set_span_error, get_tracer

    tracer = get_tracer("test")
    span = tracer.start_span("test-span")

    # Should not raise
    try:
        set_span_error(span, ValueError("test error"))
        print("  ✅ set_span_error() handles exceptions without raising")
    except Exception as e:
        print(f"  ❌ set_span_error() raised: {e}")
        raise

    span.end()


def test_attribute_constants():
    """Test that attribute constants are properly defined."""
    print("Testing attribute constants...")
    from experimental.otel import attributes as attr

    # Check Gen AI standard attributes
    assert attr.REQUEST_ID == "gen_ai.request.id"
    assert attr.CONVERSATION_ID == "gen_ai.conversation.id"
    assert attr.MODEL == "gen_ai.request.model"
    print("  ✅ Gen AI standard attributes defined correctly")

    # Check new Gen AI required attributes
    assert attr.OPERATION_NAME == "gen_ai.operation.name"
    assert attr.PROVIDER_NAME == "gen_ai.provider.name"
    print("  ✅ Gen AI required attributes defined correctly")

    # Check tool attributes (library uses dots, e.g., gen_ai.tool.call.id)
    assert attr.TOOL_NAME == "gen_ai.tool.name"
    assert attr.TOOL_CALL_ID == "gen_ai.tool.call.id"  # Library uses dots
    assert attr.TOOL_DURATION_MS == "gen_ai.tool.duration_ms"  # Custom attribute
    print("  ✅ Tool attributes defined correctly")

    # Check legacy span names
    assert attr.SPAN_AGENT_RUN == "agent.run"
    assert attr.SPAN_TOOL_EXECUTE == "tool.execute"
    print("  ✅ Legacy span names defined correctly")

    # Check Gen AI semantic convention span names
    assert attr.SPAN_INVOKE_AGENT == "invoke_agent"
    assert attr.SPAN_CHAT == "chat"
    assert attr.SPAN_EXECUTE_TOOL == "execute_tool"
    print("  ✅ Gen AI span names defined correctly")

    # Check metric names
    assert attr.METRIC_TOKEN_USAGE == "gen_ai.client.token.usage"
    assert attr.METRIC_OPERATION_DURATION == "gen_ai.client.operation.duration"
    assert attr.METRIC_TOOL_DURATION == "gen_ai.tool.duration"
    print("  ✅ Metric names defined correctly")

    # Check agent-specific attributes
    assert attr.AGENT_NAME == "gen_ai.agent.name"
    assert attr.AGENT_ITERATION == "gen_ai.agent.iteration"
    print("  ✅ Agent attributes defined correctly")


def test_server_agui_imports():
    """Test that server-agui.py can import the OTEL module."""
    print("Testing server-agui.py compatibility...")

    # Simulate what server-agui.py does
    from opentelemetry import trace
    from experimental.otel.tracing import init_otel_tracer, get_tracer, set_span_error
    from experimental.otel import attributes as otel_attr

    # Check the attributes used in server-agui.py exist
    assert hasattr(otel_attr, "SPAN_AGENT_RUN")
    assert hasattr(otel_attr, "SPAN_TOOL_EXECUTE")
    assert hasattr(otel_attr, "REQUEST_ID")
    assert hasattr(otel_attr, "CONVERSATION_ID")
    assert hasattr(otel_attr, "AGENT_TYPE")
    assert hasattr(otel_attr, "MODEL")
    assert hasattr(otel_attr, "TOOL_NAME")
    assert hasattr(otel_attr, "TOOL_CALL_ID")
    assert hasattr(otel_attr, "TOOL_DURATION_MS")
    assert hasattr(otel_attr, "TOOL_OUTPUT")
    assert hasattr(otel_attr, "RESULT_SUCCESS")
    assert hasattr(otel_attr, "truncate")
    print("  ✅ All attributes used by server-agui.py are available")


def test_metrics_disabled_by_default():
    """Test that metrics are disabled when OTEL_ENABLED is not set."""
    print("Testing metrics initialization (disabled)...")

    # Ensure OTEL is disabled
    os.environ.pop("OTEL_ENABLED", None)
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    from experimental.otel.metrics import init_otel_metrics, get_meter

    # Reset module state
    import experimental.otel.metrics as metrics_module
    metrics_module._meter = None
    metrics_module._meter_provider = None

    result = init_otel_metrics()
    assert result is False, f"Expected False (disabled), got {result}"
    print("  ✅ init_otel_metrics() returns False when OTEL_ENABLED not set")

    # Should return None when disabled
    meter = get_meter()
    assert meter is None, f"Expected None when disabled, got {meter}"
    print("  ✅ get_meter() returns None when disabled")


def test_metrics_helpers_no_op_when_disabled():
    """Test that metric helper functions don't crash when disabled."""
    print("Testing metric helpers (no-op when disabled)...")

    from experimental.otel.metrics import (
        record_token_usage,
        record_operation_duration,
        record_tool_duration,
        increment_iterations,
        increment_tool_calls,
        increment_errors,
    )

    # All of these should not raise when metrics are disabled
    try:
        record_token_usage(100, "input", "gpt-4")
        record_operation_duration(1.5, "invoke_agent", "gpt-4")
        record_tool_duration(0.5, "kubectl_get_pods")
        increment_iterations(3, "gpt-4", "HolmesGPT")
        increment_tool_calls(2, "prometheus_query", "gpt-4")
        increment_errors(1, "ValueError", "invoke_agent")
        print("  ✅ All metric helpers work without raising when disabled")
    except Exception as e:
        print(f"  ❌ Metric helper raised: {e}")
        raise


def test_logging_formatter():
    """Test the OTEL context formatter."""
    print("Testing OTEL logging formatter...")

    from experimental.otel.otel_logging import OTELContextFormatter
    import logging

    formatter = OTELContextFormatter(
        "%(asctime)s [%(trace_id)s/%(span_id)s] %(message)s"
    )

    # Create a test log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    # Format should work without a span context
    formatted = formatter.format(record)
    assert "0" * 32 in formatted, "Expected zero trace_id when no span"
    assert "0" * 16 in formatted, "Expected zero span_id when no span"
    print("  ✅ Formatter adds zero trace/span IDs when no active span")


def test_logging_helpers():
    """Test structured logging helper functions."""
    print("Testing logging helpers...")

    import logging
    from experimental.otel.otel_logging import (
        log_llm_call,
        log_tool_execution,
        log_agent_start,
        log_agent_complete,
        log_agent_error,
    )

    logger = logging.getLogger("test.otel")
    logger.setLevel(logging.DEBUG)

    # All of these should not raise
    try:
        log_llm_call(logger, "gpt-4", 100, 50, "stop", 0.001, 1)
        log_tool_execution(logger, "kubectl", 500, True, "call-1")
        log_agent_start(logger, "HolmesGPT", "gpt-4", "run-1", "thread-1")
        log_agent_complete(logger, "HolmesGPT", 1500, 5, 2.5, 3)
        log_agent_error(logger, "HolmesGPT", ValueError("test"))
        print("  ✅ All logging helpers work without raising")
    except Exception as e:
        print(f"  ❌ Logging helper raised: {e}")
        raise


def test_stream_events():
    """Test that new stream events are defined."""
    print("Testing stream events...")

    from holmes.utils.stream import (
        StreamEvents,
        build_stream_event_llm_iteration_start,
        build_stream_event_llm_iteration_complete,
    )

    # Check new events exist
    assert hasattr(StreamEvents, "LLM_ITERATION_START")
    assert hasattr(StreamEvents, "LLM_ITERATION_COMPLETE")
    print("  ✅ New stream events defined")

    # Check event values
    assert StreamEvents.LLM_ITERATION_START.value == "llm_iteration_start"
    assert StreamEvents.LLM_ITERATION_COMPLETE.value == "llm_iteration_complete"
    print("  ✅ Event values are correct")

    # Test builder functions
    start_event = build_stream_event_llm_iteration_start(1, "gpt-4")
    assert start_event.event == StreamEvents.LLM_ITERATION_START
    assert start_event.data["iteration"] == 1
    assert start_event.data["model"] == "gpt-4"
    print("  ✅ build_stream_event_llm_iteration_start works")

    complete_event = build_stream_event_llm_iteration_complete(
        iteration=1,
        model="gpt-4",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        finish_reason="stop",
        cost_usd=0.001,
    )
    assert complete_event.event == StreamEvents.LLM_ITERATION_COMPLETE
    assert complete_event.data["iteration"] == 1
    assert complete_event.data["prompt_tokens"] == 100
    assert complete_event.data["completion_tokens"] == 50
    assert complete_event.data["total_tokens"] == 150
    assert complete_event.data["finish_reason"] == "stop"
    assert complete_event.data["cost_usd"] == 0.001
    print("  ✅ build_stream_event_llm_iteration_complete works")


def test_tracing_factory_otel_tracer():
    """Test TracingFactory OTEL tracer integration."""
    print("Testing TracingFactory OTEL integration...")

    from holmes.core.tracing import (
        TracingFactory,
        SpanType,
        OTELTracer,
        OTELSpan,
        DummyTracer,
        DummySpan,
    )

    # Ensure OTEL is disabled for this test
    os.environ.pop("OTEL_ENABLED", None)
    # Reset class state for clean test
    TracingFactory._otel_initialized = False

    # Test that create_tracer returns DummyTracer when OTEL is disabled
    tracer = TracingFactory.create_tracer("otel")
    assert isinstance(tracer, DummyTracer), f"Expected DummyTracer, got {type(tracer)}"
    print("  ✅ create_tracer('otel') returns DummyTracer when disabled")

    # Test that init_otel returns False when disabled (need to reset module state too)
    from experimental.otel import tracing as otel_tracing
    otel_tracing._initialized = False
    result = TracingFactory.init_otel()
    assert result is False, f"Expected False, got {result}"
    print("  ✅ init_otel() returns False when OTEL_ENABLED not set")

    # Test OTELTracer class exists and has required methods
    assert hasattr(OTELTracer, "start_trace")
    assert hasattr(OTELTracer, "start_experiment")
    assert hasattr(OTELTracer, "get_trace_url")
    assert hasattr(OTELTracer, "wrap_llm")
    print("  ✅ OTELTracer class has required methods")

    # Test OTELSpan class exists and has required methods
    assert hasattr(OTELSpan, "start_span")
    assert hasattr(OTELSpan, "log")
    assert hasattr(OTELSpan, "set_attributes")
    assert hasattr(OTELSpan, "end")
    assert hasattr(OTELSpan, "__enter__")
    assert hasattr(OTELSpan, "__exit__")
    print("  ✅ OTELSpan class has required methods")


def test_tracing_factory_dummy_span_context_manager():
    """Test DummySpan context manager behavior."""
    print("Testing DummySpan context manager...")

    from holmes.core.tracing import TracingFactory, SpanType

    # Ensure OTEL is disabled
    os.environ.pop("OTEL_ENABLED", None)

    tracer = TracingFactory.create_tracer("otel")

    # Test context manager usage
    span_entered = False
    span_exited = False

    with tracer.start_trace("test trace", SpanType.TASK) as span:
        span_entered = True
        # Test nested span creation
        with span.start_span("child span", SpanType.TOOL) as child:
            assert child is not None
            # Test log method
            child.log(input="test input", output="test output")
            # Test set_attributes
            child.set_attributes(span_attributes={"key": "value"})
        span_exited = True

    assert span_entered, "Span context manager __enter__ not called"
    assert span_exited, "Span context manager body not completed"
    print("  ✅ DummySpan context manager works correctly")
    print("  ✅ Nested spans work correctly")
    print("  ✅ log() and set_attributes() work without error")


def test_tracing_factory_composite_tracer():
    """Test CompositeTracer for dual tracing."""
    print("Testing CompositeTracer...")

    from holmes.core.tracing import (
        TracingFactory,
        SpanType,
        CompositeTracer,
        CompositeSpan,
        DummyTracer,
    )

    # When both BRAINTRUST and OTEL are disabled, should return DummyTracer
    # (CompositeTracer is only returned when at least 2 active tracers exist)
    os.environ.pop("OTEL_ENABLED", None)
    os.environ.pop("BRAINTRUST_API_KEY", None)
    TracingFactory._otel_initialized = False

    tracer = TracingFactory.create_tracer("braintrust,otel")
    assert isinstance(tracer, DummyTracer), f"Expected DummyTracer when both tracers disabled, got {type(tracer)}"
    print("  ✅ create_tracer('braintrust,otel') returns DummyTracer when both disabled")

    # Test CompositeTracer class exists and has correct methods
    assert hasattr(CompositeTracer, "start_trace")
    assert hasattr(CompositeTracer, "start_experiment")
    assert hasattr(CompositeTracer, "get_trace_url")
    assert hasattr(CompositeTracer, "wrap_llm")
    print("  ✅ CompositeTracer class has required methods")

    # Test CompositeSpan class exists and has correct methods
    assert hasattr(CompositeSpan, "start_span")
    assert hasattr(CompositeSpan, "log")
    assert hasattr(CompositeSpan, "set_attributes")
    assert hasattr(CompositeSpan, "end")
    print("  ✅ CompositeSpan class has required methods")

    # Test that DummyTracer still works as expected for dual tracing fallback
    with tracer.start_trace("test", SpanType.TASK) as span:
        with span.start_span("child", SpanType.TOOL) as child:
            child.log(input="test")
            child.set_attributes(span_attributes={"test": "value"})
    print("  ✅ Fallback DummyTracer works correctly for dual tracing")


def test_tracing_factory_span_types():
    """Test SpanType enum and mappings."""
    print("Testing SpanType enum...")

    from holmes.core.tracing import SpanType, SPAN_TYPE_TO_OTEL

    # Test all span types exist
    expected_types = ["LLM", "SCORE", "FUNCTION", "EVAL", "TASK", "TOOL"]
    for type_name in expected_types:
        assert hasattr(SpanType, type_name), f"SpanType.{type_name} not found"
    print("  ✅ All expected SpanType values exist")

    # Test OTEL mappings exist for all span types
    for span_type in SpanType:
        assert span_type in SPAN_TYPE_TO_OTEL, f"No OTEL mapping for {span_type}"
    print("  ✅ All SpanTypes have OTEL mappings")

    # Test specific mappings follow Gen AI conventions
    assert SPAN_TYPE_TO_OTEL[SpanType.LLM] == "chat"
    assert SPAN_TYPE_TO_OTEL[SpanType.TOOL] == "execute_tool"
    assert SPAN_TYPE_TO_OTEL[SpanType.TASK] == "invoke_agent"
    print("  ✅ SpanType mappings follow Gen AI semantic conventions")


def test_server_uses_tracing_factory():
    """Test that server.py uses TracingFactory correctly."""
    print("Testing server.py TracingFactory usage...")

    # Read server.py and verify it imports and uses TracingFactory
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "server.py"
    )

    with open(server_path, "r") as f:
        server_content = f.read()

    # Check imports
    assert "from holmes.core.tracing import TracingFactory" in server_content
    print("  ✅ server.py imports TracingFactory")

    # Check TracingFactory.init_otel() usage
    assert "TracingFactory.init_otel()" in server_content
    print("  ✅ server.py uses TracingFactory.init_otel()")

    # Check TracingFactory.create_tracer usage
    assert 'TracingFactory.create_tracer("otel")' in server_content
    print("  ✅ server.py uses TracingFactory.create_tracer")


def test_agui_uses_tracing_factory():
    """Test that server-agui.py uses TracingFactory correctly."""
    print("Testing server-agui.py TracingFactory usage...")

    # Read server-agui.py and verify it imports and uses TracingFactory
    agui_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ag-ui",
        "server-agui.py"
    )

    with open(agui_path, "r") as f:
        agui_content = f.read()

    # Check imports
    assert "from holmes.core.tracing import TracingFactory" in agui_content
    print("  ✅ server-agui.py imports TracingFactory")

    # Check TracingFactory.init_otel() usage
    assert "TracingFactory.init_otel()" in agui_content
    print("  ✅ server-agui.py uses TracingFactory.init_otel()")


def main():
    """Run all tests."""
    print("=" * 60)
    print("OTEL Module Verification")
    print("=" * 60)
    print()

    tests = [
        test_imports,
        test_truncate_function,
        test_extract_region_from_endpoint,
        test_tracer_disabled_by_default,
        test_set_span_error,
        test_attribute_constants,
        test_server_agui_imports,
        test_metrics_disabled_by_default,
        test_metrics_helpers_no_op_when_disabled,
        test_logging_formatter,
        test_logging_helpers,
        test_stream_events,
        # New TracingFactory integration tests
        test_tracing_factory_otel_tracer,
        test_tracing_factory_dummy_span_context_manager,
        test_tracing_factory_composite_tracer,
        test_tracing_factory_span_types,
        test_server_uses_tracing_factory,
        test_agui_uses_tracing_factory,
    ]

    failed = 0
    for test in tests:
        try:
            test()
            print()
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            print()
            failed += 1

    print("=" * 60)
    if failed == 0:
        print(f"✅ All {len(tests)} tests passed!")
    else:
        print(f"❌ {failed}/{len(tests)} tests failed")
    print("=" * 60)

    return failed


if __name__ == "__main__":
    sys.exit(main())
