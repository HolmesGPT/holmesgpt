# OpenTelemetry Tracing for HolmesGPT

This document describes the OpenTelemetry (OTEL) tracing implementation for HolmesGPT, following the [Gen AI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

## Overview

HolmesGPT supports comprehensive observability through OpenTelemetry, enabling:

- **Distributed Tracing**: Track requests across the entire agent execution lifecycle
- **Metrics**: Monitor token usage, operation durations, and error rates
- **Structured Logging**: Correlate logs with trace context

The implementation follows OpenTelemetry Gen AI semantic conventions for standardized observability of AI/ML workloads.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Entry Points                                │
├─────────────────────────────────────────────────────────────────┤
│  CLI (holmes ask)  │  server.py  │  AG-UI (server-agui.py)      │
└─────────┬──────────┴──────┬──────┴──────────┬────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TracingFactory                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Braintrust  │  │   OTEL      │  │    CompositeTracer      │  │
│  │   Tracer    │  │   Tracer    │  │ (dual tracing support)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                 │
          │                 ▼
          │    ┌─────────────────────────────────────┐
          │    │     experimental/otel/              │
          │    │  ┌─────────┐ ┌─────────┐ ┌───────┐ │
          │    │  │tracing  │ │metrics  │ │logging│ │
          │    │  └─────────┘ └─────────┘ └───────┘ │
          │    └─────────────────────────────────────┘
          │                 │
          ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OTLP Exporter                                 │
│         (with optional AWS SigV4 for OSIS endpoints)            │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│              Observability Backend                               │
│     (OpenSearch/OSIS, Jaeger, Zipkin, etc.)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_ENABLED` | Enable OTEL tracing (`true`/`false`) | `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint URL | Required |
| `OTEL_AWS_PROFILE` | AWS profile for OSIS authentication | None |
| `OTEL_AWS_REGION` | AWS region for OSIS (auto-detected from endpoint) | Auto |
| `OTEL_METRICS_ENABLED` | Enable OTEL metrics | `true` |
| `OTEL_DEBUG` | Enable debug logging for span lifecycle | `false` |

### Example Configuration

```bash
# Basic OTEL setup
export OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4318/v1/traces

# AWS OSIS (OpenSearch Ingestion Service)
export OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=https://pipeline-id.us-east-1.osis.amazonaws.com/otel-trace/v1/traces
export OTEL_AWS_PROFILE=your-aws-profile
export OTEL_AWS_REGION=us-east-1
```

## Span Hierarchy

The tracing implementation creates a hierarchical span structure following Gen AI conventions:

```
invoke_agent HolmesGPT                    # Root span for agent invocation
├── chat gpt-4                            # LLM iteration 1
│   ├── execute_tool kubectl_get          # Tool execution
│   └── execute_tool prometheus_query     # Tool execution
├── chat gpt-4                            # LLM iteration 2
│   └── execute_tool kubectl_describe     # Tool execution
└── chat gpt-4                            # Final LLM iteration (answer)
```

### Span Naming Convention

Following Gen AI semantic conventions:

| Span Type | Name Format | Example |
|-----------|-------------|---------|
| Agent invocation | `invoke_agent {agent_name}` | `invoke_agent HolmesGPT` |
| LLM chat/completion | `chat {model}` | `chat gpt-4` |
| Tool execution | `execute_tool {tool_name}` | `execute_tool kubectl_get` |

## Attributes

### Required Gen AI Attributes

| Attribute | Description |
|-----------|-------------|
| `gen_ai.operation.name` | Operation type: `invoke_agent`, `chat`, `execute_tool` |
| `gen_ai.request.model` | Model ID used for the request |
| `gen_ai.agent.name` | Agent name (e.g., `HolmesGPT`) |

### Token Usage Attributes

| Attribute | Description |
|-----------|-------------|
| `gen_ai.usage.input_tokens` | Number of prompt tokens |
| `gen_ai.usage.output_tokens` | Number of completion tokens |
| `gen_ai.usage.total_tokens` | Total tokens used |
| `gen_ai.usage.cost_usd` | Cost in USD (if available) |

### Tool Attributes

| Attribute | Description |
|-----------|-------------|
| `gen_ai.tool.name` | Tool name |
| `gen_ai.tool.call_id` | Unique tool call identifier |
| `gen_ai.tool.duration_ms` | Tool execution duration in milliseconds |
| `gen_ai.tool.output` | Tool output (truncated to 8KB) |

### Correlation Attributes

| Attribute | Description |
|-----------|-------------|
| `gen_ai.request.id` | Request/run ID for correlation |
| `gen_ai.conversation.id` | Conversation/thread ID |

## Metrics

The following metrics are exported following Gen AI conventions:

| Metric | Type | Description |
|--------|------|-------------|
| `gen_ai.client.token.usage` | Histogram | Token usage by type (input/output) |
| `gen_ai.client.operation.duration` | Histogram | Operation duration in seconds |
| `gen_ai.tool.duration` | Histogram | Tool execution duration |
| `gen_ai.agent.iterations` | Counter | Number of LLM iterations |
| `gen_ai.agent.tool_calls` | Counter | Number of tool calls |
| `gen_ai.agent.errors` | Counter | Number of errors |

## Usage

### Server Mode (server.py)

OTEL is automatically enabled when `OTEL_ENABLED=true`:

```bash
OTEL_ENABLED=true \
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector:4318/v1/traces \
python server.py
```

The server creates:

- HTTP middleware that wraps all API requests in spans
- Automatic span context propagation

### AG-UI Mode (server-agui.py)

```bash
OTEL_ENABLED=true \
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector:4318/v1/traces \
python experimental/ag-ui/server-agui.py
```

Features:

- Root spans for each agent invocation
- Child spans for each LLM iteration
- Nested tool execution spans
- Token usage and cost tracking per iteration

### CLI Mode

```bash
holmes ask --trace otel "Why is my pod crashing?"
```

Or for dual tracing (both Braintrust and OTEL):

```bash
holmes ask --trace braintrust,otel "Why is my pod crashing?"
```

### Programmatic Usage

```python
from holmes.core.tracing import TracingFactory, SpanType

# Initialize OTEL at startup
TracingFactory.init_otel()

# Create tracer
tracer = TracingFactory.create_tracer("otel")

# Use spans
with tracer.start_trace("my_operation", SpanType.TASK) as span:
    span.set_attributes(span_attributes={"custom.attribute": "value"})

    # Create child span
    with span.start_span("child_operation", SpanType.TOOL) as child:
        child.log(input="tool input", output="tool output")
```

## AWS OSIS Integration

For AWS OpenSearch Ingestion Service (OSIS), the implementation:

1. Automatically detects OSIS endpoints (`.osis.` in URL)
2. Uses AWS SigV4 request signing
3. Supports separate AWS profiles for OTEL vs other AWS services

```bash
# Use different AWS profiles for Bedrock and OSIS
export AWS_PROFILE=bedrock-profile        # For LLM calls
export OTEL_AWS_PROFILE=osis-profile      # For OTEL export
export OTEL_EXPORTER_OTLP_ENDPOINT=https://pipeline.us-east-1.osis.amazonaws.com/...
```

## Files

| File | Description |
|------|-------------|
| `holmes/core/tracing.py` | Unified TracingFactory with OTEL support |
| `experimental/otel/tracing.py` | OTEL tracer initialization and AWS SigV4 auth |
| `experimental/otel/attributes.py` | Gen AI semantic convention constants |
| `experimental/otel/metrics.py` | OTEL metrics instrumentation |
| `experimental/otel/otel_logging.py` | OTEL-correlated logging |
| `server.py` | Main server with OTEL middleware |
| `experimental/ag-ui/server-agui.py` | AG-UI server with full OTEL instrumentation |

## Testing

Run the OTEL test suite:

```bash
poetry run python experimental/otel/test_otel.py
```

## Troubleshooting

### Common Issues

**"OTEL tracing requested but OTEL_ENABLED not set to 'true'"**

Set `OTEL_ENABLED=true` in your environment.

**"Failed to create OSIS session"**

Check your AWS credentials and ensure `OTEL_AWS_PROFILE` points to a valid profile with OSIS permissions.

**"payload too large" errors**

Tool outputs are automatically truncated to 8KB. If you still see this error, check for large metadata values.

### Debug Logging

Enable debug logging to see OTEL initialization details:

```bash
LOG_LEVEL=DEBUG python server.py
```

## References

- [OpenTelemetry Gen AI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)
- [AWS OpenSearch Ingestion](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ingestion.html)
