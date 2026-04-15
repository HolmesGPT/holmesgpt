# OpenTelemetry Observability

HolmesGPT includes built-in OpenTelemetry (OTel) instrumentation that produces **distributed traces** and **metrics** for every investigation. This enables end-to-end observability from user prompt through LLM calls and MCP tool execution.

## Enabling OpenTelemetry

OTel instrumentation activates automatically when the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable is set. No code changes or flags are needed.

### CLI

```bash
# Run with OTel enabled
export OTEL_EXPORTER_OTLP_ENDPOINT="http://your-otel-collector:4317"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
export OTEL_SERVICE_NAME="holmesgpt"
holmes ask "Why is my pod crashing?"
```

### Helm / Kubernetes

Add the following to your Helm `values.yaml`:

```yaml
additionalEnvVars:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector.monitoring.svc:4317"
  - name: OTEL_EXPORTER_OTLP_PROTOCOL
    value: "grpc"
  - name: OTEL_SERVICE_NAME
    value: "holmesgpt"
  # Optional: for backends requiring auth (e.g., Dynatrace, Grafana Cloud)
  - name: OTEL_EXPORTER_OTLP_HEADERS
    value: "Authorization=Api-Token YOUR_TOKEN"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint (enables OTel when set) | *(unset — OTel disabled)* |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Export protocol (`grpc` or `http/protobuf`) | `grpc` |
| `OTEL_SERVICE_NAME` | Service name in traces/metrics | `holmesgpt` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Headers for OTLP exporter (e.g., auth tokens) | *(none)* |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Override metrics endpoint (if different from traces) | *(uses OTEL_EXPORTER_OTLP_ENDPOINT)* |

When `OTEL_EXPORTER_OTLP_ENDPOINT` is **not** set, HolmesGPT uses a no-op `DummyTracer` with zero overhead.

## Distributed Traces

Every investigation produces a trace hierarchy:

```
holmesgpt.investigation (root span)
├── gen_ai.chat (LLM iteration 0 — includes token counts)
│   └── POST (auto-instrumented httpx → LLM provider)
├── holmesgpt.tool.<name> (tool/MCP call)
│   └── POST (auto-instrumented httpx → MCP server)
│       └── MCP server spans (execute_tool, k8s.api/*, etc.)
├── gen_ai.chat (LLM iteration 1)
│   └── ...
└── gen_ai.chat (final iteration — produces answer)
```

### Span Attributes

**Investigation span** (`holmesgpt.investigation`):
- `holmesgpt.investigation.question` — the user's question
- `holmesgpt.investigation.stream` — whether streaming was used

**LLM spans** (`gen_ai.chat`):
- `gen_ai.system` — LLM provider (`litellm`)
- `gen_ai.request.model` — model name
- `gen_ai.usage.input_tokens` — prompt tokens
- `gen_ai.usage.output_tokens` — completion tokens
- `gen_ai.usage.total_tokens` — total tokens
- `holmesgpt.iteration` — iteration number (0-based)

**Tool spans** (`holmesgpt.tool.<name>`):
- `holmesgpt.tool.name` — tool name
- `holmesgpt.tool.status` — result status (`success` or `error`)

### MCP Trace Propagation

When HolmesGPT calls MCP tools over HTTP, trace context is automatically propagated via W3C `traceparent` headers (using httpx auto-instrumentation). MCP servers that support OpenTelemetry will create child spans linked to the same trace.

## Metrics

HolmesGPT exports the following OTel metrics via OTLP. All metrics use **underscore-delimited attribute keys** for maximum compatibility across backends.

### Token Usage

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `gen_ai.client.token.usage` | Counter | `{token}` | LLM token consumption |

**Dimensions:** `gen_ai_request_model`, `gen_ai_system`, `gen_ai_token_type` (`input` or `output`)

### Investigation Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `holmesgpt.investigation.count` | Counter | `{investigation}` | Number of investigations started |
| `holmesgpt.investigation.duration` | Histogram | `s` | End-to-end investigation duration |
| `holmesgpt.investigation.iterations` | Histogram | `{iteration}` | LLM iterations per investigation |

**Dimensions:** `gen_ai_request_model`

### LLM Call Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `gen_ai.client.operation.duration` | Histogram | `s` | Individual LLM call latency |

**Dimensions:** `gen_ai_request_model`, `gen_ai_system`

### Tool / MCP Call Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `holmesgpt.tool.call.count` | Counter | `{call}` | Number of tool calls |
| `holmesgpt.tool.call.duration` | Histogram | `s` | Tool call latency |
| `holmesgpt.tool.call.errors` | Counter | `{error}` | Failed tool calls |

**Dimensions:** `holmesgpt_tool_name`

### Example Queries

**Dynatrace DQL:**

```sql
# Tool call duration by tool name
timeseries avg(holmesgpt.tool.call.duration, default:0), by: {holmesgpt_tool_name}

# Token usage by model
timeseries sum(gen_ai.client.token.usage, default:0), by: {gen_ai_request_model, gen_ai_token_type}

# Investigation count over time
timeseries sum(holmesgpt.investigation.count, default:0)

# Slowest tools (p95)
timeseries percentile(holmesgpt.tool.call.duration, 95), by: {holmesgpt_tool_name}
```

**PromQL (Grafana / Prometheus):**

```promql
# Tool call rate by tool name
rate(holmesgpt_tool_call_count_total[5m])

# Average LLM call duration
rate(gen_ai_client_operation_duration_sum[5m]) / rate(gen_ai_client_operation_duration_count[5m])
```

## Backend Examples

### Dynatrace (direct OTLP)

```yaml
additionalEnvVars:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "https://YOUR_ENV.live.dynatrace.com/api/v2/otlp"
  - name: OTEL_EXPORTER_OTLP_PROTOCOL
    value: "grpc"
  - name: OTEL_SERVICE_NAME
    value: "holmesgpt"
  - name: OTEL_EXPORTER_OTLP_HEADERS
    value: "Authorization=Api-Token YOUR_DT_TOKEN"
```

### OTel Collector (self-hosted)

```yaml
additionalEnvVars:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector.monitoring.svc:4317"
  - name: OTEL_EXPORTER_OTLP_PROTOCOL
    value: "grpc"
  - name: OTEL_SERVICE_NAME
    value: "holmesgpt"
```

### Grafana Cloud

```yaml
additionalEnvVars:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
  - name: OTEL_EXPORTER_OTLP_PROTOCOL
    value: "grpc"
  - name: OTEL_SERVICE_NAME
    value: "holmesgpt"
  - name: OTEL_EXPORTER_OTLP_HEADERS
    value: "Authorization=Basic BASE64_ENCODED_INSTANCE:TOKEN"
```

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    HolmesGPT                         │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  Tracer   │  │  Metrics  │  │ httpx auto-instr │   │
│  │ (traces)  │  │(counters, │  │ (W3C traceparent)│   │
│  │          │  │histograms)│  │                  │   │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
│       │              │                 │             │
└───────┼──────────────┼─────────────────┼─────────────┘
        │              │                 │
        │  OTLP/gRPC   │                 │ W3C traceparent
        ▼              ▼                 ▼
┌─────────────────┐              ┌─────────────────┐
│  OTel Collector  │              │   MCP Servers    │
│  or Direct OTLP  │              │  (child spans)   │
└────────┬────────┘              └─────────────────┘
         │
         ▼
┌─────────────────┐
│    Backend       │
│ (Dynatrace,     │
│  Grafana, etc.) │
└─────────────────┘
```
