# Why HolmesGPT?

HolmesGPT is an AI agent purpose-built for production observability and incident response. This page explains what makes it uniquely suited for troubleshooting production systems at scale.

## 1. Scalable Data Access for Terabytes of Telemetry

Production systems generate enormous amounts of telemetry data—thousands of metric time series per service, gigabytes of logs per day, and millions of trace spans. HolmesGPT is designed to work with this scale.

### Server-Side Filtering

All HolmesGPT toolsets are designed to push filtering to the data source. Instead of retrieving everything and parsing locally, Holmes constructs precise queries with appropriate time ranges, label filters, and aggregations:

```yaml
# Example: Prometheus toolset queries with specific parameters
- name: prometheus_query_range
  parameters:
    - name: query        # PromQL with label selectors
    - name: start_time   # Bounded time range
    - name: end_time
    - name: step         # Appropriate resolution
```

### Iterative Query Narrowing

Holmes uses an agentic loop to progressively narrow its search:

1. Query high-level cluster metrics → identify affected namespace
2. Query namespace-level metrics → identify affected pod
3. Fetch detailed container metrics → analyze root cause

Each step uses targeted queries, keeping the data within token limits while covering the full investigation scope.

### Context-Window-Aware Tooling

For tools that return large JSON responses, HolmesGPT adds built-in parameters that let the LLM control response size:

```python
# JsonFilterMixin adds these parameters to any tool:
"max_depth": "Maximum nesting depth (0 = top-level keys only)"
"jq": "jq expression to extract specific parts (e.g., '.items[0:5]')"
```

This applies to built-in toolsets, MCP server integrations, and the HTTP connector. The LLM can explore a large API response incrementally—first checking top-level structure with `max_depth=0`, then drilling into specific fields with `jq` expressions.

### Tool Output Transformers

For tools that still return large outputs, HolmesGPT supports [transformers](../development/transformers.md) that summarize data before sending it to the LLM, keeping context windows manageable while preserving critical information.

## 2. 40+ Built-In Observability Integrations

HolmesGPT ships with pre-built integrations for the most popular observability and cloud platforms:

- **Metrics**: Prometheus, Datadog, Coralogix
- **Logs**: Loki, Elasticsearch/OpenSearch, Datadog, Coralogix
- **Traces**: Tempo, Datadog, NewRelic
- **Dashboards**: Grafana
- **Infrastructure**: Kubernetes, Docker, Helm, ArgoCD
- **Cloud**: AWS RDS, Azure SQL, Azure AKS
- **Messaging**: Kafka, RabbitMQ
- **Knowledge**: Confluence, Slab, Internet/web search

See the [full list of built-in toolsets](../data-sources/builtin-toolsets/index.md).

### Safe, Read-Only Access

All built-in toolsets are read-only by design. Holmes respects existing platform permissions (Kubernetes RBAC, Grafana roles, cloud IAM policies) and logs all tool calls for auditability.

### No Setup Required

Toolsets auto-detect available services (e.g., Kubernetes if a kubeconfig is present, Prometheus if configured) and activate automatically. For external services, provide connection details via environment variables or `~/.holmes/config.yaml`.

### HTTP Connector for Any API

When you need to integrate a service that doesn't have a built-in toolset, the [HTTP connector](../data-sources/api-toolsets.md) lets you add it through YAML configuration alone:

```yaml
toolsets:
  my-internal-api:
    type: http
    config:
      endpoints:
        - hosts: ["api.internal.company.com"]
          paths: ["/v1/*"]
          methods: ["GET"]
          auth:
            type: bearer
            token: "{{ env.INTERNAL_API_TOKEN }}"
    llm_instructions: |
      Use this API to query internal service status.
      GET /v1/services - list all services
      GET /v1/services/{id}/health - get service health
```

Key features of the HTTP connector:

- **Endpoint whitelisting**: Only approved hosts, paths, and methods are accessible
- **Multiple auth methods**: Basic, Bearer, custom headers
- **Context-window-aware**: Inherits `jq` and `max_depth` parameters for large responses
- **Multi-instance**: Configure multiple API connectors with independent credentials

## 3. Hallucination-Free Visualizations

For supported clients, HolmesGPT provides direct visualization paths that bypass the LLM entirely:

- **Grafana dashboards**: Direct links with correct time ranges and template variables pre-configured
- **Prometheus graphs**: PromQL query links for the relevant time window
- **Tempo/Jaeger traces**: Direct links to specific trace IDs

