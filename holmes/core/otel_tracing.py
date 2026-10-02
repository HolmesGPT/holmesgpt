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

import json
import logging
import os
from typing import Any, Dict, Optional

from holmes.core.tracing import (
    HOLMES_LANGFUSE_ATTRIBUTES,
    DummySpan,
    SpanType,
    TracingFactory,
)

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry import metrics
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import View
    from opentelemetry.trace import StatusCode
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_ON,
        Decision,
        Sampler,
        SamplingResult,
    )

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

# OTLP exporters — gRPC and HTTP variants ship as separate packages, so each
# is imported independently and selected at runtime based on
# OTEL_EXPORTER_OTLP_PROTOCOL (see _create_exporters).
try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as GRPCSpanExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter as GRPCMetricExporter,
    )

    GRPC_EXPORTER_AVAILABLE = True
except ImportError:
    GRPC_EXPORTER_AVAILABLE = False

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HTTPSpanExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HTTPMetricExporter,
    )

    HTTP_EXPORTER_AVAILABLE = True
except ImportError:
    HTTP_EXPORTER_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default OTLP endpoints per protocol (OTel spec: gRPC uses 4317, HTTP uses 4318)
DEFAULT_GRPC_ENDPOINT = "http://localhost:4317"
DEFAULT_HTTP_ENDPOINT = "http://localhost:4318"

# Max chars for input/output span attributes; bounded for OTLP limits. Override via env.
_MAX_ATTR_CHARS = int(os.environ.get("HOLMES_OTEL_MAX_ATTR_CHARS", 100000))


def _to_attr_str(value: Any) -> str:
    """Render a value as a bounded string: verbatim if str, else JSON, truncated."""
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(value)
    return rendered[:_MAX_ATTR_CHARS]


# HTTP method names used by the httpx auto-instrumentation as span names.
_HTTP_METHOD_SPAN_NAMES = {
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE",
}

