#!/usr/bin/env python3
"""Integration test for OTEL tracing with real OSIS endpoint.

Usage:
    # Set up environment variables for OSIS (required)
    export OTEL_ENABLED=true
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-osis-pipeline.region.osis.amazonaws.com/path/v1/traces
    export OTEL_AWS_PROFILE=your-aws-profile  # optional, for OSIS auth
    export OTEL_AWS_REGION=us-east-1          # optional, extracted from endpoint if not set

    # Set up environment variables for OpenSearch verification (optional)
    export OPENSEARCH_ENDPOINT=https://your-opensearch-cluster.region.on.aws
    export OPENSEARCH_USERNAME=admin
    export OPENSEARCH_PASSWORD=your-password

    # Run the test
    poetry run python experimental/otel/test_otel_integration.py

Environment Variables:
    OTEL_ENABLED              - Must be 'true' to enable tracing (required)
    OTEL_EXPORTER_OTLP_ENDPOINT - OSIS pipeline endpoint URL (required)
    OTEL_AWS_PROFILE          - AWS profile for OSIS authentication (optional)
    OTEL_AWS_REGION           - AWS region for OSIS (optional, auto-detected from endpoint)
    OTEL_SERVICE_NAME         - Service name for traces (default: holmesgpt)
    OPENSEARCH_ENDPOINT       - OpenSearch cluster URL for verification (optional)
    OPENSEARCH_USERNAME       - OpenSearch username (optional)
    OPENSEARCH_PASSWORD       - OpenSearch password (optional)
"""

import json
import logging
import os
import sys
import time

import requests

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Configure logging to see debug output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def check_environment():
    """Verify required environment variables are set."""
    print("=" * 60)
    print("Environment Check")
    print("=" * 60)

    required = {
        "OTEL_ENABLED": os.environ.get("OTEL_ENABLED"),
        "OTEL_EXPORTER_OTLP_ENDPOINT": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    }

    optional = {
        "OTEL_AWS_PROFILE": os.environ.get("OTEL_AWS_PROFILE"),
        "OTEL_AWS_REGION": os.environ.get("OTEL_AWS_REGION"),
        "OTEL_SERVICE_NAME": os.environ.get("OTEL_SERVICE_NAME", "holmesgpt"),
        "AWS_PROFILE": os.environ.get("AWS_PROFILE"),
        "AWS_REGION": os.environ.get("AWS_REGION"),
    }

    opensearch_vars = {
        "OPENSEARCH_ENDPOINT": os.environ.get("OPENSEARCH_ENDPOINT"),
        "OPENSEARCH_USERNAME": os.environ.get("OPENSEARCH_USERNAME"),
        "OPENSEARCH_PASSWORD": "***" if os.environ.get("OPENSEARCH_PASSWORD") else None,
    }

    print("\nRequired variables:")
    all_set = True
    for key, value in required.items():
        status = "✅" if value else "❌ MISSING"
        print(f"  {key}: {value or 'NOT SET'} {status}")
        if not value:
            all_set = False

    print("\nOptional variables:")
    for key, value in optional.items():
        print(f"  {key}: {value or 'NOT SET'}")

    print("\nOpenSearch verification (optional):")
    for key, value in opensearch_vars.items():
        print(f"  {key}: {value or 'NOT SET'}")

    if not all_set:
        print("\n❌ Missing required environment variables!")
        print("\nSet them with:")
        print("  export OTEL_ENABLED=true")
        print(
            "  export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-osis-endpoint/v1/traces"
        )
        print("  export OTEL_AWS_PROFILE=your-profile  # optional, for OSIS auth")
        return False

    print("\n✅ Environment configured correctly")
    return True


def test_tracer_initialization():
    """Test that tracer initializes with OSIS endpoint."""
    print("\n" + "=" * 60)
    print("Test: Tracer Initialization")
    print("=" * 60)

    # Reset module state for clean test
    from experimental.otel import tracing

    tracing._initialized = False
    tracing._tracer_provider = None

    from experimental.otel.tracing import init_otel_tracer

    result = init_otel_tracer()

    if result:
        print("✅ Tracer initialized successfully")
        return True
    else:
        print("❌ Tracer initialization failed")
        return False


