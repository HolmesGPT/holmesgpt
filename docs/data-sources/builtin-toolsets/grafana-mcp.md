# Grafana (MCP)

The Grafana MCP server provides comprehensive access to your Grafana instance and its ecosystem. It enables Holmes to search dashboards, run PromQL and LogQL queries, investigate incidents, manage alerts, and explore metrics — all through a single MCP connection.

## Prerequisites

- A running Grafana instance (Grafana Cloud or self-hosted)
- A [Grafana MCP server](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/grafana/README.md) deployed and accessible from Holmes
- A Grafana service account token (see below)

**Creating a service account token:**

1. In Grafana, go to **Administration** → **Users and Access** → **Service Accounts**
2. Click **Add service account** and set the role to **Viewer**
3. Click **Create**, then go into the created service account
4. Click **Add service account token** → **Generate token** (no expiration or longest duration available)
5. Copy the token (starts with `glsa_...`)

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    mcp_servers:
      grafana:
        description: "Grafana observability and dashboards"
        config:
          url: "https://your-grafana-instance/mcp"
          mode: streamable-http
          extra_headers:
            X-Grafana-API-Key: "<YOUR_TOKEN>"
        icon_url: "https://cdn.simpleicons.org/grafana/F46800"
        # These instructions were tested and produce improved results
        llm_instructions: |
          Always use Grafana tools (e.g. query_prometheus) for metrics/PromQL. Do not use kubectl top or prometheus/metrics toolset.
          NEVER answer based on truncated data. Retry with topk/bottomk or more filters until the query succeeds.
          For high-cardinality metrics (>10 series), ALWAYS use topk(5, <query>). Check cardinality first with count() if unsure.
          Standard metrics: CPU=container_cpu_usage_seconds_total, Memory=container_memory_working_set_bytes, Throttling=container_cpu_cfs_throttled_periods_total.
          NEVER use type "promql" embeds. ALWAYS embed charts: << {"type": "chart", "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array, return Chart.js config */ }", "title": "Title"} >>
          Embed at most 2 charts with line spacing between them.
    ```

    Replace `<YOUR_TOKEN>` with your Grafana service account token.

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=grafana-api-key="glsa_..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: GRAFANA_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: grafana-api-key

    mcp_servers:
      grafana:
        description: "Grafana observability and dashboards"
        config:
          url: "https://your-grafana-instance/mcp"
          mode: streamable-http
          extra_headers:
            X-Grafana-API-Key: "{{ env.GRAFANA_API_KEY }}"
        icon_url: "https://cdn.simpleicons.org/grafana/F46800"
        # These instructions were tested and produce improved results
        llm_instructions: |
          Always use Grafana tools (e.g. query_prometheus) for metrics/PromQL. Do not use kubectl top or prometheus/metrics toolset.
          NEVER answer based on truncated data. Retry with topk/bottomk or more filters until the query succeeds.
          For high-cardinality metrics (>10 series), ALWAYS use topk(5, <query>). Check cardinality first with count() if unsure.
          Standard metrics: CPU=container_cpu_usage_seconds_total, Memory=container_memory_working_set_bytes, Throttling=container_cpu_cfs_throttled_periods_total.
          NEVER use type "promql" embeds. ALWAYS embed charts: << {"type": "chart", "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array, return Chart.js config */ }", "title": "Title"} >>
          Embed at most 2 charts with line spacing between them.
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=grafana-api-key="glsa_..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # generated_values.yaml
    holmes:
      additionalEnvVars:
        - name: GRAFANA_API_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-secrets
              key: grafana-api-key

      mcp_servers:
        grafana:
          description: "Grafana observability and dashboards"
          config:
            url: "https://your-grafana-instance/mcp"
            mode: streamable-http
            extra_headers:
              X-Grafana-API-Key: "{{ env.GRAFANA_API_KEY }}"
          icon_url: "https://cdn.simpleicons.org/grafana/F46800"
          # These instructions were tested and produce improved results
          llm_instructions: |
              Always use Grafana tools (e.g. query_prometheus) for metrics/PromQL. Do not use kubectl top or prometheus/metrics toolset.
              NEVER answer based on truncated data. Retry with topk/bottomk or more filters until the query succeeds.
              For high-cardinality metrics (>10 series), ALWAYS use topk(5, <query>). Check cardinality first with count() if unsure.
              Standard metrics: CPU=container_cpu_usage_seconds_total, Memory=container_memory_working_set_bytes, Throttling=container_cpu_cfs_throttled_periods_total.
              NEVER use type "promql" embeds. ALWAYS embed charts: << {"type": "chart", "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array, return Chart.js config */ }", "title": "Title"} >>
              Embed at most 2 charts with line spacing between them.
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

!!! warning "MCP endpoint path"
    The Grafana MCP server serves on `/mcp`, not `/sse` or `/mcp/messages`. Make sure your Holmes config URL ends with `/mcp`.

## Available Tools

The Grafana MCP server exposes ~57 tools organized by category:

| Category | Key Tools | Description |
|----------|-----------|-------------|
| **Dashboards** | `search_dashboards`, `get_dashboard_by_uid`, `get_dashboard_panel_queries` | Search, retrieve, and analyze dashboard configurations and panel queries |
| **Datasources** | `list_datasources`, `get_datasource_by_name` | Discover and inspect configured datasources |
| **Prometheus** | `query_prometheus`, `list_prometheus_metric_names`, `list_prometheus_label_values` | Run PromQL queries, discover metrics, and explore label dimensions |
| **Loki** | `query_loki_logs`, `query_loki_stats`, `list_loki_label_names` | Execute LogQL queries, retrieve log patterns and statistics |
| **Alerting** | `list_alert_rules`, `get_alert_rule_by_uid`, `list_contact_points` | Inspect alert rule configurations and notification channels |
| **Incidents** | `list_incidents`, `create_incident`, `get_incident` | Search, create, and manage Grafana Incidents |
| **OnCall** | `get_current_oncall_users`, `list_oncall_schedules` | View on-call schedules, shifts, and team assignments |
| **Sift** | `get_sift_investigation`, `find_error_pattern_logs`, `find_slow_requests` | Run Sift investigations for automated log and trace analysis |
| **Pyroscope** | `fetch_pyroscope_profile`, `list_pyroscope_profile_types` | Fetch continuous profiling data |
| **Navigation** | `generate_deeplink` | Generate deeplink URLs for Grafana resources |

For the full list of tools, see the [Grafana MCP Server documentation](https://github.com/grafana/mcp-grafana).

## Testing the Connection

```bash
holmes ask "List all Grafana dashboards"
```

## Common Use Cases

```bash
holmes ask "show me memory and cpu usage by namespace for the past day?"
```

```bash
holmes ask "Run a PromQL query to show CPU usage for the checkout-api pods over the last hour"
```

```bash
holmes ask "Search Loki logs for errors in the user-service namespace in the last 30 minutes"
```

```bash
holmes ask "What alert rules are currently configured and which ones are firing?"
```

```bash
holmes ask "Who is currently on-call for the platform team?"
```

## Additional Resources

- [Grafana MCP setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/grafana/README.md)
- [Grafana MCP Server (upstream)](https://github.com/grafana/mcp-grafana)
- [Grafana Service Account Tokens](https://grafana.com/docs/grafana/latest/administration/service-accounts/)
