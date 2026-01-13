"""OpenTelemetry metrics initialization and instrumentation for HolmesGPT.

Follows Gen AI Semantic Conventions:
https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/
"""

import logging
import os
from typing import Optional

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from experimental.otel import attributes as otel_attr

# Module-level meter instance
_meter: Optional[metrics.Meter] = None
_meter_provider: Optional[MeterProvider] = None

# Metric instruments
_token_usage_histogram = None
_operation_duration_histogram = None
_tool_duration_histogram = None
_agent_iterations_counter = None
_tool_calls_counter = None
_error_counter = None


def _get_otel_enabled() -> bool:
    """Check if OTEL is enabled via environment variable."""
    return os.environ.get("OTEL_ENABLED", "false").lower() == "true"


def _get_metrics_enabled() -> bool:
    """Check if OTEL metrics are enabled."""
    return os.environ.get("OTEL_METRICS_ENABLED", "true").lower() == "true"


def _get_otel_endpoint() -> Optional[str]:
    """Get the OTEL exporter endpoint."""
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")


def init_otel_metrics(service_name: str = "holmesgpt") -> bool:
    """Initialize OpenTelemetry metrics.

    Args:
        service_name: The service name for the resource

    Returns:
        True if metrics were initialized, False otherwise
    """
    global _meter, _meter_provider
    global _token_usage_histogram, _operation_duration_histogram, _tool_duration_histogram
    global _agent_iterations_counter, _tool_calls_counter, _error_counter

    if not _get_otel_enabled() or not _get_metrics_enabled():
        logging.debug("OTEL metrics disabled")
        return False

    endpoint = _get_otel_endpoint()
    if not endpoint:
        logging.warning("OTEL_EXPORTER_OTLP_ENDPOINT not set, metrics disabled")
        return False

    try:
        # Create resource with service name
        resource = Resource.create({"service.name": service_name})

        # Import exporter dynamically to avoid import errors when OTEL is disabled
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        # Check if AWS SigV4 auth is needed (same as tracing)
        aws_profile = os.environ.get("OTEL_AWS_PROFILE")
        if aws_profile or ".osis." in endpoint or ".es." in endpoint:
            # Use AWS SigV4 authentication - reuse session creation from tracing
            from experimental.otel.tracing import _create_osis_session

            session = _create_osis_session(endpoint)
            if session is None:
                logging.warning("Failed to create OSIS session for metrics")
                return False
            metrics_endpoint = endpoint.replace("/v1/traces", "/v1/metrics")
            exporter = OTLPMetricExporter(
                endpoint=metrics_endpoint,
                session=session,
            )
        else:
            # Standard OTLP exporter
            metrics_endpoint = f"{endpoint.rstrip('/')}/v1/metrics"
            exporter = OTLPMetricExporter(endpoint=metrics_endpoint)

        # Create metric reader with periodic export
        metric_reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=60000,  # Export every 60 seconds
        )

        # Create and set meter provider
        _meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(_meter_provider)

        # Get meter instance
        _meter = metrics.get_meter("holmesgpt.agui", "1.0.0")

        # Create metric instruments following Gen AI semantic conventions

        # Token usage histogram
        _token_usage_histogram = _meter.create_histogram(
            name=otel_attr.METRIC_TOKEN_USAGE,
            description="Measures number of input and output tokens used",
            unit="{token}",
        )

        # Operation duration histogram
        _operation_duration_histogram = _meter.create_histogram(
            name=otel_attr.METRIC_OPERATION_DURATION,
            description="Duration of GenAI operations",
            unit="s",
        )

        # Tool execution duration histogram
        _tool_duration_histogram = _meter.create_histogram(
            name=otel_attr.METRIC_TOOL_DURATION,
            description="Duration of tool executions",
            unit="s",
        )

        # Agent iterations counter
        _agent_iterations_counter = _meter.create_counter(
            name=otel_attr.METRIC_AGENT_ITERATIONS,
            description="Number of LLM iterations per agent run",
            unit="{iteration}",
        )

        # Tool calls counter
        _tool_calls_counter = _meter.create_counter(
            name=otel_attr.METRIC_AGENT_TOOL_CALLS,
            description="Number of tool calls per agent run",
            unit="{call}",
        )

        # Error counter
        _error_counter = _meter.create_counter(
            name=otel_attr.METRIC_AGENT_ERRORS,
            description="Number of errors during agent execution",
            unit="{error}",
        )

        logging.info(f"OTEL metrics initialized with endpoint: {metrics_endpoint}")
        return True

    except Exception as e:
        logging.warning(f"Failed to initialize OTEL metrics: {e}")
        return False