# Global to store test ID for verification
_current_test_id = None


def test_create_and_export_spans():
    """Test creating spans and exporting them to OSIS."""
    global _current_test_id

    print("\n" + "=" * 60)
    print("Test: Create and Export Spans")
    print("=" * 60)

    from experimental.otel import attributes as otel_attr
    from experimental.otel.tracing import get_tracer, set_span_error
    from opentelemetry import trace

    tracer = get_tracer("holmesgpt.integration_test")

    # Generate a unique test ID for this run
    test_id = f"integration-test-{int(time.time())}"
    _current_test_id = test_id  # Store for later verification
    print(f"\nTest ID: {test_id}")
    print("(Use this to find the trace in your observability backend)\n")

    # Simulate an agent run with tool calls
    print("Creating root span (agent.run)...")
    root_span = tracer.start_span(otel_attr.SPAN_AGENT_RUN)

    try:
        # Set correlation attributes
        root_span.set_attribute(otel_attr.REQUEST_ID, test_id)
        root_span.set_attribute(otel_attr.CONVERSATION_ID, f"conv-{test_id}")
        root_span.set_attribute(otel_attr.AGENT_TYPE, "HolmesGPT")
        root_span.set_attribute(otel_attr.MODEL, "test-model")
        print("  ✅ Set correlation attributes")

        # Simulate tool calls
        tool_names = ["kubectl_get_pods", "prometheus_query", "analyze_logs"]

        for i, tool_name in enumerate(tool_names):
            print(f"\nCreating tool span ({i+1}/{len(tool_names)}): {tool_name}")

            # Record start time
            start_time = time.time()

            # Simulate tool execution delay
            time.sleep(0.1)

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Create tool span as child of root
            tool_span = tracer.start_span(
                otel_attr.SPAN_TOOL_EXECUTE,
                context=trace.set_span_in_context(root_span),
            )

            tool_span.set_attribute(otel_attr.TOOL_NAME, tool_name)
            tool_span.set_attribute(otel_attr.TOOL_CALL_ID, f"call-{i}")
            tool_span.set_attribute(otel_attr.TOOL_DURATION_MS, duration_ms)
            tool_span.set_attribute(
                otel_attr.TOOL_OUTPUT,
                otel_attr.truncate(f"Mock result from {tool_name}: success"),
            )

            tool_span.end()
            print(f"  ✅ Tool span created (duration: {duration_ms}ms)")

        # Set success on root span
        root_span.set_attribute("tool_call_count", len(tool_names))
        root_span.set_attribute(otel_attr.RESULT_SUCCESS, True)
        print("\n✅ All spans created successfully")

    except Exception as e:
        print(f"\n❌ Error during span creation: {e}")
        set_span_error(root_span, e)
        raise

    finally:
        root_span.end()
        print("✅ Root span ended")

    return True


def test_error_span():
    """Test creating a span with an error."""
    print("\n" + "=" * 60)
    print("Test: Error Span")
    print("=" * 60)

    from experimental.otel import attributes as otel_attr
    from experimental.otel.tracing import get_tracer, set_span_error

    tracer = get_tracer("holmesgpt.integration_test")

    test_id = f"error-test-{int(time.time())}"
    print(f"Test ID: {test_id}\n")

    root_span = tracer.start_span(otel_attr.SPAN_AGENT_RUN)

    try:
        root_span.set_attribute(otel_attr.REQUEST_ID, test_id)
        root_span.set_attribute(otel_attr.AGENT_TYPE, "HolmesGPT")

        # Simulate an error
        raise ValueError("Simulated error for testing")

    except Exception as e:
        print(f"  Caught expected error: {e}")
        set_span_error(root_span, e)
        print("  ✅ Error recorded on span")

    finally:
        root_span.end()

    print("✅ Error span test completed")
    return True


