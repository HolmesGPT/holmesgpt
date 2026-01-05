# Prometheus

Connect HolmesGPT to Prometheus for metrics analysis and query generation. This integration enables detection of memory leaks, CPU throttling, queue backlogs, and performance issues.

## Prerequisites

- A running and accessible Prometheus server
- Ensure HolmesGPT can connect to the Prometheus endpoint

## Configuration

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: http://<your-prometheus-service>:9090

            # Optional:
            #headers:
            #    Authorization: "Basic <base_64_encoded_string>"
```


💡 **Alternative**: Set environment variables instead of using the config file:
- `PROMETHEUS_URL`: The Prometheus server URL
- `PROMETHEUS_AUTH_HEADER`: Optional authorization header value (e.g., "Bearer token123")

## Validation

To test your connection, run:

```bash
holmes ask "Show me the CPU usage for the last hour"
```

## Troubleshooting



### Finding your Prometheus URL

There are several ways to find your Prometheus URL:

**Option 1: Simple method (port-forwarding)**

```bash
# Find Prometheus services
kubectl get svc -A | grep prometheus

# Port forward for testing
kubectl port-forward svc/<your-prometheus-service> 9090:9090 -n <namespace>
# Then access Prometheus at: http://localhost:9090
```

**Option 2: Advanced method (get full cluster DNS URL)**

If you want to find the full internal DNS URL for Prometheus, run:

```bash
kubectl get svc --all-namespaces -o jsonpath='{range .items[*]}{.metadata.name}{"."}{.metadata.namespace}{".svc.cluster.local:"}{.spec.ports[0].port}{"\n"}{end}' | grep prometheus | grep -Ev 'operat|alertmanager|node|coredns|kubelet|kube-scheduler|etcd|controller' | awk '{print "http://"$1}'
```

This will print all possible Prometheus service URLs in your cluster. Pick the one that matches your deployment.

### Common Issues

- **Connection refused**: Check if the Prometheus URL is accessible from HolmesGPT.
- **Authentication errors**: Verify the headers configuration for secured Prometheus endpoints.
- **No metrics returned**: Ensure that Prometheus is scraping your targets.


## Advanced Configuration

You can further customize the Prometheus toolset with the following options:

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://prometheus-server.monitoring.svc.cluster.local:9090
      headers:
        Authorization: "Basic <base64_encoded_credentials>"

      # Discovery settings
      discover_metrics_from_last_hours: 1  # Only return metrics with data in last N hours (default: 1)

      # Timeout configuration
      query_timeout_seconds_default: 20  # Default timeout for PromQL queries (default: 20)
      query_timeout_seconds_hard_max: 180  # Maximum allowed timeout for PromQL queries (default: 180)
      metadata_timeout_seconds_default: 20  # Default timeout for metadata/discovery APIs (default: 20)
      metadata_timeout_seconds_hard_max: 60  # Maximum allowed timeout for metadata APIs (default: 60)

      # Other options
      rules_cache_duration_seconds: 1800  # Cache duration for Prometheus rules (default: 30 minutes)
      verify_ssl: true  # Enable SSL verification (default: true)
      tool_calls_return_data: true  # If false, disables returning Prometheus data (default: true)
      additional_labels:  # Additional labels to add to all queries
        cluster: "production"
```

**Config option explanations:**

- `prometheus_url`: The base URL for Prometheus. Should include protocol and port.
- `headers`: Extra headers for all Prometheus HTTP requests (e.g., for authentication).
- `discover_metrics_from_last_hours`: Only return metrics that have data in the last N hours when using discovery APIs (get_metric_names, get_label_values, etc.). Default: 1 hour. Increase if you have metrics that report infrequently.
- `query_timeout_seconds_default`: Default timeout for PromQL queries. Can be overridden per query. Default: 20.
- `query_timeout_seconds_hard_max`: Maximum allowed timeout for PromQL queries. Default: 180.
- `metadata_timeout_seconds_default`: Default timeout for metadata/discovery API calls. Default: 20.
- `metadata_timeout_seconds_hard_max`: Maximum allowed timeout for metadata API calls. Default: 60.
- `rules_cache_duration_seconds`: How long to cache Prometheus rules. Set to `null` to disable caching. Default: 1800 (30 minutes).
- `verify_ssl`: Enable SSL certificate verification. Default: true.
- `tool_calls_return_data`: If `false`, disables returning Prometheus data to HolmesGPT (useful if you hit token limits). Default: true.
- `additional_labels`: Dictionary of labels to add to all queries (currently only implemented for AWS/AMP).

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| list_prometheus_rules | List all defined Prometheus rules with descriptions and annotations |
| get_metric_names | Get list of metric names (fastest discovery method) - requires match filter |
| get_label_values | Get all values for a specific label (e.g., pod names, namespaces) |
| get_all_labels | Get list of all label names available in Prometheus |
| get_series | Get time series matching a selector (returns full label sets) |
| get_metric_metadata | Get metadata (type, description, unit) for metrics |
| execute_prometheus_instant_query | Execute an instant PromQL query (single point in time) |
| execute_prometheus_range_query | Execute a range PromQL query for time series data with graph generation |

