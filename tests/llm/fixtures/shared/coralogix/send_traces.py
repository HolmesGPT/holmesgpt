#!/usr/bin/env python3
"""
Send test traces to Coralogix via OTLP HTTP.

Usage:
    python send_traces.py --domain eu2.coralogix.com --api-key <key> \
        --app-name holmes-eval-174 --subsystem traces-test \
        --trace-id TRACE-ABC123 --error-code ERR-5847

Environment variables (alternative to CLI args):
    Domain defaults to eu2.coralogix.com
    CORALOGIX_SEND_API_KEY - API key with SendData permissions (for ingestion)
    SSL_VERIFY - Set to 'false' to disable SSL verification

Note: Coralogix uses separate API keys for sending vs querying data.
See: https://coralogix.com/docs/user-guides/account-management/api-keys/api-keys/
"""

import argparse
import os
import sys
import time

import urllib3
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode


def send_traces(domain: str, api_key: str, app_name: str, subsystem: str,
                trace_id: str, error_code: str) -> bool:
    """Send test traces to Coralogix via OTLP HTTP."""
    # Check SSL verification setting
    ssl_verify = os.environ.get("SSL_VERIFY", "true").lower() not in ("false", "0", "no")
    if not ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        os.environ["OTEL_EXPORTER_OTLP_INSECURE"] = "true"
        print("   Note: SSL verification disabled")

    # Configure resource with Coralogix-specific attributes
    resource = Resource.create({
        "service.name": app_name,
        "cx.application.name": app_name,
        "cx.subsystem.name": subsystem,
    })

    # Configure OTLP HTTP exporter for Coralogix
    endpoint = f"https://ingress.{domain}/v1/traces"
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "CX-Application-Name": app_name,
            "CX-Subsystem-Name": subsystem,
        },
    )

    # Set up tracer provider
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)

    print(f"Sending traces to {endpoint}...")

    # Create a realistic trace with multiple spans
    with tracer.start_as_current_span("http_request") as root_span:
        root_span.set_attribute("http.method", "POST")
        root_span.set_attribute("http.url", "/api/checkout")
        root_span.set_attribute("trace.id.custom", trace_id)

        # Simulate database query
        with tracer.start_as_current_span("db_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "SELECT")
            db_span.set_attribute("db.statement", "SELECT * FROM orders WHERE id = ?")
            time.sleep(0.05)

        # Simulate external API call that fails
        with tracer.start_as_current_span("external_api_call") as api_span:
            api_span.set_attribute("http.url", "https://payment-gateway.example.com/charge")
            api_span.set_attribute("error.code", error_code)
            api_span.set_attribute("error.message", f"Payment gateway timeout - {error_code}")
            api_span.set_status(Status(StatusCode.ERROR, f"Payment failed: {error_code}"))
            time.sleep(0.02)

        # Mark root span as error due to child failure
        root_span.set_status(Status(StatusCode.ERROR, "Request failed"))
        root_span.set_attribute("error", True)

    # Force flush to ensure spans are sent
    flush_success = provider.force_flush()
    if not flush_success:
        print("❌ FAILURE: provider.force_flush() returned False - spans may not have been exported")
        provider.shutdown()
        return False

    provider.shutdown()

    print(f"✅ Traces sent successfully with trace_id={trace_id}, error_code={error_code}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Send test traces to Coralogix")
    parser.add_argument("--domain", default=os.environ.get("CORALOGIX_DOMAIN", "eu2.coralogix.com"),
                        help="Coralogix domain (e.g., eu2.coralogix.com)")
    parser.add_argument("--api-key", default=os.environ.get("CORALOGIX_SEND_API_KEY"),
                        help="Coralogix Send-Your-Data API key (SendData permissions)")
    parser.add_argument("--app-name", required=True, help="Application name")
    parser.add_argument("--subsystem", required=True, help="Subsystem name")
    parser.add_argument("--trace-id", required=True, help="Custom trace ID for verification")
    parser.add_argument("--error-code", required=True, help="Error code to inject")

    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key or CORALOGIX_SEND_API_KEY required")
        sys.exit(1)

    success = send_traces(
        domain=args.domain,
        api_key=args.api_key,
        app_name=args.app_name,
        subsystem=args.subsystem,
        trace_id=args.trace_id,
        error_code=args.error_code,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