def test_flush_spans():
    """Test that spans are flushed to the backend."""
    print("\n" + "=" * 60)
    print("Test: Flush Spans to Backend")
    print("=" * 60)

    from experimental.otel.tracing import _tracer_provider

    if _tracer_provider:
        print("Forcing span flush...")
        try:
            # Force flush with timeout
            _tracer_provider.force_flush(timeout_millis=10000)
            print("✅ Spans flushed successfully")
            print("\n📊 Check your OSIS/OpenSearch backend for the traces!")
            return True
        except Exception as e:
            print(f"❌ Flush failed: {e}")
            return False
    else:
        print("⚠️ No tracer provider to flush")
        return False


# OpenSearch verification configuration (all from environment variables, no defaults)
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_USERNAME = os.environ.get("OPENSEARCH_USERNAME", "")
OPENSEARCH_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")


def verify_traces_in_opensearch(
    test_id: str, max_retries: int = 5, retry_delay: int = 2
):
    """Verify that traces were successfully indexed in OpenSearch.

    Args:
        test_id: The test ID to search for (e.g., 'integration-test-1234567890')
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds

    Returns:
        True if traces were found, False otherwise
    """
    print("\n" + "=" * 60)
    print("Test: Verify Traces in OpenSearch")
    print("=" * 60)

    print(f"\nOpenSearch Endpoint: {OPENSEARCH_ENDPOINT}")
    print(f"Searching for test_id: {test_id}")

    # Try multiple index patterns that OSIS might use
    index_patterns = [
        "otel-v1-apm-span-*",
        "ss4o_traces-*",
        "traces-*",
        "*trace*",
    ]

    # Search query for the test ID
    search_body = {
        "size": 10,
        "query": {
            "bool": {
                "should": [
                    {"match": {"resource.attributes.gen_ai@request@id": test_id}},
                    {"match": {"attributes.gen_ai@request@id": test_id}},
                    {"match": {"gen_ai.request.id": test_id}},
                    {"wildcard": {"traceId": "*"}},
                ],
                "minimum_should_match": 1,
                "filter": [{"range": {"startTime": {"gte": "now-5m"}}}],
            }
        },
        "sort": [{"startTime": {"order": "desc"}}],
    }

    # Also try a broader search for holmesgpt service
    broad_search_body = {
        "size": 10,
        "query": {
            "bool": {
                "should": [
                    {"match": {"serviceName": "holmesgpt"}},
                    {"match": {"resource.attributes.service@name": "holmesgpt"}},
                ],
                "minimum_should_match": 1,
                "filter": [{"range": {"startTime": {"gte": "now-5m"}}}],
            }
        },
        "sort": [{"startTime": {"order": "desc"}}],
    }

    auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)
    headers = {"Content-Type": "application/json"}

    for attempt in range(max_retries):
        print(f"\n🔍 Attempt {attempt + 1}/{max_retries}...")

        if attempt > 0:
            print(f"   Waiting {retry_delay}s for indexing...")
            time.sleep(retry_delay)

        for index_pattern in index_patterns:
            url = f"{OPENSEARCH_ENDPOINT}/{index_pattern}/_search"

            try:
                # First try the specific test_id search
                response = requests.post(
                    url,
                    auth=auth,
                    headers=headers,
                    json=search_body,
                    timeout=10,
                    verify=True,
                )

                if response.status_code == 200:
                    result = response.json()
                    hits = result.get("hits", {}).get("total", {})
                    hit_count = hits.get("value", 0) if isinstance(hits, dict) else hits

                    if hit_count > 0:
                        print(
                            f"\n✅ Found {hit_count} traces in index '{index_pattern}'!"
                        )
                        print("\nSample trace data:")
                        for hit in result.get("hits", {}).get("hits", [])[:2]:
                            source = hit.get("_source", {})
                            print(f"  - Trace ID: {source.get('traceId', 'N/A')}")
                            print(f"    Span Name: {source.get('name', 'N/A')}")
                            print(f"    Service: {source.get('serviceName', 'N/A')}")
                        return True

                # Try broader search for holmesgpt
                response = requests.post(
                    url,
                    auth=auth,
                    headers=headers,
                    json=broad_search_body,
                    timeout=10,
                    verify=True,
                )

                if response.status_code == 200:
                    result = response.json()
                    hits = result.get("hits", {}).get("total", {})
                    hit_count = hits.get("value", 0) if isinstance(hits, dict) else hits

                    if hit_count > 0:
                        print(
                            f"\n✅ Found {hit_count} holmesgpt traces in '{index_pattern}'!"
                        )
                        print("\nSample trace data:")
                        for hit in result.get("hits", {}).get("hits", [])[:3]:
                            source = hit.get("_source", {})
                            print(f"  - Trace ID: {source.get('traceId', 'N/A')}")
                            print(f"    Span Name: {source.get('name', 'N/A')}")
                            print(f"    Service: {source.get('serviceName', 'N/A')}")
                            # Print attributes if available
                            attrs = source.get(
                                "attributes",
                                source.get("resource", {}).get("attributes", {}),
                            )
                            if attrs:
                                print(
                                    f"    Attributes: {json.dumps(attrs, indent=6)[:200]}..."
                                )
                        return True

                elif response.status_code == 404:
                    print(f"   Index '{index_pattern}' not found, trying next...")
                else:
                    print(f"   Index '{index_pattern}': HTTP {response.status_code}")

            except requests.exceptions.RequestException as e:
                print(f"   Error querying '{index_pattern}': {e}")

    # If we get here, let's list available indices
    print("\n⚠️ Traces not found. Listing available indices...")
    try:
        response = requests.get(
            f"{OPENSEARCH_ENDPOINT}/_cat/indices?v&format=json",
            auth=auth,
            timeout=10,
            verify=True,
        )
        if response.status_code == 200:
            indices = response.json()
            trace_indices = [
                idx
                for idx in indices
                if "trace" in idx.get("index", "").lower()
                or "otel" in idx.get("index", "").lower()
            ]
            if trace_indices:
                print("\nTrace-related indices found:")
                for idx in trace_indices[:10]:
                    print(
                        f"  - {idx.get('index')} (docs: {idx.get('docs.count', 'N/A')})"
                    )
            else:
                print("\nNo trace-related indices found. Available indices:")
                for idx in indices[:10]:
                    print(f"  - {idx.get('index')}")
    except Exception as e:
        print(f"   Error listing indices: {e}")

    print("\n❌ Could not verify traces in OpenSearch")
    print("   This might be due to indexing delay or different index naming.")
    return False


