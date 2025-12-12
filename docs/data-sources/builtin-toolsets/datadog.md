# Datadog

Connect HolmesGPT to Datadog for comprehensive observability including logs, metrics, traces, and more.

--8<-- "snippets/toolsets_that_provide_logging.md"

## Quick Start

### 1. Get Your API Keys

You'll need two keys from your Datadog account:

- **API Key**: Found under **Organization Settings > API Keys**
- **Application Key**: Found under **Organization Settings > Application Keys**

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
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com  # Change for EU/other regions

      datadog/metrics:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/traces:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/rds:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/general:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com
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
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com  # Change for EU/other regions

      datadog/metrics:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/traces:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/rds:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com

      datadog/general:
        enabled: true
        config:
          dd_api_key: "{{ env.DD_API_KEY }}"
          dd_app_key: "{{ env.DD_APP_KEY }}"
          site_api_url: https://app.datadoghq.com
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
    runner:
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

      customToolsets:
        # Enable all Datadog toolsets
        datadog/logs:
          enabled: true
          config:
            dd_api_key: "{{ env.DD_API_KEY }}"
            dd_app_key: "{{ env.DD_APP_KEY }}"
            site_api_url: https://app.datadoghq.com  # Change for EU/other regions

        datadog/metrics:
          enabled: true
          config:
            dd_api_key: "{{ env.DD_API_KEY }}"
            dd_app_key: "{{ env.DD_APP_KEY }}"
            site_api_url: https://app.datadoghq.com

        datadog/traces:
          enabled: true
          config:
            dd_api_key: "{{ env.DD_API_KEY }}"
            dd_app_key: "{{ env.DD_APP_KEY }}"
            site_api_url: https://app.datadoghq.com

        datadog/rds:
          enabled: true
          config:
            dd_api_key: "{{ env.DD_API_KEY }}"
            dd_app_key: "{{ env.DD_APP_KEY }}"
            site_api_url: https://app.datadoghq.com

        datadog/general:
          enabled: true
          config:
            dd_api_key: "{{ env.DD_API_KEY }}"
            dd_app_key: "{{ env.DD_APP_KEY }}"
            site_api_url: https://app.datadoghq.com
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

HolmesGPT provides five specialized Datadog toolsets:

| Toolset | Purpose | Common Use Cases |
|---------|---------|------------------|
| **[datadog/logs](#datadog-logs)** | Query application logs | Debugging errors, tracking deployments, historical analysis |
| **[datadog/metrics](#datadog-metrics)** | Access performance metrics | CPU/memory monitoring, custom metrics, SLI tracking |
| **[datadog/traces](#datadog-traces)** | Analyze distributed traces | Latency issues, service dependencies, bottlenecks |
| **[datadog/rds](#datadog-rds)** | Monitor RDS databases | Database performance, slow queries, connection issues |
| **[datadog/general](#datadog-general)** | Access other Datadog APIs | Monitors, dashboards, SLOs, incidents, synthetics |

## Core Configuration

All Datadog toolsets share the same basic configuration structure:

```yaml-toolset-config
toolsets:
  datadog/<toolset-name>:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com  # Required - see regions below
```

### Regional Endpoints

Configure the correct API endpoint for your Datadog region:

| Region | Site API URL |
|--------|--------------|
| US1 (default) | `https://app.datadoghq.com` |
| US3 | `https://us3.datadoghq.com` |
| US5 | `https://us5.datadoghq.com` |
| EU1 | `https://app.datadoghq.eu` |
| US1-FED (Government) | `https://app.ddog-gov.com` |
| AP1 (Japan) | `https://ap1.datadoghq.com` |
| AP2 (Australia) | `https://ap2.datadoghq.com` |


## Toolset Details

### Datadog Logs

Query and analyze logs from Datadog, including historical data from terminated pods.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/logs:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com
      request_timeout: 60  # Timeout in seconds (default: 60)

      # Optional: Log search configuration
      indexes: ["*"]  # Log indexes to search (default: ["*"])
      storage_tiers: ["indexes"]  # Options: indexes, online-archives, flex
      page_size: 300  # Results per page (default: 300)
      default_limit: 1000  # Max logs to retrieve (default: 1000)

      # Optional: Kubernetes field mappings
      labels:
        pod: "pod_name"  # Datadog field for pod name
        namespace: "kube_namespace"  # Datadog field for namespace

  # Disable Kubernetes native logging when using Datadog
  kubernetes/logs:
    enabled: false
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `fetch_pod_logs` | Retrieve logs for specific pods with time range and filter support |

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
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com
      request_timeout: 60  # Timeout in seconds (default: 60)

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
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com
      request_timeout: 60  # Timeout in seconds (default: 60)

      # Optional
      indexes: ["*"]  # Trace indexes to search (default: ["*"])
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `fetch_datadog_traces` | Search and fetch traces by service, operation, or tags |
| `fetch_datadog_trace_by_id` | Get detailed information about a specific trace |
| `fetch_datadog_spans` | Search for spans with specific filters |

**Example Usage**

```bash
# Find slow requests
holmes ask "find traces where the checkout service took longer than 5 seconds"

# Analyze specific trace
holmes ask "analyze trace ID abc123 for performance issues"

# Service dependencies
holmes ask "show me traces involving both payment and inventory services"
```

### Datadog RDS

Monitor and troubleshoot RDS database instances.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/rds:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com
      request_timeout: 60  # Timeout in seconds (default: 60)

      # Optional
      default_time_span_seconds: 3600  # Time span for queries (default: 1 hour)
      default_top_instances: 10  # Number of top instances (default: 10)
```

**Capabilities**

| Tool | Description |
|------|-------------|
| `datadog_rds_performance_report` | Generate comprehensive performance report for an RDS instance |
| `datadog_rds_top_worst_performing` | Get report of worst performing RDS instances by latency, CPU, and errors |

**Example Usage**

```bash
# List database instances
holmes ask "show me all RDS instances and their status"

# Performance analysis
holmes ask "analyze the performance of the production database"

# Slow query analysis
holmes ask "find slow queries on the analytics database"
```

### Datadog General

Access general-purpose Datadog API endpoints for read-only operations including monitors, dashboards, SLOs, incidents, synthetics, and more.

**Configuration**

```yaml-toolset-config
toolsets:
  datadog/general:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.com
      request_timeout: 60  # Timeout in seconds (default: 60)

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

## Complete Configuration Example

Here's a comprehensive example enabling all Datadog toolsets:

```yaml-toolset-config
toolsets:
  # Logs configuration
  datadog/logs:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.eu  # EU region
      request_timeout: 60
      indexes: ["main", "security"]
      storage_tiers: ["indexes", "online-archives"]
      page_size: 300
      default_limit: 2000
      labels:
        pod: "pod_name"
        namespace: "kube_namespace"

  # Metrics configuration
  datadog/metrics:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.eu
      request_timeout: 60
      default_limit: 5000

  # Traces configuration
  datadog/traces:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.eu
      request_timeout: 60
      indexes: ["*"]

  # RDS monitoring
  datadog/rds:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.eu
      request_timeout: 60
      default_time_span_seconds: 3600
      default_top_instances: 10

  # General API access
  datadog/general:
    enabled: true
    config:
      dd_api_key: "{{ env.DD_API_KEY }}"
      dd_app_key: "{{ env.DD_APP_KEY }}"
      site_api_url: https://api.datadoghq.eu
      request_timeout: 60
      max_response_size: 10485760
      allow_custom_endpoints: false

  # Disable Kubernetes native logging when using Datadog
  kubernetes/logs:
    enabled: false
```
