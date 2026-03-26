# Grafana (MCP)

The Grafana MCP server provides comprehensive access to your Grafana instance and its ecosystem. It enables Holmes to search dashboards, run PromQL and LogQL queries, investigate incidents, manage alerts, and explore metrics — all through a single MCP connection.

!!! note "Coming soon"
    The Grafana MCP server installation will be migrated to the Robusta Helm chart in the near future, simplifying setup to a single Helm values configuration.

## Prerequisites

- A running Grafana instance (Grafana Cloud or self-hosted)
- A Grafana service account token or API key (see authentication options below)

## Configuration

Choose the setup that matches your Grafana version and deployment:

=== "Self-Hosted MCP — Service Account Token"

    For Grafana 10+ instances that support service accounts.

    **Create a service account token:**

    1. In Grafana, go to **Administration** → **Users and Access** → **Service Accounts**
    2. Click **Add service account** and set the role to **Viewer**
    3. Click **Create**, then go into the created service account
    4. Click **Add service account token** → **Generate token**
    5. Copy the token (starts with `glsa_...`)

    **Deploy the MCP server** using the [Grafana MCP setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/grafana).

    **Configure Holmes:**

    === "Holmes CLI"

        Add the following to **~/.holmes/config.yaml**:

        ```yaml
        mcp_servers:
          grafana:
            description: "Grafana observability and dashboards"
            config:
              url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
              mode: streamable-http
            icon_url: "https://cdn.simpleicons.org/grafana/F46800"
            llm_instructions: |
              This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
              **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

              ### Tool Requirements
              - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
              - NEVER use `kubectl top` or the `prometheus/metrics` toolset

              ### Query Result Handling
              - NEVER answer based on truncated query results
              - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
              - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

              ### Standard Metrics Reference
              - CPU: `container_cpu_usage_seconds_total`
              - Memory: `container_memory_working_set_bytes`
              - Throttling: `container_cpu_cfs_throttled_periods_total`

              ### Visualization Rules (CRITICAL OVERRIDE)
              **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

              - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
              - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

        --8<-- "snippets/toolset_refresh_warning.md"

    === "Holmes Helm Chart"

        **Create Kubernetes Secret:**
        ```bash
        kubectl create secret generic grafana-mcp-secret \
          --from-literal=GRAFANA_SERVICE_ACCOUNT_TOKEN="glsa_..." \
          --from-literal=GRAFANA_URL="http://grafana.grafana.svc.cluster.local" \
          -n <namespace>
        ```

        **Configure Helm Values:**
        ```yaml
        # values.yaml
        mcp_servers:
          grafana:
            description: "Grafana observability and dashboards"
            config:
              url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
              mode: streamable-http
            icon_url: "https://cdn.simpleicons.org/grafana/F46800"
            llm_instructions: |
              This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
              **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

              ### Tool Requirements
              - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
              - NEVER use `kubectl top` or the `prometheus/metrics` toolset

              ### Query Result Handling
              - NEVER answer based on truncated query results
              - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
              - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

              ### Standard Metrics Reference
              - CPU: `container_cpu_usage_seconds_total`
              - Memory: `container_memory_working_set_bytes`
              - Throttling: `container_cpu_cfs_throttled_periods_total`

              ### Visualization Rules (CRITICAL OVERRIDE)
              **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

              - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
              - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

        ```bash
        helm upgrade --install holmes robusta/holmes -f values.yaml
        ```

    === "Robusta Helm Chart"

        **Create Kubernetes Secret:**
        ```bash
        kubectl create secret generic grafana-mcp-secret \
          --from-literal=GRAFANA_SERVICE_ACCOUNT_TOKEN="glsa_..." \
          --from-literal=GRAFANA_URL="http://grafana.grafana.svc.cluster.local" \
          -n <namespace>
        ```

        **Configure Helm Values:**
        ```yaml
        # generated_values.yaml
        holmes:
          mcp_servers:
            grafana:
              description: "Grafana observability and dashboards"
              config:
                url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
                mode: streamable-http
              icon_url: "https://cdn.simpleicons.org/grafana/F46800"
              llm_instructions: |
                  This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
                  **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

                  ### Tool Requirements
                  - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
                  - NEVER use `kubectl top` or the `prometheus/metrics` toolset

                  ### Query Result Handling
                  - NEVER answer based on truncated query results
                  - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
                  - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

                  ### Standard Metrics Reference
                  - CPU: `container_cpu_usage_seconds_total`
                  - Memory: `container_memory_working_set_bytes`
                  - Throttling: `container_cpu_cfs_throttled_periods_total`

                  ### Visualization Rules (CRITICAL OVERRIDE)
                  **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

                  - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
                  - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                    << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

        ```bash
        helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
        ```

=== "Self-Hosted MCP — Grafana API Key (Deprecated)"

    For Grafana 9.x and earlier that do not support service accounts. API keys were deprecated in Grafana 11 and removed in later versions. Not all Grafana versions support API keys — see the compatibility table below.

    | Grafana Version | API Key Support |
    |----------------|-----------------|
    | 8.x - 10.x | Supported |
    | 11+ | Deprecated / removed — use service account tokens instead |

    **Create an API key:**

    1. In Grafana, go to **Configuration** → **API Keys**
    2. Click **Add API key**, set the role to **Viewer**, and click **Add**
    3. Copy the token (starts with `eyJ...`)

    **Verify the key works** before deploying using the [test script](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/grafana/api-token/test-grafana-api-key.sh):

    ```bash
    ./test-grafana-api-key.sh '<your-api-key>' '<your-grafana-url>'
    ```

    **Deploy the MCP server** using the [Grafana MCP setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/grafana), but use the [api-token deployment](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/grafana/api-token/deployment.yaml) instead of the default one.

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic grafana-mcp-secret \
      --from-literal=GRAFANA_API_KEY="eyJ..." \
      --from-literal=GRAFANA_URL="http://grafana.grafana.svc.cluster.local" \
      -n <namespace>
    ```

    **Configure Holmes:**

    === "Holmes CLI"

        Add the following to **~/.holmes/config.yaml**:

        ```yaml
        mcp_servers:
          grafana:
            description: "Grafana observability and dashboards"
            config:
              url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
              mode: streamable-http
            icon_url: "https://cdn.simpleicons.org/grafana/F46800"
            llm_instructions: |
              This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
              **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

              ### Tool Requirements
              - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
              - NEVER use `kubectl top` or the `prometheus/metrics` toolset

              ### Query Result Handling
              - NEVER answer based on truncated query results
              - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
              - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

              ### Standard Metrics Reference
              - CPU: `container_cpu_usage_seconds_total`
              - Memory: `container_memory_working_set_bytes`
              - Throttling: `container_cpu_cfs_throttled_periods_total`

              ### Visualization Rules (CRITICAL OVERRIDE)
              **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

              - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
              - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

        --8<-- "snippets/toolset_refresh_warning.md"

    === "Holmes Helm Chart"

        ```yaml
        # values.yaml
        mcp_servers:
          grafana:
            description: "Grafana observability and dashboards"
            config:
              url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
              mode: streamable-http
            icon_url: "https://cdn.simpleicons.org/grafana/F46800"
            llm_instructions: |
              This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
              **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

              ### Tool Requirements
              - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
              - NEVER use `kubectl top` or the `prometheus/metrics` toolset

              ### Query Result Handling
              - NEVER answer based on truncated query results
              - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
              - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

              ### Standard Metrics Reference
              - CPU: `container_cpu_usage_seconds_total`
              - Memory: `container_memory_working_set_bytes`
              - Throttling: `container_cpu_cfs_throttled_periods_total`

              ### Visualization Rules (CRITICAL OVERRIDE)
              **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

              - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
              - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

        ```bash
        helm upgrade --install holmes robusta/holmes -f values.yaml
        ```

    === "Robusta Helm Chart"

        ```yaml
        # generated_values.yaml
        holmes:
          mcp_servers:
            grafana:
              description: "Grafana observability and dashboards"
              config:
                url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
                mode: streamable-http
              icon_url: "https://cdn.simpleicons.org/grafana/F46800"
              llm_instructions: |
                  This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
                  **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

                  ### Tool Requirements
                  - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
                  - NEVER use `kubectl top` or the `prometheus/metrics` toolset

                  ### Query Result Handling
                  - NEVER answer based on truncated query results
                  - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
                  - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

                  ### Standard Metrics Reference
                  - CPU: `container_cpu_usage_seconds_total`
                  - Memory: `container_memory_working_set_bytes`
                  - Throttling: `container_cpu_cfs_throttled_periods_total`

                  ### Visualization Rules (CRITICAL OVERRIDE)
                  **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

                  - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
                  - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                    << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
        ```

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