def get_meter() -> Optional[metrics.Meter]:
    """Get the OTEL meter instance."""
    return _meter


def shutdown_otel_metrics() -> None:
    """Shutdown OTEL metrics and flush pending exports."""
    global _meter_provider
    if _meter_provider:
        try:
            _meter_provider.shutdown()
            logging.debug("OTEL metrics shut down successfully")
        except Exception as e:
            logging.warning(f"Error shutting down OTEL metrics: {e}")
        finally:
            _meter_provider = None


# Metric recording helper functions


def record_token_usage(
    tokens: int,
    token_type: str,
    model: str,
    operation_name: str = "chat",
) -> None:
    """Record token usage metric.

    Args:
        tokens: Number of tokens
        token_type: Either "input" or "output"
        model: Model name/ID
        operation_name: Operation type (chat, invoke_agent, etc.)
    """
    if _token_usage_histogram is None:
        return

    _token_usage_histogram.record(
        tokens,
        attributes={
            otel_attr.OPERATION_NAME: operation_name,
            otel_attr.MODEL: model,
            otel_attr.TOKEN_TYPE: token_type,
        },
    )


def record_operation_duration(
    duration_seconds: float,
    operation_name: str,
    model: str,
    agent_name: Optional[str] = None,
    success: bool = True,
) -> None:
    """Record operation duration metric.

    Args:
        duration_seconds: Duration in seconds
        operation_name: Operation type (chat, invoke_agent, execute_tool)
        model: Model name/ID
        agent_name: Agent name for agent operations
        success: Whether the operation succeeded
    """
    if _operation_duration_histogram is None:
        return

    attributes = {
        otel_attr.OPERATION_NAME: operation_name,
        otel_attr.MODEL: model,
        otel_attr.RESULT_SUCCESS: success,
    }
    if agent_name:
        attributes[otel_attr.AGENT_NAME] = agent_name

    _operation_duration_histogram.record(duration_seconds, attributes=attributes)


def record_tool_duration(
    duration_seconds: float,
    tool_name: str,
    success: bool = True,
) -> None:
    """Record tool execution duration metric.

    Args:
        duration_seconds: Duration in seconds
        tool_name: Name of the tool
        success: Whether the tool execution succeeded
    """
    if _tool_duration_histogram is None:
        return

    _tool_duration_histogram.record(
        duration_seconds,
        attributes={
            otel_attr.TOOL_NAME: tool_name,
            otel_attr.TOOL_STATUS: "SUCCESS" if success else "FAILURE",
        },
    )


def increment_iterations(
    count: int = 1,
    model: str = "unknown",
    agent_name: str = "HolmesGPT",
) -> None:
    """Increment the agent iterations counter.

    Args:
        count: Number of iterations to add
        model: Model name/ID
        agent_name: Agent name
    """
    if _agent_iterations_counter is None:
        return

    _agent_iterations_counter.add(
        count,
        attributes={
            otel_attr.MODEL: model,
            otel_attr.AGENT_NAME: agent_name,
        },
    )


def increment_tool_calls(
    count: int = 1,
    tool_name: str = "unknown",
    model: str = "unknown",
) -> None:
    """Increment the tool calls counter.

    Args:
        count: Number of tool calls to add
        tool_name: Name of the tool
        model: Model name/ID
    """
    if _tool_calls_counter is None:
        return

    _tool_calls_counter.add(
        count,
        attributes={
            otel_attr.TOOL_NAME: tool_name,
            otel_attr.MODEL: model,
        },
    )


def increment_errors(
    count: int = 1,
    error_type: str = "unknown",
    operation_name: str = "invoke_agent",
) -> None:
    """Increment the error counter.

    Args:
        count: Number of errors to add
        error_type: Type/category of error
        operation_name: Operation where error occurred
    """
    if _error_counter is None:
        return

    _error_counter.add(
        count,
        attributes={
            otel_attr.ERROR_TYPE: error_type,
            otel_attr.OPERATION_NAME: operation_name,
        },
    )
