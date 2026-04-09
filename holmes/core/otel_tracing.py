"""OpenTelemetry tracing and metrics implementation for HolmesGPT.

Provides the ``OpenTelemetryTracer``, ``OTelSpan`` and ``OTelMetrics`` classes
that plug into Holmes' tracing abstraction layer.  When the
``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable is set the factory in
:mod:`holmes.core.tracing` automatically selects this module; otherwise a
zero-overhead ``DummyTracer`` is used.

Naming convention
-----------------
OTel **span attributes** follow the upstream semantic conventions and use
dot-delimited names (e.g. ``gen_ai.system``).

OTel **metric dimension keys** intentionally use underscore-delimited names
(e.g. ``gen_ai_system``) for maximum compatibility across backends such as
Dynatrace, Grafana and Prometheus which normalise dots to underscores.

Two sets of constants are provided below to keep the distinction explicit.
"""

import logging
import os
from typing import Any, Dict, Optional

from holmes.core.tracing import DummySpan, SpanType, TracingFactory

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import View
    from opentelemetry.trace import StatusCode

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OTel GenAI semantic convention — span attribute names (dot-delimited)
# Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
# ---------------------------------------------------------------------------
ATTR_GEN_AI_SYSTEM = "gen_ai.system"
ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
ATTR_GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
ATTR_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"

# ---------------------------------------------------------------------------
# Metric dimension keys — underscore-delimited for backend compatibility
# These are used as attribute keys on OTel metric data points and in View
# definitions.  Many backends (Dynatrace, Prometheus, Grafana) normalise dots
# to underscores in metric dimensions, so we use underscores from the start.
# ---------------------------------------------------------------------------
DIM_GEN_AI_SYSTEM = "gen_ai_system"
DIM_GEN_AI_REQUEST_MODEL = "gen_ai_request_model"
DIM_GEN_AI_TOKEN_TYPE = "gen_ai_token_type"
DIM_TOOL_NAME = "holmesgpt_tool_name"

# Backward-compatible aliases (deprecated — prefer ATTR_* / DIM_* above)
GEN_AI_SYSTEM = DIM_GEN_AI_SYSTEM
GEN_AI_REQUEST_MODEL = DIM_GEN_AI_REQUEST_MODEL
GEN_AI_RESPONSE_MODEL = ATTR_GEN_AI_RESPONSE_MODEL
GEN_AI_REQUEST_TEMPERATURE = ATTR_GEN_AI_REQUEST_TEMPERATURE
GEN_AI_USAGE_INPUT_TOKENS = ATTR_GEN_AI_USAGE_INPUT_TOKENS
GEN_AI_USAGE_OUTPUT_TOKENS = ATTR_GEN_AI_USAGE_OUTPUT_TOKENS
GEN_AI_USAGE_TOTAL_TOKENS = ATTR_GEN_AI_USAGE_TOTAL_TOKENS


class OTelMetrics:
    """Container for all HolmesGPT OTel metric instruments.

    Instantiated once by :class:`OpenTelemetryTracer` and registered via
    :meth:`TracingFactory.set_metrics` so that any module can record metrics
    without holding a direct reference to the meter.
    """

    def __init__(self, meter: Any):
        """Initialise all metric instruments from the given OTel *meter*.

        Args:
            meter: An ``opentelemetry.metrics.Meter`` instance used to create
                counters and histograms for LLM, investigation and tool metrics.
        """
        # Token counters
        self.token_usage = meter.create_counter(
            name="gen_ai.client.token.usage",
            description="Number of input/output tokens used by LLM calls",
            unit="{token}",
        )

        # Investigation metrics
        self.investigation_duration = meter.create_histogram(
            name="holmesgpt.investigation.duration",
            description="Duration of investigations in seconds",
            unit="s",
        )
        self.investigation_count = meter.create_counter(
            name="holmesgpt.investigation.count",
            description="Number of investigations started",
            unit="{investigation}",
        )
        self.investigation_iterations = meter.create_histogram(
            name="holmesgpt.investigation.iterations",
            description="Number of LLM iterations per investigation",
            unit="{iteration}",
        )

        # LLM call metrics
        self.llm_call_duration = meter.create_histogram(
            name="gen_ai.client.operation.duration",
            description="Duration of individual LLM calls in seconds",
            unit="s",
        )

        # Tool/MCP metrics
        self.tool_call_count = meter.create_counter(
            name="holmesgpt.tool.call.count",
            description="Number of tool/MCP calls",
            unit="{call}",
        )
        self.tool_call_duration = meter.create_histogram(
            name="holmesgpt.tool.call.duration",
            description="Duration of tool/MCP calls in seconds",
            unit="s",
        )
        self.tool_call_errors = meter.create_counter(
            name="holmesgpt.tool.call.errors",
            description="Number of tool/MCP call errors",
            unit="{error}",
        )