These visualization URLs are constructed programmatically from tool output metadata—the LLM doesn't interpret the visual data. When Holmes links you to a Grafana dashboard showing checkout service latency for the last hour, that link is guaranteed to render the actual data with the correct parameters.

This means you can verify any claim Holmes makes by clicking through to the source visualization.

## 4. End-to-End Workflow Integration

HolmesGPT integrates into your existing on-call and incident response workflows, covering the full lifecycle from alert ingestion to results delivery.

### Alert Source Integration

Holmes fetches alerts directly from your incident management systems:

```bash
# Investigate Prometheus/AlertManager alerts
holmes investigate alertmanager --alertmanager-url http://alertmanager:9093

# Investigate PagerDuty incidents
holmes investigate pagerduty --pagerduty-api-key <key>

# Investigate OpsGenie alerts
holmes investigate opsgenie --opsgenie-api-key <key>

# Investigate Jira tickets
holmes investigate jira --jira-url https://company.atlassian.net \
  --jira-username user@example.com --jira-api-key <key>

# Investigate GitHub issues
holmes investigate github --github-owner org --github-repository repo \
  --github-pat <token>
```

Holmes automatically extracts alert metadata (labels, severity, annotations), selects relevant toolsets, and begins investigation.

### Results Delivery

Holmes can write investigation findings back to the source system:

```bash
# Write findings back to PagerDuty incident
holmes investigate pagerduty --pagerduty-api-key <key> --update

# Write findings back to Jira ticket
holmes investigate jira --jira-url https://company.atlassian.net \
  --jira-username user@example.com --jira-api-key <key> --update
```

Results include root cause analysis, evidence with links to dashboards and traces, and recommended actions.

### Interactive Follow-Up

Holmes supports interactive mode for drill-down investigations:

```bash
holmes ask "investigate the memory leak in payment-service" --interactive

# Holmes provides initial analysis, then you can ask follow-ups:
> "show me the specific pod that's leaking"
> "what was the memory usage yesterday at this time?"
> "check if this correlates with deployment times"
```

## 5. Kubernetes Operator for Proactive Monitoring

The [Holmes Operator](../operator/index.md) extends HolmesGPT from an on-demand investigation tool into a continuous, declarative health monitoring system using Kubernetes CRDs.

### One-Time Health Checks

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-payments
spec:
  query: "Are all pods in the payments namespace running and healthy?"
  timeout: 30
```

### Scheduled Health Checks

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: ScheduledHealthCheck
metadata:
  name: hourly-cluster-health
spec:
  schedule: "0 * * * *"
  query: "Are there any unhealthy pods or failing deployments?"
  timeout: 60
  destinations:
    - type: slack
      config:
        channel: "#platform-alerts"
```

### Operator Features

- **Kubernetes-native**: Managed through `kubectl`, integrates with RBAC and standard tooling
- **Execution history**: Tracks pass/fail results, LLM rationale, and duration per check
- **Alert routing**: Send failure notifications to Slack or PagerDuty
- **Horizontal scaling**: Lightweight operator coordinates checks across stateless Holmes API servers

See the [Operator documentation](../operator/index.md) for installation and configuration.

## Summary

| Capability | What HolmesGPT Provides |
|------------|------------------------|
| **Data scale** | Server-side filtering, jq/max_depth parameters, transformers for terabyte-scale telemetry |
| **Integrations** | 40+ built-in read-only toolsets, HTTP connector for any API |
| **Visualizations** | Direct links to Grafana, Prometheus, Tempo dashboards—no LLM interpretation |
| **Workflow** | Alert ingestion from PagerDuty/OpsGenie/AlertManager/Jira, results written back |
| **Proactive monitoring** | Kubernetes operator with CRD-based health checks and scheduling |
| **Extensibility** | HTTP connector, MCP servers, custom toolsets—all with context-window-aware parameters |

## Get Started

```bash
pip install holmesgpt
holmes ask "what pods are unhealthy and why?"
```

- **[Installation Guide](../installation/cli-installation.md)** - Set up HolmesGPT
- **[Built-in Toolsets](../data-sources/builtin-toolsets/index.md)** - See all integrations
- **[HTTP Connector](../data-sources/api-toolsets.md)** - Connect any REST API
- **[Operator](../operator/index.md)** - Kubernetes-native health checks
- **[Tool Output Transformers](../development/transformers.md)** - Handle large outputs