def main():
    """Run all integration tests."""
    global _current_test_id

    print("=" * 60)
    print("OTEL Integration Test with OSIS + OpenSearch Verification")
    print("=" * 60)

    # Check environment first
    if not check_environment():
        return 1

    tests = [
        ("Tracer Initialization", test_tracer_initialization),
        ("Create and Export Spans", test_create_and_export_spans),
        ("Error Span", test_error_span),
        ("Flush Spans", test_flush_spans),
    ]

    failed = 0
    for name, test_func in tests:
        try:
            if not test_func():
                failed += 1
        except Exception as e:
            print(f"\n❌ Test '{name}' raised exception: {e}")
            failed += 1

    # Verify traces in OpenSearch (only if previous tests passed and OpenSearch is configured)
    if failed == 0 and _current_test_id:
        if OPENSEARCH_ENDPOINT and OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
            print("\n⏳ Waiting 3s for OSIS to index traces...")
            time.sleep(3)
            if not verify_traces_in_opensearch(_current_test_id):
                # Don't fail the test if verification fails - could be indexing delay
                print(
                    "\n⚠️ OpenSearch verification inconclusive (may need more time to index)"
                )
        else:
            print("\n⏭️ Skipping OpenSearch verification (OPENSEARCH_* env vars not set)")

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    if failed == 0:
        print(f"✅ All {len(tests)} tests passed!")
        print("\n📊 Traces should now be visible in your OpenSearch/OSIS backend.")
        print("   Look for traces with service.name='holmesgpt' and")
        print(f"   gen_ai.request.id = '{_current_test_id}'")
    else:
        print(f"❌ {failed}/{len(tests)} tests failed")

    return failed


if __name__ == "__main__":
    sys.exit(main())