# Metrics are registered via TracingFactory.set_metrics()


class OTelSpan:
    """Wraps an OTel span to match Holmes' span interface.

    Key design: every OTelSpan **activates** its underlying span in the
    current OTel context so that auto-instrumented libraries (httpx, etc.)
    automatically create child spans under it.
    """

    def __init__(self, otel_span: Any, tracer: Any, token: Any = None):
        """Wrap an OpenTelemetry span with Holmes-compatible lifecycle management.

        Args:
            otel_span: The underlying ``opentelemetry.trace.Span``.
            tracer: The ``opentelemetry.trace.Tracer`` used to create child spans.
            token: Context token from ``otel_context.attach()``; stored so the
                context can be detached when the span ends.
        """
        self._span = otel_span
        self._tracer = tracer
        # Token from context.attach() — needed to detach on end/exit
        self._token = token

    def start_span(self, name: Optional[str] = None, span_type: Optional[SpanType] = None, **kwargs) -> "OTelSpan":
        """Create a child span and activate it in the current context."""
        span_name = name or kwargs.get("type", "unknown")
        if span_type and not name:
            span_name = span_type.value

        # Parent context is the current context (which has self._span active)
        ctx = trace.set_span_in_context(self._span)
        new_span = self._tracer.start_span(span_name, context=ctx)

        # Activate the child span so httpx/other auto-instrumented calls
        # made while this span is alive become its children
        new_ctx = trace.set_span_in_context(new_span)
        token = otel_context.attach(new_ctx)

        return OTelSpan(new_span, self._tracer, token)

    def log(self, *args: Any, **kwargs: Any) -> None:
        """Log attributes to the span.

        Supported keyword arguments:
            input: Stored as the ``input`` span attribute (truncated to 4096 chars).
            output: Stored as the ``output`` span attribute (truncated to 4096 chars).
            metadata: A ``dict`` whose entries are set as individual span attributes.
        """
        if "input" in kwargs:
            val = str(kwargs["input"])
            self._span.set_attribute("input", val[:4096])
        if "output" in kwargs:
            val = str(kwargs["output"])
            self._span.set_attribute("output", val[:4096])
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            for k, v in kwargs["metadata"].items():
                if isinstance(v, (str, int, float, bool)):
                    self._span.set_attribute(k, v)
                else:
                    self._span.set_attribute(k, str(v))

    def end(self) -> None:
        """End the span and detach from context."""
        self._safe_detach()
        self._span.end()

    def _safe_detach(self) -> None:
        """Detach context token, tolerating cross-context calls (generators/threads)."""
        if self._token is not None:
            try:
                otel_context.detach(self._token)
            except ValueError:
                # Token created in a different context (e.g., streaming generator
                # yielding across thread/coroutine boundaries). This is expected
                # for long-lived spans that wrap generators. The span still exports
                # correctly; we just can't restore the previous context.
                logger.debug("Context detach skipped (cross-context span lifecycle)")
            self._token = None

    def set_attributes(self, name: Optional[str] = None, span_type: Optional[str] = None, span_attributes: Optional[Dict[str, Any]] = None) -> None:
        """Update the span's name and/or set additional attributes.

        Args:
            name: If provided, updates the span display name.
            span_type: Unused; kept for interface compatibility.
            span_attributes: Key/value pairs to set on the span.
        """
        if name:
            self._span.update_name(name)
        if span_attributes:
            for k, v in span_attributes.items():
                if isinstance(v, (str, int, float, bool)):
                    self._span.set_attribute(k, v)
                else:
                    self._span.set_attribute(k, str(v))

    def __enter__(self) -> "OTelSpan":
        """Enter the span context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the span context manager, recording errors and ending the span."""
        if exc_type and OTEL_AVAILABLE:
            self._span.set_status(StatusCode.ERROR, str(exc_val))
        self._safe_detach()
        self._span.end()


