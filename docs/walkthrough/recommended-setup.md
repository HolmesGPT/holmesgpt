# Recommended Setup

After [installing HolmesGPT](../installation/cli-installation.md) and [running your first investigation](index.md), connect your data sources so Holmes can perform deeper investigations.

## What Works Out of the Box

HolmesGPT automatically enables Kubernetes toolsets when it detects `kubectl` access:

- **Kubernetes Core** - Pod status, events, resource descriptions, YAML definitions
- **Kubernetes Logs** - Live pod logs from running and recently terminated containers

This is enough to investigate basic pod issues (CrashLoopBackOff, pending pods, OOMKills). But for production troubleshooting, you'll want to connect the data sources below.

## 1. Connect a Metrics Provider

**Why:** Kubernetes events and logs help Holmes diagnose many issues, but metrics add a critical dimension - performance trends over time. With metrics, Holmes can spot gradual CPU/memory pressure, check alerting rules, correlate resource usage with incidents, and generate PromQL queries on your behalf.

Connect whichever metrics platform you already use:

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **Prometheus** | [Setup](../data-sources/builtin-toolsets/prometheus.md) | Most common. Works with self-hosted, Grafana Cloud (Mimir), AWS AMP, Azure Managed Prometheus, Google Managed Prometheus, and Coralogix PromQL |
| **Datadog** | [Setup](../data-sources/builtin-toolsets/datadog.md) | Enable `datadog/metrics` (and optionally `datadog/logs`, `datadog/traces`, `datadog/general`) |
| **New Relic** | [Setup](../data-sources/builtin-toolsets/newrelic.md) | Uses NRQL for metrics, traces, and logs in one toolset |
| **Coralogix** | [Setup](../data-sources/builtin-toolsets/coralogix-logs.md) | For Coralogix-native log and metrics queries |

**Quick example** (Prometheus):

```yaml
# ~/.holmes/config.yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://prometheus-server.monitoring:9090
```

## 2. Connect Centralized Logging

**Why:** Default Kubernetes logs only cover running pods. When a pod crashes, restarts, or gets evicted, those logs are lost. A centralized logging system gives Holmes access to historical logs, cross-service log correlation, and pattern search across your entire cluster.

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **Loki** | [Setup](../data-sources/builtin-toolsets/grafanaloki.md) | Can connect through Grafana or directly |
| **Elasticsearch / OpenSearch** | [Setup](../data-sources/builtin-toolsets/elasticsearch.md) | `elasticsearch/data` for log search, `elasticsearch/cluster` for cluster health |
| **Datadog Logs** | [Setup](../data-sources/builtin-toolsets/datadog.md) | Enable `datadog/logs` alongside metrics |
| **Splunk** | [Setup](../data-sources/builtin-toolsets/splunk-mcp.md) | Via MCP server |

!!! note
    When you enable Loki, disable the default Kubernetes logs toolset to avoid duplicate results:
    ```yaml
    toolsets:
      grafana/loki:
        enabled: true
        config:
          api_key: <your-grafana-token>
          api_url: https://your-grafana.net
          grafana_datasource_uid: <loki-datasource-uid>
      kubernetes/logs:
        enabled: false
    ```

## 3. Connect Your Cloud Provider

**Why:** Many production issues originate outside Kubernetes - misconfigured security groups, IAM permission changes, database failovers, load balancer issues, or resource quota limits. Connecting your cloud provider lets Holmes investigate infrastructure-level causes.

| Platform | Setup Guide | Notes |
|----------|-------------|-------|
| **AWS** | [Setup](../data-sources/builtin-toolsets/aws.md) | Read-only access to EC2, RDS, ELB, CloudWatch, CloudTrail, and more via MCP server |
| **GCP** | [Setup](../data-sources/builtin-toolsets/gcp.md) | Logging, monitoring, traces, gcloud CLI, and storage via MCP server |
| **Azure** | [Setup](../data-sources/builtin-toolsets/azure-mcp.md) | Azure resource management via MCP server |

## 4. Connect Grafana Dashboards (Bonus)

If you use Grafana, connecting the dashboards toolset lets Holmes see what you're already monitoring - it can find relevant dashboards, extract PromQL queries from panels, and use them during investigations.

| Platform | Setup Guide |
|----------|-------------|
| **Grafana Dashboards** | [Setup](../data-sources/builtin-toolsets/grafanadashboards.md) |

## Verify Your Setup

After configuring your data sources, verify everything is connected:

```bash
# List all enabled toolsets
holmes toolset list

# Test with a real investigation
holmes ask "what is the health of my cluster?"
```

## Next Steps

- **[Interactive Mode](interactive-mode.md)** - Use Holmes interactively for follow-up questions
- **[Investigating Prometheus Alerts](investigating-prometheus-alerts.md)** - Automate alert investigation
- **[All Built-in Toolsets](../data-sources/builtin-toolsets/index.md)** - Browse the full list of integrations
- **[Custom Toolsets](../data-sources/custom-toolsets.md)** - Create integrations for proprietary tools
