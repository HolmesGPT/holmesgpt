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
        init_otel_tracer,
        get_tracer,
        shutdown_otel_tracer,
        set_span_error,
        REQUEST_ID,
        CONVERSATION_ID,
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
        RESULT_SUCCESS,
        SPAN_AGENT_RUN,
        SPAN_TOOL_EXECUTE,
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

    # Test over limit
    over = "x" * (MAX_ATTRIBUTE_SIZE + 100)
    result = truncate(over)
    assert len(result) < len(over), "Expected truncation"
    assert result.endswith("...[TRUNCATED]"), f"Expected truncation marker, got {result[-20:]!r}"
    print(f"  ✅ truncate(string of {MAX_ATTRIBUTE_SIZE + 100} chars) truncates correctly")

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

    # Check tool attributes
    assert attr.TOOL_NAME == "gen_ai.tool.name"
    assert attr.TOOL_CALL_ID == "gen_ai.tool.call_id"
    assert attr.TOOL_DURATION_MS == "gen_ai.tool.duration_ms"
    print("  ✅ Tool attributes defined correctly")

    # Check span names
    assert attr.SPAN_AGENT_RUN == "agent.run"
    assert attr.SPAN_TOOL_EXECUTE == "tool.execute"
    print("  ✅ Span names defined correctly")


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
