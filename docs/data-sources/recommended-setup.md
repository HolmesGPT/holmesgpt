# Recommended Setup

After installing HolmesGPT and running your first investigation, connect your data sources so Holmes can perform deeper investigations.

## How Holmes Works

HolmesGPT is an AI troubleshooting agent that investigates issues by pulling data from your existing observability stack. The more data sources you connect, the more thoroughly Holmes can investigate — correlating metrics with logs, tracing infrastructure changes to application failures, and building a complete picture of what went wrong.

Holmes works across cloud, on-premise, and hybrid environments. If you use Kubernetes, the Kubernetes toolsets are enabled automatically. But Kubernetes is not required — Holmes works equally well with Prometheus, Datadog, Elasticsearch, AWS, GCP, databases, and [many other data sources](builtin-toolsets/index.md). Configure the toolsets that match your stack.

## 1. Connect a Metrics Provider

Metrics give Holmes visibility into trends over time. Without metrics, Holmes can still investigate using logs and infrastructure state, but it won't be able to spot gradual degradation or correlate historical information as well. Metrics are also critical to answering numerical questions, like 'what is the error rate for service xyz?'

Connect whichever metrics platform you already use:

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **Prometheus** | [Setup](builtin-toolsets/prometheus.md) | Most common. Works with self-hosted, Grafana Cloud (Mimir), AWS AMP, Azure Managed Prometheus, Google Managed Prometheus, and Coralogix PromQL |
| **Datadog** | [Setup](builtin-toolsets/datadog.md) | Enable `datadog/metrics` (and optionally `datadog/logs`, `datadog/traces`, `datadog/general`) |
| **New Relic** | [Setup](builtin-toolsets/newrelic.md) | Uses NRQL for metrics, traces, and logs in one toolset |
| **Coralogix** | [Setup](builtin-toolsets/coralogix-logs.md) | For Coralogix-native log and metrics queries |

**Quick example** (Prometheus):

```yaml-toolset-config
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://prometheus-server.monitoring:9090
```

## 2. Connect Centralized Logging

Centralized logging gives Holmes access to historical logs, cross-service log correlation, and full-text search across your environment. This is especially important for investigating issues where logs from the affected service are no longer available — crashed processes, terminated containers, rotated log files, or services running on VMs and bare metal.

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **Loki** | [Setup](builtin-toolsets/grafanaloki.md) | Can connect through Grafana or directly |
| **Elasticsearch / OpenSearch** | [Setup](builtin-toolsets/elasticsearch.md) | `elasticsearch/data` for log search, `elasticsearch/cluster` for cluster health |
| **Datadog Logs** | [Setup](builtin-toolsets/datadog.md) | Enable `datadog/logs` alongside metrics |
| **Splunk** | [Setup](builtin-toolsets/splunk-mcp.md) | Via MCP server |

**Quick example** (Loki via Grafana):

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_key: <your-grafana-token>
      api_url: https://your-grafana.net
      grafana_datasource_uid: <loki-datasource-uid>
```

## 3. Connect Your Cloud Provider

Cloud provider access lets Holmes investigate infrastructure-level causes — misconfigured security groups, IAM permission changes, database failovers, load balancer issues, DNS misconfigurations, or resource quota limits. Many production incidents involve changes at the infrastructure layer that aren't visible from application metrics or logs alone.

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **AWS** | [Setup](builtin-toolsets/aws.md) | Read-only access to EC2, RDS, ELB, CloudWatch, CloudTrail, and more via MCP server |
| **GCP** | [Setup](builtin-toolsets/gcp.md) | Logging, monitoring, traces, gcloud CLI, and storage via MCP server |
| **Azure** | [Setup](builtin-toolsets/azure-mcp.md) | Azure resource management via MCP server |

## 4. Connect Grafana Dashboards (Bonus)

If you use Grafana, connecting the dashboards toolset lets Holmes see what you're already monitoring — it can find relevant dashboards, extract PromQL queries from panels, and use them during investigations.

| Platform | Setup Guide |
|----------|-------------|
| **Grafana Dashboards** | [Setup](builtin-toolsets/grafanadashboards.md) |

## Verify Your Setup

After configuring your data sources, verify everything is connected:

```bash
# List all enabled toolsets
holmes toolset list

# Test with a real investigation
holmes ask "what is the health of my environment?"
```

## Next Steps

- **[Interactive Mode](../walkthrough/interactive-mode.md)** - Use Holmes interactively for follow-up questions
- **[Investigating Prometheus Alerts](../walkthrough/investigating-prometheus-alerts.md)** - Automate alert investigation
- **[All Built-in Toolsets](builtin-toolsets/index.md)** - Browse the full list of integrations
- **[Custom Toolsets](custom-toolsets.md)** - Create integrations for proprietary tools
