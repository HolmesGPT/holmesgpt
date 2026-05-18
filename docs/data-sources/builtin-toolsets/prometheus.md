# Prometheus

Connect HolmesGPT to Prometheus for metrics analysis and query generation.

## Prerequisites

- A running and accessible Prometheus server
- Ensure HolmesGPT can connect to the Prometheus endpoint (see [Finding your Prometheus URL](#finding-your-prometheus-url))

## Configuration

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        subtype: prometheus
        config:
            prometheus_url: http://<your-prometheus-service>:9090

            # Optional:
            #additional_headers:
            #    Authorization: "Basic <base_64_encoded_string>"
```

!!! note "About `subtype`"
    The `subtype:` field tells HolmesGPT which Prometheus variant you're connecting to. For a plain self-hosted Prometheus use `prometheus`; variant-specific values (`victoriametrics`, `coralogix`, `grafana-cloud`, `aws-managed-prometheus`, `azure-managed-prometheus`, `google-managed-prometheus`) are shown in the sections below. The field is optional — HolmesGPT will infer the variant from the configuration fields when you omit it — but setting it makes the resulting toolset card light up under the correct integration in the UI.

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

## Specific Providers

### Coralogix Prometheus

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
          subtype: coralogix
          config:
            prometheus_url: "https://prom-api.eu2.coralogix.com"  # Use your region's endpoint
            additional_headers:
              token: "{{ env.CORALOGIX_API_KEY }}"
            discover_metrics_from_last_hours: 72  # Look back 72 hours for metrics
            tool_calls_return_data: true
    ```

---

### AWS Managed Prometheus (AMP)

To connect HolmesGPT to AWS Managed Prometheus:

```yaml
holmes:
  toolsets:
    prometheus/metrics:
      enabled: true
      subtype: aws-managed-prometheus
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

### Google Managed Prometheus

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
      subtype: google-managed-prometheus
      config:
        # Set this to the URL of your Prometheus Frontend endpoint, it may change based on the namespace you deployed frontend to.
        prometheus_url: http://frontend.default.svc.cluster.local:9090
```

**Notes:**

* Authentication is handled automatically via Google Cloud (Workload Identity or default service account in the frontend deployed app)
* No additional headers or credentials are required
* The Prometheus Frontend endpoint must be accessible from the cluster

### Azure Managed Prometheus

Before configuring Holmes, make sure you have:

* An Azure Monitor workspace with Managed Prometheus enabled
* A service principal (or managed identity) that has access to the workspace

#### Using a service principal (client secret)

```yaml
holmes:
  toolsets:
    prometheus/metrics:
      enabled: true
      subtype: azure-managed-prometheus
      config:
        prometheus_url: "https://<your-workspace>.<region>.prometheus.monitor.azure.com:443/"
  additionalEnvVars:
    - name: AZURE_CLIENT_ID
      value: "<your-app-client-id>"
    - name: AZURE_TENANT_ID
      value: "<your-tenant-id>"
    - name: AZURE_CLIENT_SECRET
      value: "<your-client-secret>"
```

**Notes:**
- `prometheus_url` must point to the Azure Managed Prometheus workspace endpoint (include the trailing slash).
- No extra headers are required; authentication is handled through Azure AD (service principal or managed identity).
- SSL is enabled by default (`verify_ssl: true`). Disable only if you know you need to trust a custom cert.

### Grafana Cloud (Mimir)

There are two ways to connect HolmesGPT to Grafana Cloud's Prometheus/Mimir endpoint.

#### Option 1: Direct Prometheus Endpoint (Recommended)

Use Grafana Cloud's direct Prometheus endpoint with Basic authentication. This is the simplest approach.

**Find your credentials:**

- Go to your Grafana Cloud portal → your stack → Prometheus card → **Details**
- Note the **remote write endpoint URL** — remove the `/push` suffix to get the query endpoint
- Note the **Username / Instance ID** (a numeric ID)
- Generate a **Cloud Access Policy token** with `metrics:read` scope

The query endpoint URL format is: `https://prometheus-prod-XX-prod-REGION.grafana.net/api/prom`

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      prometheus/metrics:
        enabled: true
        subtype: grafana-cloud
        config:
          prometheus_url: https://prometheus-prod-XX-prod-REGION.grafana.net/api/prom
          additional_headers:
            Authorization: "Basic <base64_encoded_credentials>"
    ```

    The Basic auth credentials are `<instance_id>:<cloud_access_policy_token>` base64-encoded.

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your credentials:

    ```bash
    # Base64-encode your credentials: <instance_id>:<cloud_access_policy_token>
    kubectl create secret generic grafana-cloud-prometheus \
      --from-literal=auth-header="Basic $(echo -n 'INSTANCE_ID:CLOUD_ACCESS_POLICY_TOKEN' | base64)" \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: GRAFANA_CLOUD_PROM_AUTH
        valueFrom:
          secretKeyRef:
            name: grafana-cloud-prometheus
            key: auth-header

    toolsets:
      prometheus/metrics:
        enabled: true
        subtype: grafana-cloud
        config:
          prometheus_url: "https://prometheus-prod-XX-prod-REGION.grafana.net/api/prom"
          additional_headers:
            Authorization: "{{ env.GRAFANA_CLOUD_PROM_AUTH }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your credentials:

    ```bash
    # Base64-encode your credentials: <instance_id>:<cloud_access_policy_token>
    kubectl create secret generic grafana-cloud-prometheus \
      --from-literal=auth-header="Basic $(echo -n 'INSTANCE_ID:CLOUD_ACCESS_POLICY_TOKEN' | base64)" \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: GRAFANA_CLOUD_PROM_AUTH
          valueFrom:
            secretKeyRef:
              name: grafana-cloud-prometheus
              key: auth-header
      toolsets:
        prometheus/metrics:
          enabled: true
          subtype: grafana-cloud
          config:
            prometheus_url: "https://prometheus-prod-XX-prod-REGION.grafana.net/api/prom"
            additional_headers:
              Authorization: "{{ env.GRAFANA_CLOUD_PROM_AUTH }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

#### Option 2: Grafana API Proxy

Use Grafana's datasource proxy to route requests through the Grafana API. This approach uses a Grafana service account token.

**Find your credentials:**

- Navigate to "Administration → Service accounts" in Grafana Cloud
- Create a new service account and generate a token (starts with `glsa_`)
- Find your Prometheus datasource UID:

```bash
curl -H "Authorization: Bearer YOUR_GLSA_TOKEN" \
     "https://YOUR-INSTANCE.grafana.net/api/datasources" | \
     jq '.[] | select(.type=="prometheus") | {name, uid}'
```

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      prometheus/metrics:
        enabled: true
        subtype: grafana-cloud
        config:
          prometheus_url: https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID
          additional_headers:
            Authorization: Bearer YOUR_GLSA_TOKEN
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your service account token:

    ```bash
    kubectl create secret generic grafana-cloud-sa-token \
      --from-literal=token=YOUR_GLSA_TOKEN \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: GRAFANA_CLOUD_SA_TOKEN
        valueFrom:
          secretKeyRef:
            name: grafana-cloud-sa-token
            key: token

    toolsets:
      prometheus/metrics:
        enabled: true
        subtype: grafana-cloud
        config:
          prometheus_url: "https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID"
          additional_headers:
            Authorization: "Bearer {{ env.GRAFANA_CLOUD_SA_TOKEN }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your service account token:

    ```bash
    kubectl create secret generic grafana-cloud-sa-token \
      --from-literal=token=YOUR_GLSA_TOKEN \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: GRAFANA_CLOUD_SA_TOKEN
          valueFrom:
            secretKeyRef:
              name: grafana-cloud-sa-token
              key: token
      toolsets:
        prometheus/metrics:
          enabled: true
          subtype: grafana-cloud
          config:
            prometheus_url: "https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID"
            additional_headers:
              Authorization: "Bearer {{ env.GRAFANA_CLOUD_SA_TOKEN }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

---

## Advanced Configuration

You can further customize the Prometheus toolset with the following options:

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    subtype: prometheus
    config:
      prometheus_url: http://prometheus-server.monitoring.svc.cluster.local:9090
      additional_headers:
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

**Configuration options:**

`subtype` is set at the toolset level (sibling of `enabled:` and `config:`); the rest of the fields below go inside `config:`.

| Option | Default | Description |
|--------|---------|-------------|
| `subtype` | (inferred) | Top-level field — picks the Prometheus variant. One of `prometheus`, `victoriametrics`, `coralogix`, `grafana-cloud`, `aws-managed-prometheus`, `azure-managed-prometheus`, `google-managed-prometheus`. Setting it is recommended; if omitted, HolmesGPT infers the variant from configuration fields (`aws_region` → AMP, Azure-specific fields → Azure) for backwards compatibility. |
| `prometheus_url` | (required for variants other than `prometheus`) | Prometheus server URL (include protocol and port). For `subtype: prometheus` this is optional — if omitted, HolmesGPT auto-detects via the `PROMETHEUS_URL` env var or in-cluster service discovery. |
| `additional_headers` | per `subtype` | Authentication headers (e.g., `Authorization: Bearer token`). Variant defaults: empty for most; `coralogix` defaults to `{token: "{{ env.CORALOGIX_API_KEY }}"}`; `grafana-cloud` defaults to `{Authorization: "Basic {{ env.GRAFANA_CLOUD_AUTH }}"}`. |
| `discover_metrics_from_last_hours` | `1` (`72` for `coralogix`) | Only discover metrics with data in last N hours |
| `query_timeout_seconds_default` | `20` | Default PromQL query timeout |
| `query_timeout_seconds_hard_max` | `180` | Maximum query timeout |
| `metadata_timeout_seconds_default` | `20` | Default metadata/discovery API timeout |
| `metadata_timeout_seconds_hard_max` | `60` | Maximum metadata API timeout |
| `rules_cache_duration_seconds` | `1800` | Cache duration for rules (set to `null` to disable) |
| `verify_ssl` | `true` (`false` for `aws-managed-prometheus`) | Enable SSL certificate verification |
| `tool_calls_return_data` | `true` | Return Prometheus data (disable if hitting token limits) |
| `additional_labels` | `{}` | Labels to add to all queries |

### Variant-specific fields

These fields are only valid for specific `subtype` values:

| Field | `subtype` | Required | Description |
|---|---|---|---|
| `aws_region` | `aws-managed-prometheus` | Yes | AWS region (e.g. `us-east-1`) |
| `aws_access_key` / `aws_secret_access_key` | `aws-managed-prometheus` | No | Falls back to default AWS credential chain when omitted |
| `aws_service_name` | `aws-managed-prometheus` | No (default `aps`) | AWS service name for SigV4 |
| `assume_role_arn` | `aws-managed-prometheus` | No | IAM role to assume for cross-account access |
| `refresh_interval_seconds` | `aws-managed-prometheus`, `azure-managed-prometheus` | No (default `900`) | How often to refresh credentials — AWS STS creds for AMP, Azure AD bearer token for Azure |
| `azure_client_id` / `azure_client_secret` / `azure_tenant_id` | `azure-managed-prometheus` | UI-required | Azure AD service principal. CLI/Helm: omit to use managed identity (`azure_use_managed_id: true`) or `AZURE_*` env vars. |
| `azure_use_managed_id` | `azure-managed-prometheus` | No (default `false`) | Set `true` to use Azure managed identity instead of a service principal |
| `azure_resource` / `azure_metadata_endpoint` / `azure_token_endpoint` | `azure-managed-prometheus` | No | Azure AD endpoints — sensible defaults are applied automatically |

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
