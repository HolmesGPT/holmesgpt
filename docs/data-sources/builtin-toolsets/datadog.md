# Datadog

Connect HolmesGPT to Datadog for comprehensive observability including logs, metrics, traces, and more.


## Quick Start

### 1. Get Your API Keys and Site URL

You'll need two keys and your site URL from your Datadog account:

- **API Key**: Found under **Organization Settings > API Keys** (disable 'Remote Config' when creating)
- **Application Key**: Found under **Organization Settings > Application Keys**
- **Site URL**: Your Datadog site endpoint
    - **US (default)**: `https://app.datadoghq.com`
    - **EU**: `https://app.datadoghq.eu`
    - **Other regions**: See the [complete list of Datadog sites](https://docs.datadoghq.com/getting_started/site/)

### 2. Configure HolmesGPT

=== "Holmes CLI"

    Set environment variables:
    ```bash
    export DD_API_KEY="your-datadog-api-key"
    export DD_APP_KEY="your-datadog-app-key"
    ```

    Add to your config file:
    ```yaml
    toolsets:
      # Enable all Datadog toolsets
      datadog/logs:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com  # Change for EU/other regions

      datadog/metrics:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com

      datadog/traces:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com

      datadog/general:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your API keys:
    ```bash
    kubectl create secret generic holmes-datadog-secrets \
      --from-literal=dd-api-key=your-datadog-api-key \
      --from-literal=dd-app-key=your-datadog-app-key
    ```

    Then add to your Holmes Helm values:
    ```yaml
    # Load API keys from secret
    additionalEnvVars:
      - name: DD_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-datadog-secrets
            key: dd-api-key
      - name: DD_APP_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-datadog-secrets
            key: dd-app-key

    toolsets:
      # Enable all Datadog toolsets
      datadog/logs:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com  # Change for EU/other regions

      datadog/metrics:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com

      datadog/traces:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com

      datadog/general:
        enabled: true
        config:
          api_key: "{{ env.DD_API_KEY }}"
          app_key: "{{ env.DD_APP_KEY }}"
          api_url: https://app.datadoghq.com
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your API keys:
    ```bash
    kubectl create secret generic holmes-datadog-secrets \
      --from-literal=dd-api-key=your-datadog-api-key \
      --from-literal=dd-app-key=your-datadog-app-key
    ```

    Then add to your Robusta Helm values:
    ```yaml
    holmes:
      # Load API keys from secret
      additionalEnvVars:
        - name: DD_API_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-datadog-secrets
              key: dd-api-key
        - name: DD_APP_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-datadog-secrets
              key: dd-app-key

      toolsets:
        # Enable all Datadog toolsets
        datadog/logs:
          enabled: true
          config:
            api_key: "{{ env.DD_API_KEY }}"
            app_key: "{{ env.DD_APP_KEY }}"
            api_url: https://app.datadoghq.com  # Change for EU/other regions

        datadog/metrics:
          enabled: true
          config:
            api_key: "{{ env.DD_API_KEY }}"
            app_key: "{{ env.DD_APP_KEY }}"
            api_url: https://app.datadoghq.com

        datadog/traces:
          enabled: true
          config:
            api_key: "{{ env.DD_API_KEY }}"
            app_key: "{{ env.DD_APP_KEY }}"
            api_url: https://app.datadoghq.com

        datadog/general:
          enabled: true
          config:
            api_key: "{{ env.DD_API_KEY }}"
            app_key: "{{ env.DD_APP_KEY }}"
            api_url: https://app.datadoghq.com
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

That's it! You're now connected to Datadog with all toolsets enabled.

## Available Toolsets

HolmesGPT provides four specialized Datadog toolsets:

| Toolset | Purpose | Common Use Cases |
|---------|---------|------------------|
| **[datadog/logs](#datadog-logs)** | Query application logs | Debugging errors, tracking deployments, historical analysis |
| **[datadog/metrics](#datadog-metrics)** | Access performance metrics | CPU/memory monitoring, custom metrics, SLI tracking |
| **[datadog/traces](#datadog-traces)** | Analyze distributed traces | Latency issues, service dependencies, bottlenecks |
| **[datadog/general](#datadog-general)** | Access other Datadog APIs | Monitors, dashboards, SLOs, incidents, synthetics |


## Toolset Details

### Datadog Logs

Query and analyze logs from Datadog, including historical data from terminated pods.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/logs:
    enabled: true
    config:
      api_key: "{{ env.DD_API_KEY }}"
      app_key: "{{ env.DD_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)

      # Optional: Log search configuration
      indexes: ["*"]  # Log indexes to search (default: ["*"])
      compact_logs: True # Reduces log metadata and tags to save LLM context space.
      storage_tiers: ["indexes"]  # Options: indexes, online-archives, flex
      default_limit: 150  # Max logs to retrieve in a query.


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
      api_key: "{{ env.DD_API_KEY }}"
      app_key: "{{ env.DD_APP_KEY }}"
      api_url: https://api.datadoghq.com
      timeout_seconds: 60  # Timeout in seconds (default: 60)

      # Optional
      default_limit: 1000  # Max data points to retrieve (default: 1000)
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
      api_key: "{{ env.DD_API_KEY }}"
      app_key: "{{ env.DD_APP_KEY }}"
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
      api_key: "{{ env.DD_API_KEY }}"
      app_key: "{{ env.DD_APP_KEY }}"
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
