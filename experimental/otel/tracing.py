"""OpenTelemetry tracer initialization and management.

Based on patterns from ml-commons AgentTracer.java
"""

import atexit
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import hashlib

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Tracer, Status, StatusCode

# Global tracer provider
_tracer_provider: Optional[TracerProvider] = None
_initialized = False


def _get_otel_enabled() -> bool:
    """Read OTEL_ENABLED at call time, not import time."""
    return os.environ.get("OTEL_ENABLED", "false").lower() == "true"


def _get_otel_service_name() -> str:
    """Read OTEL_SERVICE_NAME at call time."""
    return os.environ.get("OTEL_SERVICE_NAME", "holmesgpt")


def _get_otel_endpoint() -> str:
    """Read OTEL_EXPORTER_OTLP_ENDPOINT at call time."""
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")


def _get_otel_aws_profile() -> Optional[str]:
    """Read OTEL_AWS_PROFILE at call time.

    If set, uses this AWS profile for OSIS authentication instead of the default.
    This allows using different credentials for OSIS vs other AWS services (e.g., Bedrock).
    """
    return os.environ.get("OTEL_AWS_PROFILE")


def _get_otel_aws_region() -> Optional[str]:
    """Read OTEL_AWS_REGION at call time.

    If not set, tries to extract region from the OSIS endpoint URL.
    """
    return os.environ.get("OTEL_AWS_REGION")


def _get_otel_aws_service() -> str:
    """Read OTEL_AWS_SERVICE at call time.

    Default is 'osis'. Try 'osis-pipelines' or 'es' if auth fails.
    """
    return os.environ.get("OTEL_AWS_SERVICE", "osis")


def _extract_region_from_endpoint(endpoint: str) -> str:
    """Extract AWS region from OSIS endpoint URL.

    Example: https://xxx.us-east-1.osis.amazonaws.com/... -> us-east-1
    """
    try:
        parsed = urlparse(endpoint)
        if not parsed.hostname:
            logging.warning(
                f"[OTEL] Could not parse hostname from endpoint: {endpoint}"
            )
            return "us-east-1"
        host_parts = parsed.hostname.split(".")
        # OSIS endpoints look like: xxx.<region>.osis.amazonaws.com
        for i, part in enumerate(host_parts):
            if part == "osis" and i > 0:
                return host_parts[i - 1]
    except Exception:
        pass
    return "us-east-1"  # Default fallback


class AWSSigV4Session(requests.Session):
    """A requests Session that signs all requests with AWS SigV4."""

    def __init__(self, boto_session: boto3.Session, region: str, service: str):
        super().__init__()
        self._boto_session = boto_session
        self._region = region
        self._service = service

    def request(self, method, url, **kwargs):
        # Get fresh credentials (handles refresh for assumed roles)
        credentials = self._boto_session.get_credentials()
        if not credentials:
            raise ValueError("No AWS credentials available")

        frozen_credentials = credentials.get_frozen_credentials()

        # Separate Request kwargs from send kwargs
        # These are passed to Session.send(), not Request()
        send_kwargs = {}
        for key in [
            "timeout",
            "verify",
            "cert",
            "proxies",
            "allow_redirects",
            "stream",
        ]:
            if key in kwargs:
                send_kwargs[key] = kwargs.pop(key)

        # Get the body data for content hash calculation
        data = kwargs.get("data", b"")
        if data is None:
            data = b""

        # Calculate content SHA256 hash (required for SigV4)
        content_sha256 = hashlib.sha256(
            data if isinstance(data, bytes) else data.encode()
        ).hexdigest()

        # Build minimal headers for signing (matching awscurl behavior)
        # Only include Content-Type from original headers, let SigV4 add the rest
        headers = kwargs.get("headers", {})
        minimal_headers = {
            "Content-Type": headers.get("Content-Type", "application/x-protobuf"),
            "x-amz-content-sha256": content_sha256,
        }

        # Debug: show what we're about to sign
        logging.debug(f"[OTEL SIGN] Method: {method}, URL: {url}")
        logging.debug(f"[OTEL SIGN] Body length: {len(data)}")
        logging.debug(f"[OTEL SIGN] Content SHA256: {content_sha256}")

        # Create AWSRequest for signing with minimal headers
        aws_request = AWSRequest(
            method=method,
            url=url,
            headers=minimal_headers,
            data=data,
        )

        # Sign the request using botocore
        SigV4Auth(frozen_credentials, self._service, self._region).add_auth(aws_request)

        # Debug: show signed headers
        logging.debug(f"[OTEL SIGN] Headers after signing: {dict(aws_request.headers)}")

        # Now make the actual request with signed headers
        kwargs["headers"] = dict(aws_request.headers)
        kwargs["data"] = data

        # Use parent's request method to actually send
        return super().request(method, url, **kwargs, **send_kwargs)