class OpenTelemetryTracer:
    """OpenTelemetry implementation of Holmes tracing.

    Configures a :class:`TracerProvider` and :class:`MeterProvider` with OTLP
    gRPC exporters, creates metric instruments, and optionally auto-instruments
    ``httpx`` for W3C trace-context propagation to MCP servers.
    """

    def __init__(self, service_name: str = "holmesgpt"):
        """Set up OTel trace and metric providers from environment variables.

        Args:
            service_name: The ``service.name`` resource attribute reported in
                all exported spans and metrics.

        Raises:
            ImportError: If the OpenTelemetry SDK packages are not installed.

        Environment variables read:
            ``OTEL_EXPORTER_OTLP_ENDPOINT``, ``OTEL_EXPORTER_OTLP_HEADERS``,
            ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT``, ``OTEL_SERVICE_NAME``.
        """
        if not OTEL_AVAILABLE:
            raise ImportError(
                "opentelemetry packages required. Install with: pip install 'holmesgpt[otel]'"
            )

        resource = Resource.create({"service.name": service_name})

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        headers_str = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
        headers = _parse_otel_headers(headers_str)
        insecure = not endpoint.startswith("https://")

        # --- Traces ---
        trace_provider = TracerProvider(resource=resource)
        trace_exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=insecure,
            headers=headers or None,
        )
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)
        self._tracer = trace.get_tracer("holmesgpt", "0.1.0")
        self._provider = trace_provider

        # --- Metrics ---
        metrics_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", endpoint)
        logger.info("OTel metrics exporter endpoint: %s (traces: %s)", metrics_endpoint, endpoint)
        metric_exporter = OTLPMetricExporter(
            endpoint=metrics_endpoint,
            insecure=insecure,
            headers=headers or None,
        )
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=30000
        )
        # Define views to ensure attribute keys are preserved as dimensions
        views = [
            View(
                instrument_name="holmesgpt.tool.call.count",
                attribute_keys=[DIM_TOOL_NAME],
            ),
            View(
                instrument_name="holmesgpt.tool.call.duration",
                attribute_keys=[DIM_TOOL_NAME],
            ),
            View(
                instrument_name="holmesgpt.tool.call.errors",
                attribute_keys=[DIM_TOOL_NAME],
            ),
            View(
                instrument_name="gen_ai.client.token.usage",
                attribute_keys=[DIM_GEN_AI_REQUEST_MODEL, DIM_GEN_AI_SYSTEM, DIM_GEN_AI_TOKEN_TYPE],
            ),
            View(
                instrument_name="gen_ai.client.operation.duration",
                attribute_keys=[DIM_GEN_AI_REQUEST_MODEL, DIM_GEN_AI_SYSTEM],
            ),
            View(
                instrument_name="holmesgpt.investigation.count",
                attribute_keys=[DIM_GEN_AI_REQUEST_MODEL],
            ),
            View(
                instrument_name="holmesgpt.investigation.duration",
                attribute_keys=[DIM_GEN_AI_REQUEST_MODEL],
            ),
            View(
                instrument_name="holmesgpt.investigation.iterations",
                attribute_keys=[DIM_GEN_AI_REQUEST_MODEL],
            ),
        ]
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader], views=views)
        metrics.set_meter_provider(meter_provider)
        self._meter_provider = meter_provider
        meter = metrics.get_meter("holmesgpt", "0.1.0")
        TracingFactory.set_metrics(OTelMetrics(meter))
        logger.info("OTel metrics initialized with %d views, export interval=30s", len(views))

        # Auto-instrument httpx for MCP trace context propagation.
        # Must happen AFTER set_tracer_provider so httpx spans use our provider.
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            logger.info("httpx auto-instrumented for MCP trace context propagation")
        except ImportError:
            logger.warning(
                "opentelemetry-instrumentation-httpx not installed; MCP HTTP calls won't propagate trace context"
            )

    def start_experiment(self, experiment_name: Optional[str] = None, additional_metadata: Optional[dict] = None) -> None:
        """No-op — experiments are a Braintrust concept not used by OTel."""
        return None

    def start_trace(self, name: str, span_type: Optional[SpanType] = None) -> OTelSpan:
        """Start a root trace span and activate it in the current context.

        The span is attached to the OTel context so that any auto-instrumented
        calls (httpx, etc.) made while this span is alive become its children.
        """
        span = self._tracer.start_span(name)
        # Activate the root span in context
        ctx = trace.set_span_in_context(span)
        token = otel_context.attach(ctx)
        return OTelSpan(span, self._tracer, token)

    def get_trace_url(self) -> Optional[str]:
        """Return a URL to view the trace.  Not applicable for generic OTLP export."""
        return None

    def wrap_llm(self, llm_module: Any) -> Any:
        """Return the LLM module unchanged — OTel uses explicit instrumentation."""
        return llm_module

    def shutdown(self) -> None:
        """Flush pending spans/metrics and shut down both providers."""
        self._provider.shutdown()
        self._meter_provider.shutdown()


def _parse_otel_headers(headers_str: str) -> Dict[str, str]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS format: 'key1=value1,key2=value2'."""
    if not headers_str:
        return {}
    headers = {}
    for pair in headers_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            headers[key.strip()] = value.strip()
    return headers