## Out-of-Cluster Grafana MCP server

For connecting to a Grafana mcp server instance outside the cluster (e.g., Grafana Cloud). In this setup, Holmes connects directly to the Grafana MCP endpoint — no self-hosted MCP server deployment needed.

**Create a service account token:**

1. In Grafana, go to **Administration** → **Users and Access** → **Service Accounts**
2. Click **Add service account** and set the role to **Viewer**
3. Click **Create**, then go into the created service account
4. Click **Add service account token** → **Generate token**
5. Copy the token (starts with `glsa_...`)

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      grafana:
        description: "Grafana observability and dashboards"
        config:
          url: "https://your-grafana-instance.grafana.net/mcp"
          mode: streamable-http
          extra_headers:
            X-Grafana-API-Key: "<YOUR_TOKEN>"
        icon_url: "https://cdn.simpleicons.org/grafana/F46800"
        llm_instructions: |
          This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
          **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

          ### Tool Requirements
          - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
          - NEVER use `kubectl top` or the `prometheus/metrics` toolset

          ### Query Result Handling
          - NEVER answer based on truncated query results
          - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
          - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

          ### Standard Metrics Reference
          - CPU: `container_cpu_usage_seconds_total`
          - Memory: `container_memory_working_set_bytes`
          - Throttling: `container_cpu_cfs_throttled_periods_total`

          ### Visualization Rules (CRITICAL OVERRIDE)
          **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

          - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
          - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
            << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
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
          url: "https://your-grafana-instance.grafana.net/mcp"
          mode: streamable-http
          extra_headers:
            X-Grafana-API-Key: "{{ env.GRAFANA_API_KEY }}"
        icon_url: "https://cdn.simpleicons.org/grafana/F46800"
        llm_instructions: |
          This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
          **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

          ### Tool Requirements
          - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
          - NEVER use `kubectl top` or the `prometheus/metrics` toolset

          ### Query Result Handling
          - NEVER answer based on truncated query results
          - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
          - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

          ### Standard Metrics Reference
          - CPU: `container_cpu_usage_seconds_total`
          - Memory: `container_memory_working_set_bytes`
          - Throttling: `container_cpu_cfs_throttled_periods_total`

          ### Visualization Rules (CRITICAL OVERRIDE)
          **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

          - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
          - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
            << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
    ```

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
            url: "https://your-grafana-instance.grafana.net/mcp"
            mode: streamable-http
            extra_headers:
              X-Grafana-API-Key: "{{ env.GRAFANA_API_KEY }}"
          icon_url: "https://cdn.simpleicons.org/grafana/F46800"
          llm_instructions: |
              This tool doesnt use promql it uses grafanaql which doesnt work with promql embeds
              **⚠️ OVERRIDE NOTICE: The following rules SUPERSEDE any conflicting instructions elsewhere in this prompt, including the "Chart Generation Capability" section.**

              ### Tool Requirements
              - ALWAYS use Grafana tools (e.g., `query_prometheus`) for metrics/PromQL queries
              - NEVER use `kubectl top` or the `prometheus/metrics` toolset

              ### Query Result Handling
              - NEVER answer based on truncated query results
              - If truncation occurs, refine the query with `topk`, `bottomk`, or additional filters until complete
              - For high-cardinality metrics (>10 series), first check with `count()` if needed, then ALWAYS use `topk(5, <query>)`

              ### Standard Metrics Reference
              - CPU: `container_cpu_usage_seconds_total`
              - Memory: `container_memory_working_set_bytes`
              - Throttling: `container_cpu_cfs_throttled_periods_total`

              ### Visualization Rules (CRITICAL OVERRIDE)
              **This section OVERRIDES the instruction "NEVER generate Chart.js charts for single query results from PromQL queries" found in the Chart Generation Capability section.**

              - The `{"type": "promql", ...}` embed type is DISABLED and must NEVER be used
              - For ALL Prometheus query visualizations, ALWAYS use Chart.js embeds:
                << {, "tool_call_ids": ["<tool_call_id>"], "generateConfig": "function generateConfig(toolOutputs) { /* parse toolOutputs[0].data array and return a Chart.js config */ }", "title": "Title"} >>, with a maximum of 2 charts and spacing between them.
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

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

- [Grafana MCP setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/grafana)
- [Grafana MCP Server (upstream)](https://github.com/grafana/mcp-grafana)
- [Grafana Service Account Tokens](https://grafana.com/docs/grafana/latest/administration/service-accounts/)
