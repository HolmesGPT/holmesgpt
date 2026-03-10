# Why HolmesGPT

HolmesGPT is an AI agent purpose-built for production observability and incident response.

## 1. Petabyte-Scale Observability Data

Production systems generate enormous amounts of telemetry data. HolmesGPT is designed to work at this scale without pulling unbounded data into context:

- **Aggregations at source**: Where possible, filters and aggregations are pushed to the data source rather than fetching everything and parsing locally
- **Traversable JSON trees**: For APIs that return large JSON payloads, Holmes transforms responses into traversable trees with filtering and depth-limiting controls so the LLM can extract data without pulling the entire payload into context
- **Summarization transformers**: For tools that still return large outputs, HolmesGPT supports transformers that summarize data before it reaches the LLM

## 2. Memory-Safe Execution

Per-tool memory limits, streaming large results to disk, and automatic output budgeting prevent OOM kills when querying large observability datasets.

## 3. Operator Mode

HolmesGPT can run in the background to proactively find problems and notify your team.

The [Holmes Operator](operator/index.md) manages health checks as Kubernetes-native resources:

**One-Time Health Checks:**

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-payments
spec:
  query: "Are all pods in the payments namespace running and healthy?"
  timeout: 30
```

**Scheduled Health Checks:**

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

See the [Operator documentation](operator/index.md) for installation and configuration.

## 4. Connect Any API as a Data Source

HolmesGPT ships with read-only integrations for every major observability vendor. Connect custom MCP servers for proprietary tools, or use the [HTTP connector](data-sources/api-toolsets.md) to turn any REST API into an LLM-friendly data source through YAML alone.

- **Metrics**: Prometheus, Datadog, Coralogix, NewRelic
- **Logs**: Loki, Elasticsearch/OpenSearch, Datadog, Coralogix, Splunk
- **Traces**: Tempo, Datadog, NewRelic
- **Dashboards**: Grafana
- **Infrastructure**: Kubernetes, Docker, Helm, ArgoCD, OpenShift, Cilium, KubeVela
- **Cloud**: AWS RDS, Azure SQL, Azure AKS, GCP
- **Databases**: PostgreSQL, MySQL, ClickHouse, MariaDB, SQL Server, MongoDB Atlas
- **ITSM**: ServiceNow
- **Messaging**: Kafka, RabbitMQ
- **Knowledge**: Confluence, Notion, Slab, Internet/web search

See the [full list of built-in toolsets](data-sources/builtin-toolsets/index.md).

### Safe by Design

Give SRE agents the data access they need, with the safety profile production demands. All built-in toolsets are read-only, respecting existing platform permissions (Kubernetes RBAC, Grafana roles, cloud IAM policies) with full audit logging of every tool call.

### Controlled Access for Your Whole Team

Instead of every engineer connecting their local AI tools to production with personal credentials that carry write access, deploy one Holmes instance with scoped, read-only access. Let engineers use LLMs with observability data - safely.

### Raw HTTP Endpoints as LLM-Friendly Tools

When you need to integrate a service that doesn't have a built-in toolset, the [HTTP connector](data-sources/api-toolsets.md) turns raw HTTP endpoints into LLM-friendly tools through YAML configuration—no MCP servers or custom code required:

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

Holmes automatically transforms these raw endpoints to be LLM-friendly:

- **Context-window-aware**: Adds `jq` and `max_depth` parameters so the LLM can navigate large responses without overflow
- **Endpoint whitelisting**: Only approved hosts, paths, and methods are accessible—safe by default
- **Multiple auth methods**: Basic, Bearer, custom headers—configured once, used automatically
- **Multi-instance**: Configure multiple API connectors with independent credentials

## 5. Runtime Dependency Graph

Reconstructs upstream/downstream chains from the production data you didn't realize you already have. Sees the dependency graph as it actually runs, not as it was designed.

Holmes infers service relationships from the telemetry data already flowing through your stack:

- **Distributed traces**: Span parent-child relationships in Tempo reveal which services call which, with latency at each hop
- **Kubernetes resource graphs**: Ownership chains from deployments to pods to services, plus network policies and ingress rules
- **Metric labels**: Prometheus `job`, `instance`, and custom labels connect metrics to the services that emit them

Works even without distributed tracing—Holmes infers service relationships from Kubernetes resource hierarchies and metric labels alone, but takes advantage of trace data if available.

## 6. Zero-Hallucination Visualizations

When Holmes queries a data source like Prometheus, the raw response data—time series, log entries, trace spans—is passed through to the client alongside the LLM's text analysis. Supported clients render this data as interactive HTML and JavaScript visualizations in a sandboxed environment: metric graphs with tooltips, legends, and zoom; sortable log tables with severity coloring and CSV export; distributed trace waterfalls with timing breakdowns.

The LLM decides *what* to query and *how* to analyze it, but the visualization itself is a faithful rendering of the raw data. There is no opportunity for the LLM to hallucinate values, misread a graph, or fabricate trends—what you see is exactly what the data source returned.

One such supporting client is implemented by [Robusta.dev](https://home.robusta.dev/).

## 7. Alert-to-Resolution Workflow

HolmesGPT can integrate into your existing workflows, by automatically fetching alerts and incidents from AlertManager, PagerDuty, OpsGenie, or more—and writing the investigation results back to the source.

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

## Get Started

See the [Installation Guide](installation/cli-installation.md) to set up HolmesGPT.
