# Datadog

Connect HolmesGPT to Datadog for comprehensive observability including logs, metrics, traces, and more.

A single `datadog` toolset covers logs, metrics, APM traces, and general read-only Datadog APIs (monitors, dashboards, SLOs, incidents, and more) from one set of credentials — enable it once and all Datadog capabilities are available.

!!! note "Legacy per-signal toolsets"
    The separate `datadog/logs`, `datadog/metrics`, `datadog/traces`, and `datadog/general` toolsets still work for existing configurations, but are superseded by the unified `datadog` toolset. See [Advanced: per-signal toolsets](#advanced-per-signal-toolsets) for their options.


## Quick Start

### 1. Get Your API Keys and Site URL

You'll need two keys and your site URL from your Datadog account:

- **API Key**: Found under **Organization Settings > API Keys**
- **Application Key**: Found under **Organization Settings > Application Keys**
- **API URL**: Your Datadog site's API endpoint (note: `api.` subdomain, not `app.`)
    - **US1 (default)**: `https://api.datadoghq.com`
    - **EU**: `https://api.datadoghq.eu`
    - **US3**: `https://api.us3.datadoghq.com`
    - **US5**: `https://api.us5.datadoghq.com`
    - **AP1**: `https://api.ap1.datadoghq.com`
    - **GOV**: `https://api.ddog-gov.com`
    - See the [complete list of Datadog sites](https://docs.datadoghq.com/getting_started/site/) for reference

### 2. Configure HolmesGPT

=== "Holmes CLI"

    Set environment variables:
    ```bash
    export DATADOG_API_KEY="your-datadog-api-key"
    export DATADOG_APP_KEY="your-datadog-app-key"
    ```

    Add to your config file:
    ```yaml
    # anchors: is ignored by Holmes — use it to define reusable YAML blocks
    anchors:
      dd_config: &dd_config
        api_key: "{{ env.DATADOG_API_KEY }}"
        app_key: "{{ env.DATADOG_APP_KEY }}"
        api_url: https://api.datadoghq.com  # Change for EU/other regions

    toolsets:
      datadog:
        enabled: true
        config: *dd_config
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your API keys:
    ```bash
    kubectl create secret generic holmes-datadog-secrets \
      --from-literal=datadog-api-key=your-datadog-api-key \
      --from-literal=datadog-app-key=your-datadog-app-key \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:
    ```yaml
    # Load API keys from secret
    additionalEnvVars:
      - name: DATADOG_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-datadog-secrets
            key: datadog-api-key
      - name: DATADOG_APP_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-datadog-secrets
            key: datadog-app-key

    toolsets:
      # Enable all Datadog capabilities from one toolset
      datadog:
        enabled: true
        config:
          api_key: "{{ env.DATADOG_API_KEY }}"
          app_key: "{{ env.DATADOG_APP_KEY }}"
          api_url: https://api.datadoghq.com  # Change for EU/other regions
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your API keys:
    ```bash
    kubectl create secret generic holmes-datadog-secrets \
      --from-literal=datadog-api-key=your-datadog-api-key \
      --from-literal=datadog-app-key=your-datadog-app-key \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:
    ```yaml
    holmes:
      # Load API keys from secret
      additionalEnvVars:
        - name: DATADOG_API_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-datadog-secrets
              key: datadog-api-key
        - name: DATADOG_APP_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-datadog-secrets
              key: datadog-app-key

      toolsets:
        # Enable all Datadog capabilities from one toolset
        datadog:
          enabled: true
          config:
            api_key: "{{ env.DATADOG_API_KEY }}"
            app_key: "{{ env.DATADOG_APP_KEY }}"
            api_url: https://api.datadoghq.com  # Change for EU/other regions
    ```

### 3. Test It Works

```bash
# Test logs
holmes ask "show me recent logs from Datadog"

# Test metrics
holmes ask "list available Datadog metrics"

# Test general API
holmes ask "list Datadog monitors"
```

That's it! You're now connected to Datadog with all capabilities enabled.

### Optional configuration

The unified toolset accepts these optional fields alongside the credentials:

```yaml
toolsets:
  datadog:
    enabled: true
    config:
      api_key: "{{ env.DATADOG_API_KEY }}"
      app_key: "{{ env.DATADOG_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60           # HTTP request timeout (default: 60)

      # Logs
      logs_default_limit: 100       # Max log events per query (default: 100)
      storage_tier: indexes         # indexes | flex | online-archives (default: indexes)
      compact_logs: true            # Trim log metadata to save context (default: true)

      # Metrics
      metrics_default_limit: 100    # Max metric data points per query (default: 100)

      # General API access
      max_response_size: 10485760   # Max API response size in bytes (default: 10MB)
      allow_custom_endpoints: false # Allow non-whitelisted read-only endpoints (default: false)
```

## Multiple Instances

```multi-instance
toolset: datadog
name: Datadog
config: |
  api_key: "{{ env.DATADOG_API_KEY }}"
  app_key: "{{ env.DATADOG_APP_KEY }}"
  api_url: https://api.datadoghq.com
```

## Capabilities

The unified `datadog` toolset exposes the following capabilities:

| Capability | Purpose | Common Use Cases |
|---------|---------|------------------|
| **Logs** | Query application logs | Debugging errors, tracking deployments, historical analysis |
| **Metrics** | Access performance metrics | CPU/memory monitoring, custom metrics, SLI tracking |
| **APM traces** | Analyze distributed traces | Latency issues, service dependencies, bottlenecks |
| **General API** | Access other Datadog APIs | Monitors, dashboards, SLOs, incidents, synthetics |


## Advanced: per-signal toolsets

The `datadog` toolset above is the recommended way to connect Datadog. The four per-signal toolsets below (`datadog/logs`, `datadog/metrics`, `datadog/traces`, `datadog/general`) remain available for backwards compatibility — for example if you want to enable only one signal, or already have them configured. Each takes the same credentials plus a few signal-specific options.

### Datadog Logs

Query and analyze logs from Datadog, including historical data from terminated pods.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/logs:
    enabled: true
    config:
      api_key: "{{ env.DATADOG_API_KEY }}"
      app_key: "{{ env.DATADOG_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)

      # Optional: Log search configuration
      indexes: ["*"]  # Log indexes to search (default: ["*"])
      compact_logs: True # Reduces log metadata and tags to save LLM context space.
      storage_tier: indexes  # Options: indexes, online-archives, flex (default: indexes)
      default_limit: 100  # Max logs to retrieve in a query (default: 100)


```

**Capabilities**

| Tool | Description |
|------|-------------|
| `fetch_datadog_logs` | Retrieve logs with time range and search query |

**Example Usage**

```bash
# Get logs for a specific pod
holmes ask "show me logs for pod payment-service in namespace production"

# Search for errors in the last hour
holmes ask "find all error logs in the last hour"

# Historical logs from deleted pods
holmes ask "show me logs from the crashed pod that was running yesterday"
```

### Datadog Metrics

Access and analyze metrics from your infrastructure and applications.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/metrics:
    enabled: true
    config:
      api_key: "{{ env.DATADOG_API_KEY }}"
      app_key: "{{ env.DATADOG_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)

      # Optional
      default_limit: 100  # Max data points to retrieve (default: 100)
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `list_active_datadog_metrics` | List metrics that have reported data in the last 24 hours |
| `query_datadog_metrics` | Query specific metrics with aggregation and filtering |
| `get_datadog_metric_metadata` | Get metadata about available metrics |
| `list_datadog_metric_tags` | List available tags and aggregations for a specific metric |

**Example Usage**

```bash
# List available metrics
holmes ask "what metrics are available for my application?"

# Query CPU usage
holmes ask "show me CPU usage for the payment service over the last 6 hours"

# Custom application metrics
holmes ask "analyze the payment_processing_time metric for anomalies"
```

### Datadog Traces

Analyze distributed traces to identify performance bottlenecks and latency issues.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/traces:
    enabled: true
    config:
      api_key: "{{ env.DATADOG_API_KEY }}"
      app_key: "{{ env.DATADOG_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `fetch_datadog_spans` | Search for spans using span syntax with wildcards and filters |
| `aggregate_datadog_spans` | Aggregate spans into buckets and compute metrics and timeseries |

**Example Usage**

```bash
# Find slow requests
holmes ask "find traces where the checkout service took longer than 5 seconds"

# Analyze specific trace
holmes ask "analyze trace ID abc123 for performance issues"

# Service dependencies
holmes ask "show me traces involving both payment and inventory services"
```

### Datadog General

Access general-purpose Datadog API endpoints for read-only operations including monitors, dashboards, SLOs, incidents, synthetics, and more.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/general:
    enabled: true
    config:
      api_key: "{{ env.DATADOG_API_KEY }}"
      app_key: "{{ env.DATADOG_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)

      # Optional
      max_response_size: 10485760  # Max response size in bytes (default: 10MB)
      allow_custom_endpoints: false  # Allow non-whitelisted endpoints (default: false)
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `datadog_api_get` | Perform GET requests to whitelisted Datadog API endpoints |
| `datadog_api_post_search` | Perform POST search operations on whitelisted endpoints |
| `list_datadog_api_resources` | List available API resource categories and endpoints |

**Supported API Endpoints**

The general toolset provides access to the following read-only API categories:

- **Monitors**: List, search, and get monitor details
- **Dashboards**: Access dashboard configurations and lists
- **SLOs**: Query Service Level Objectives and their history
- **Events**: Search and retrieve events
- **Incidents**: Access incident details and timelines
- **Synthetics**: Retrieve synthetic test results and configurations
- **Security Monitoring**: Access security rules and signals
- **Service Map**: Query APM services and dependencies
- **Hosts**: List and get host information
- **Usage & Cost**: Access usage metrics and cost estimates
- **Organizations & Teams**: Query organizational structure

**Example Usage**

```bash
# List all monitors
holmes ask "show me all Datadog monitors"

# Get dashboard details
holmes ask "retrieve my application dashboard from Datadog"

# Check SLO status
holmes ask "what's the current status of our API availability SLO?"

# Search incidents
holmes ask "find recent incidents in Datadog"

# Get synthetic test results
holmes ask "show me the latest synthetic test results for our homepage"
```