if OTEL_AVAILABLE:

    class _DropOrphanHttpSampler(Sampler):
        """Drop root httpx spans (background HTTP calls) that would bury real investigation traces.

        Only drops spans that are both a root (no parent) and named like a bare
        HTTP method; httpx spans nested in an investigation are kept.
        """

        def __init__(self, delegate: "Sampler" = ALWAYS_ON):
            self._delegate = delegate

        def should_sample(
            self, parent_context, trace_id, name, kind=None,
            attributes=None, links=None, trace_state=None,
        ) -> "SamplingResult":
            parent_span = trace.get_current_span(parent_context)
            parent_ctx = parent_span.get_span_context() if parent_span else None
            is_root = not (parent_ctx and parent_ctx.is_valid)
            if is_root and name in _HTTP_METHOD_SPAN_NAMES:
                return SamplingResult(Decision.DROP, attributes, trace_state)
            return self._delegate.should_sample(
                parent_context, trace_id, name, kind, attributes, links, trace_state
            )

        def get_description(self) -> str:
            return f"DropOrphanHttp({self._delegate.get_description()})"

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

    # Braintrust-style metric names → OTel GenAI semantic convention attributes
    _METRIC_ATTR_MAP = {
        "prompt_tokens": ATTR_GEN_AI_USAGE_INPUT_TOKENS,
        "completion_tokens": ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
        "total_tokens": ATTR_GEN_AI_USAGE_TOTAL_TOKENS,
    }

    def log(self, *args: Any, **kwargs: Any) -> None:
        """Set span attributes from input/output/error (Langfuse, gated), metadata, and metrics."""
        # Langfuse-specific input/output/error; gated (off by default).
        if HOLMES_LANGFUSE_ATTRIBUTES:
            if "input" in kwargs:
                self._span.set_attribute(
                    "langfuse.observation.input", _to_attr_str(kwargs["input"])
                )
            if "output" in kwargs:
                self._span.set_attribute(
                    "langfuse.observation.output", _to_attr_str(kwargs["output"])
                )
            if kwargs.get("error"):
                self._span.set_attribute("langfuse.observation.level", "ERROR")
                self._span.set_attribute(
                    "langfuse.observation.status_message", _to_attr_str(kwargs["error"])
                )
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            for k, v in kwargs["metadata"].items():
                if isinstance(v, (int, float, bool)):
                    self._span.set_attribute(k, v)
                elif isinstance(v, str):
                    # cap free-text metadata
                    self._span.set_attribute(k, v[:_MAX_ATTR_CHARS])
                elif isinstance(v, (list, tuple)) and all(
                    isinstance(e, str) for e in v
                ):
                    # string-array attribute (e.g. tags)
                    self._span.set_attribute(k, list(v))
                else:
                    self._span.set_attribute(k, str(v)[:_MAX_ATTR_CHARS])
        if "metrics" in kwargs and isinstance(kwargs["metrics"], dict):
            for k, v in kwargs["metrics"].items():
                if isinstance(v, (int, float)):
                    self._span.set_attribute(self._METRIC_ATTR_MAP.get(k, k), v)

    def end(self) -> None:
        """End the span and detach from context."""
        self._safe_detach()
        self._span.end()

    def _safe_detach(self) -> None:
        """Detach our context token only when in LIFO order, else skip (ROB-278)."""
        if self._token is None:
            return
        token, self._token = self._token, None
        # Out-of-order detach makes OTel log "Failed to detach context" (it swallows
        # the error, so try/except can't help); only detach when our span is current.
        if trace.get_current_span() is self._span:
            otel_context.detach(token)
        else:
            logger.debug("Context detach skipped (cross-context span lifecycle)")

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
    exporters (gRPC by default, or HTTP/protobuf when
    ``OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf``), creates metric instruments,
    and optionally auto-instruments ``httpx`` for W3C trace-context propagation
    to MCP servers.
    """

    def __init__(self, service_name: str = "holmesgpt"):
        """Set up OTel trace and metric providers from environment variables.

        Args:
            service_name: The ``service.name`` resource attribute reported in
                all exported spans and metrics.

        Raises:
            ImportError: If the OpenTelemetry SDK packages are not installed.

        Environment variables read:
            ``OTEL_EXPORTER_OTLP_ENDPOINT``, ``OTEL_EXPORTER_OTLP_PROTOCOL``,
            ``OTEL_EXPORTER_OTLP_HEADERS``,
            ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT``, ``OTEL_SERVICE_NAME``.
        """
        if not OTEL_AVAILABLE:
            raise ImportError(
                "opentelemetry packages required. Install with: pip install 'holmesgpt[otel]'"
            )

        resource = Resource.create({"service.name": service_name})

        protocol = _get_otlp_protocol()
        default_endpoint = (
            DEFAULT_HTTP_ENDPOINT if protocol == "http/protobuf" else DEFAULT_GRPC_ENDPOINT
        )
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", default_endpoint)
        metrics_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
        headers_str = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
        headers = _parse_otel_headers(headers_str)

        trace_exporter, metric_exporter = _create_exporters(
            protocol=protocol,
            endpoint=endpoint,
            metrics_endpoint=metrics_endpoint,
            headers=headers,
        )

        # --- Traces ---
        # Drop orphan httpx root spans so they don't bury real investigation traces.
        trace_provider = TracerProvider(
            resource=resource, sampler=_DropOrphanHttpSampler()
        )
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)
        self._tracer = trace.get_tracer("holmesgpt", "0.1.0")
        self._provider = trace_provider

        # --- Metrics ---
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


def _get_otlp_protocol() -> str:
    """Read and validate ``OTEL_EXPORTER_OTLP_PROTOCOL``.

    Returns:
        The normalized protocol: ``"grpc"`` (default) or ``"http/protobuf"``.

    Raises:
        ValueError: If the env var is set to an unsupported value
            (e.g. ``http/json``, which the Python OTLP exporters don't implement).
    """
    protocol = (os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL") or "grpc").strip().lower()
    if protocol not in ("grpc", "http/protobuf"):
        raise ValueError(
            f"Unsupported OTEL_EXPORTER_OTLP_PROTOCOL: {protocol!r}. "
            "Supported values: 'grpc', 'http/protobuf'"
        )
    return protocol


def _append_signal_path(endpoint: str, signal_path: str) -> str:
    """Append an OTLP/HTTP per-signal path (e.g. ``v1/traces``) to a base endpoint.

    Per the OTel spec, ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a *base* URL for
    OTLP/HTTP and exporters must append the per-signal path. If the endpoint
    already ends with the signal path (user supplied a full URL), it is used
    as-is to avoid double-appending.
    """
    if endpoint.rstrip("/").endswith(signal_path):
        return endpoint
    return endpoint.rstrip("/") + "/" + signal_path


def _create_exporters(
    protocol: str,
    endpoint: str,
    metrics_endpoint: Optional[str],
    headers: Dict[str, str],
) -> tuple:
    """Create the OTLP span and metric exporters for the given protocol.

    Args:
        protocol: ``"grpc"`` or ``"http/protobuf"`` (validated by
            :func:`_get_otlp_protocol`).
        endpoint: Base OTLP endpoint. For HTTP, per-signal paths
            (``v1/traces`` / ``v1/metrics``) are appended.
        metrics_endpoint: Optional per-signal metrics endpoint
            (``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT``) — used verbatim when set.
        headers: OTLP headers; passed to both exporters.

    Returns:
        A ``(trace_exporter, metric_exporter)`` tuple.

    Raises:
        ImportError: If the exporter package for the requested protocol is
            not installed.
    """
    if protocol == "http/protobuf":
        if not HTTP_EXPORTER_AVAILABLE:
            raise ImportError(
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf requires the "
                "opentelemetry-exporter-otlp-proto-http package. "
                "Install with: pip install opentelemetry-exporter-otlp-proto-http"
            )
        traces_endpoint = _append_signal_path(endpoint, "v1/traces")
        resolved_metrics_endpoint = metrics_endpoint or _append_signal_path(
            endpoint, "v1/metrics"
        )
        logger.info(
            "OTel exporter protocol: http/protobuf, traces endpoint: %s, metrics endpoint: %s",
            traces_endpoint,
            resolved_metrics_endpoint,
        )
        return (
            HTTPSpanExporter(endpoint=traces_endpoint, headers=headers or None),
            HTTPMetricExporter(endpoint=resolved_metrics_endpoint, headers=headers or None),
        )

    if not GRPC_EXPORTER_AVAILABLE:
        raise ImportError(
            "OTEL_EXPORTER_OTLP_PROTOCOL=grpc requires the "
            "opentelemetry-exporter-otlp-proto-grpc package. "
            "Install with: pip install opentelemetry-exporter-otlp-proto-grpc"
        )
    insecure = not endpoint.startswith("https://")
    resolved_metrics_endpoint = metrics_endpoint or endpoint
    logger.info(
        "OTel exporter protocol: grpc, traces endpoint: %s, metrics endpoint: %s",
        endpoint,
        resolved_metrics_endpoint,
    )
    return (
        GRPCSpanExporter(endpoint=endpoint, insecure=insecure, headers=headers or None),
        GRPCMetricExporter(
            endpoint=resolved_metrics_endpoint, insecure=insecure, headers=headers or None
        ),
    )


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