---

## Coralogix Prometheus Configuration

To use a Coralogix PromQL endpoint with HolmesGPT:

1. Go to [Coralogix Documentation](https://coralogix.com/docs/integrations/coralogix-endpoints/#promql) and choose the relevant PromQL endpoint for your region.
2. In Coralogix, create an API key with permissions to query metrics (Data Flow → API Keys).
3. Create a Kubernetes secret for the API key and expose it as an environment variable in your Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: CORALOGIX_API_KEY
          valueFrom:
            secretKeyRef:
              name: coralogix-api-key
              key: CORALOGIX_API_KEY
    ```

4. Add the following under your toolsets in the Helm chart:

    ```yaml
    holmes:
      toolsets:
        prometheus/metrics:
          enabled: true
          config:
            prometheus_url: "https://prom-api.eu2.coralogix.com"  # Use your region's endpoint
            headers:
              token: "{{ env.CORALOGIX_API_KEY }}"
            discover_metrics_from_last_hours: 72  # Look back 72 hours for metrics
            tool_calls_return_data: true
    ```

---

## AWS Managed Prometheus (AMP) Configuration

To connect HolmesGPT to AWS Managed Prometheus:

```yaml
holmes:
  toolsets:
    prometheus/metrics:
      enabled: true
      config:
        prometheus_url: https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/
        aws_region: us-east-1
        aws_service_name: aps  # Default value, can be omitted
        # Optional: Specify credentials (otherwise uses default AWS credential chain)
        aws_access_key: "{{ env.AWS_ACCESS_KEY_ID }}"
        aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
        # Optional: Assume a role for cross-account access
        assume_role_arn: "arn:aws:iam::123456789012:role/PrometheusReadRole"
        refresh_interval_seconds: 900  # Refresh AWS credentials every 15 minutes (default)
```

**Notes:**
- The toolset automatically detects AWS configuration when `aws_region` is present
- Uses SigV4 authentication for all requests
- Supports IAM roles and cross-account access via `assume_role_arn`
- Credentials refresh automatically based on `refresh_interval_seconds`

---

## Google Managed Prometheus Configuration

Before configuring Holmes, make sure you have:

* Google Managed Prometheus enabled
* A Prometheus Frontend endpoint accessible from your cluster
  (If you don’t already have one, you can create it following the instructions
  [here](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query-api-ui#ui-prometheus) )

To connect HolmesGPT to Google Cloud Managed Prometheus:

```yaml
holmes:
  toolsets:
    prometheus/metrics:
      enabled: true
      config:
        # Set this to the URL of your Prometheus Frontend endpoint, it may change based on the namespace you deployed frontend to.
        prometheus_url: http://frontend.default.svc.cluster.local:9090
```

**Notes:**

* Authentication is handled automatically via Google Cloud (Workload Identity or default service account in the frontend deployed app)
* No additional headers or credentials are required
* The Prometheus Frontend endpoint must be accessible from the cluster

## Grafana Cloud (Mimir) Configuration

To connect HolmesGPT to Grafana Cloud's Prometheus/Mimir endpoint:

1. **Create a service account token in Grafana Cloud:**
   - Navigate to "Administration → Service accounts"
   - Create a new service account
   - Generate a service account token (starts with `glsa_`)

2. **Find your Prometheus datasource UID:**
   ```bash
   curl -H "Authorization: Bearer YOUR_GLSA_TOKEN" \
        "https://YOUR-INSTANCE.grafana.net/api/datasources" | \
        jq '.[] | select(.type=="prometheus") | {name, uid}'
   ```

3. **Configure HolmesGPT:**
   ```yaml
   holmes:
     toolsets:
       prometheus/metrics:
         enabled: true
         config:
           prometheus_url: https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID
           headers:
             Authorization: Bearer YOUR_GLSA_TOKEN
   ```

**Important notes:**

- Use the proxy endpoint URL format `/api/datasources/proxy/uid/` - this handles authentication and routing to Mimir automatically
- The toolset automatically detects and uses the most appropriate APIs for discovery
