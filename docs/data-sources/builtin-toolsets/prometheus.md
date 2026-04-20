# Prometheus

Connect HolmesGPT to Prometheus for metrics analysis and PromQL query generation.

**Jump to:** [Standard Prometheus](#configuration) | [Coralogix](#coralogix) | [AWS AMP](#aws-managed-prometheus-amp) | [Azure](#azure-managed-prometheus) | [Google Managed](#google-managed-prometheus) | [Grafana Cloud](#grafana-cloud-mimir)

## Prerequisites

- A running and accessible Prometheus server
- Ensure HolmesGPT can connect to the Prometheus endpoint (see [Finding your Prometheus URL](#finding-your-prometheus-url))

## Configuration

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: http://<your-prometheus-service>:9090
            # additional_headers:
            #     Authorization: "Basic <base64_encoded_credentials>"
            # discover_metrics_from_last_hours: 1      # Metric discovery lookback window (default: 1)
            # query_timeout_seconds_default: 20        # Default query timeout (default: 20)
            # query_timeout_seconds_hard_max: 180      # Max allowed query timeout (default: 180)
            # metadata_timeout_seconds_default: 20     # Metadata API timeout (default: 20)
            # metadata_timeout_seconds_hard_max: 60    # Max metadata API timeout (default: 60)
            # rules_cache_duration_seconds: 1800       # Rules cache TTL (default: 1800, null to disable)
            # verify_ssl: true                         # SSL verification (default: true)
            # tool_calls_return_data: true             # Return query data (default: true)
            # query_response_size_limit_pct: null      # Token limit as % of context window
```

**Environment variables** (alternative to config file):

- `PROMETHEUS_URL`: Prometheus server URL
- `PROMETHEUS_AUTH_HEADER`: Authorization header value (e.g., `Bearer token123`)

## Validation

```bash
holmes ask "Show me CPU usage for the last hour"
```

## Finding your Prometheus URL

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

??? tip "Prompt for AI agent"

    Paste this into any AI coding assistant (Claude Code, Cursor, etc.) to have it find and configure your Prometheus URL automatically:

    ```
    I need to find my Prometheus server URL for HolmesGPT configuration.
    1. Run: kubectl get svc -A | grep -i prom
    2. Identify which service is the main Prometheus query endpoint
       (ignore alertmanager, node-exporter, operator, pushgateway)
    3. Give me the full internal DNS URL in the format:
       http://<service>.<namespace>.svc.cluster.local:<port>
    4. Verify it works by running:
       kubectl run --rm -it prom-test --image=curlimages/curl --restart=Never -- curl -s <url>/api/v1/status/buildinfo
    ```

---

## Coralogix

**Setup:**

1. Find your [regional PromQL endpoint](https://coralogix.com/docs/integrations/coralogix-endpoints/#promql)
2. Create API key in Coralogix (Data Flow → API Keys) with metrics query permissions
3. Store key in Kubernetes secret and reference via `{{ env.CORALOGIX_API_KEY }}`

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: "https://prom-api.<region>.coralogix.com"
            additional_headers:
                token: "{{ env.CORALOGIX_API_KEY }}"
            discover_metrics_from_last_hours: 72
            # query_timeout_seconds_default: 20
            # query_timeout_seconds_hard_max: 180
```

---

## AWS Managed Prometheus (AMP)

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: https://aps-workspaces.<region>.amazonaws.com/workspaces/<workspace-id>/
            aws_region: us-east-1
            # aws_service_name: aps                    # Default: aps
            # aws_access_key: "{{ env.AWS_ACCESS_KEY_ID }}"
            # aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
            # assume_role_arn: "arn:aws:iam::123456789012:role/PrometheusReadRole"
            # refresh_interval_seconds: 900            # AWS credential refresh (default: 900)
            # verify_ssl: false                        # Default: false for AMP
            # additional_labels:                       # Labels added to all queries
            #     cluster: "production"
```

**Notes:**

- Automatically uses SigV4 authentication when `aws_region` is present
- Uses default AWS credential chain if keys not specified
- Supports cross-account access via `assume_role_arn`

---

## Azure Managed Prometheus

**Prerequisites:**

- An Azure Monitor workspace with Managed Prometheus enabled
- A service principal or managed identity with access to the workspace

**Environment variables:**

- `AZURE_CLIENT_ID`: Service principal client ID
- `AZURE_TENANT_ID`: Azure AD tenant ID
- `AZURE_CLIENT_SECRET`: Service principal secret
- `AZURE_USE_MANAGED_ID`: Set to `true` for managed identity auth

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: "https://<your-workspace>.<region>.prometheus.monitor.azure.com:443/"
            # azure_client_id: "{{ env.AZURE_CLIENT_ID }}"
            # azure_tenant_id: "{{ env.AZURE_TENANT_ID }}"
            # azure_client_secret: "{{ env.AZURE_CLIENT_SECRET }}"
            # azure_use_managed_id: false              # Use managed identity instead of service principal
            # refresh_interval_seconds: 900            # Token refresh interval (default: 900)
            # verify_ssl: true                         # SSL verification (default: true)
```

**Notes:**

- Authentication is handled automatically via Azure AD
- Some tools unavailable: `get_label_values`, `get_metric_metadata`, `list_prometheus_rules`
- Include trailing slash in `prometheus_url`

---

## Google Managed Prometheus

**Prerequisites:**

- Google Managed Prometheus enabled
- [Prometheus Frontend](https://cloud.google.com/stackdriver/docs/managed-prometheus/query-api-ui#ui-prometheus) deployed and accessible

```yaml-toolset-config
toolsets:
    prometheus/metrics:
        enabled: true
        config:
            prometheus_url: http://frontend.<namespace>.svc.cluster.local:9090
```

Authentication is automatic via Workload Identity or default service account.

---

## Grafana Cloud (Mimir)

There are two ways to connect HolmesGPT to Grafana Cloud's Prometheus/Mimir endpoint.

### Option 1: Direct Prometheus Endpoint (Recommended)

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
      --from-literal=auth-header="Basic $(echo -n 'INSTANCE_ID:CLOUD_ACCESS_POLICY_TOKEN' | base64)"
    ```

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
      --from-literal=auth-header="Basic $(echo -n 'INSTANCE_ID:CLOUD_ACCESS_POLICY_TOKEN' | base64)"
    ```

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
          config:
            prometheus_url: "https://prometheus-prod-XX-prod-REGION.grafana.net/api/prom"
            additional_headers:
              Authorization: "{{ env.GRAFANA_CLOUD_PROM_AUTH }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

### Option 2: Grafana API Proxy

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
      --from-literal=token=YOUR_GLSA_TOKEN
    ```

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
        config:
          prometheus_url: "https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID"
          additional_headers:
            Authorization: "Bearer {{ env.GRAFANA_CLOUD_SA_TOKEN }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your service account token:

    ```bash
    kubectl create secret generic grafana-cloud-sa-token \
      --from-literal=token=YOUR_GLSA_TOKEN
    ```

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
          config:
            prometheus_url: "https://YOUR-INSTANCE.grafana.net/api/datasources/proxy/uid/PROMETHEUS_DATASOURCE_UID"
            additional_headers:
              Authorization: "Bearer {{ env.GRAFANA_CLOUD_SA_TOKEN }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Connection refused | Verify URL is accessible from HolmesGPT |
| Authentication errors | Check `additional_headers` configuration |
| SSL certificate errors | Set `verify_ssl: false` (not recommended for production) |
| No metrics returned | Ensure Prometheus is scraping targets |
| Query timeouts | Increase `query_timeout_seconds_default` |