def _create_osis_session(endpoint: str) -> Optional[requests.Session]:
    """Create a requests session with AWS SigV4 auth for OSIS.

    Uses OTEL_AWS_PROFILE if set, otherwise uses default credentials.
    This allows OSIS to use different credentials than other AWS services.
    """
    try:
        otel_profile = _get_otel_aws_profile()
        otel_region = _get_otel_aws_region() or _extract_region_from_endpoint(endpoint)
        otel_service = _get_otel_aws_service()

        # Create boto3 session with specific profile or default
        # IMPORTANT: We must temporarily clear ALL AWS credential env vars to ensure
        # boto3 uses the specified profile, not env vars from another profile (e.g., Bedrock)
        #
        # WARNING: This env var manipulation is not thread-safe. This function
        # should only be called during application startup, before spawning threads.
        # If called concurrently, race conditions may occur with other code reading
        # AWS environment variables.
        aws_env_vars = [
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ]

        if otel_profile:
            logging.debug(
                f"[OTEL] Using AWS profile '{otel_profile}' for OSIS (clearing AWS env vars temporarily)"
            )

            # Save and clear all AWS env vars
            saved_env = {}
            for var in aws_env_vars:
                if var in os.environ:
                    saved_env[var] = os.environ.pop(var)

            try:
                boto_session = boto3.Session(profile_name=otel_profile)
            finally:
                # Restore all saved env vars
                os.environ.update(saved_env)
        else:
            logging.debug("[OTEL] Using default AWS credentials for OSIS")
            boto_session = boto3.Session()

        credentials = boto_session.get_credentials()
        if not credentials:
            logging.warning("No AWS credentials found for OSIS")
            return None

        # Debug: show credential info (masked for security)
        frozen_credentials = credentials.get_frozen_credentials()
        has_token = frozen_credentials.token is not None
        logging.debug(f"[OTEL] Has Session Token: {has_token}")
        logging.debug(f"[OTEL] Region for signing: {otel_region}")
        logging.debug(f"[OTEL] Service for signing: {otel_service}")

        # Create custom session that signs all requests with SigV4
        session = AWSSigV4Session(boto_session, otel_region, otel_service)

        logging.debug("[OTEL] Created OSIS SigV4 session successfully")
        return session

    except Exception as e:
        logging.error(f"Failed to create OSIS session: {e}", exc_info=True)
        return None


def init_otel_tracer() -> bool:
    """Initialize OpenTelemetry tracer with OTLP HTTP exporter.

    Based on ml-commons AgentTracer.initTracer() pattern.

    Returns:
        True if initialization succeeded, False otherwise
    """
    global _tracer_provider, _initialized

    if _initialized:
        return True

    # Read env vars at call time, not import time
    otel_enabled = _get_otel_enabled()
    otel_endpoint = _get_otel_endpoint()
    otel_service_name = _get_otel_service_name()

    # Debug output to see what's happening
    logging.debug(
        f"[OTEL] OTEL_ENABLED={otel_enabled}, OTEL_EXPORTER_OTLP_ENDPOINT={otel_endpoint}"
    )

    if not otel_enabled:
        logging.info("OTEL tracing disabled (OTEL_ENABLED=false)")
        _initialized = True
        return False

    if not otel_endpoint:
        logging.warning("OTEL tracing disabled: OTEL_EXPORTER_OTLP_ENDPOINT not set")
        _initialized = True
        return False

    try:
        # Create resource with service name
        resource = Resource.create(
            {
                "service.name": otel_service_name,
                "service.version": _get_version(),
            }
        )

        # Create OTLP HTTP exporter with AWS SigV4 auth for OSIS
        # Endpoint should be like: https://your-osis-pipeline/v1/traces
        osis_session = _create_osis_session(otel_endpoint)
        if osis_session:
            exporter = OTLPSpanExporter(endpoint=otel_endpoint, session=osis_session)
        else:
            # Fall back to unauthenticated exporter (may fail with OSIS)
            logging.warning(
                "OSIS session creation failed, using unauthenticated exporter"
            )
            exporter = OTLPSpanExporter(endpoint=otel_endpoint)

        # Create tracer provider
        _tracer_provider = TracerProvider(resource=resource)

        # Add batch processor with reduced batch size to prevent payload too large errors
        # Based on ml-commons pattern: max_export_batch_size=32
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=32,  # Reduced to prevent payload too large
            schedule_delay_millis=1000,
        )
        _tracer_provider.add_span_processor(processor)

        # Set as global tracer provider
        trace.set_tracer_provider(_tracer_provider)

        # Register shutdown handler to flush remaining spans
        atexit.register(shutdown_otel_tracer)

        _initialized = True
        logging.info(
            f"OTEL tracer initialized: service={otel_service_name}, endpoint={otel_endpoint}"
        )
        return True

    except Exception as e:
        logging.error(f"Failed to initialize OTEL tracer: {e}", exc_info=True)
        _initialized = True
        return False


def get_tracer(name: str = "holmesgpt") -> Tracer:
    """Get a tracer instance.

    If OTEL is not enabled or initialization failed, returns a no-op tracer.

    Args:
        name: The tracer name (typically module name)

    Returns:
        A tracer instance
    """
    if not _initialized:
        init_otel_tracer()

    return trace.get_tracer(name)


def shutdown_otel_tracer() -> None:
    """Shutdown the tracer provider and flush remaining spans."""
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
        logging.info("OTEL tracer shutdown complete")


def set_span_error(span: trace.Span, error: Exception) -> None:
    """Set error status and attributes on a span.

    Args:
        span: The span to set error on
        error: The exception that occurred
    """
    span.set_status(Status(StatusCode.ERROR, str(error)))
    span.set_attribute("error.type", type(error).__name__)
    span.set_attribute("error.message", str(error))


def _get_version() -> str:
    """Get HolmesGPT version."""
    try:
        from holmes import get_version

        return get_version()
    except Exception:
        return "unknown"
