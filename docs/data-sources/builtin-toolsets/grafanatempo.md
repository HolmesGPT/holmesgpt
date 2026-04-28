# Tempo

Connect HolmesGPT to Tempo for distributed trace analysis. Useful for diagnosing performance issues like high latency, slow operations, and request failures across microservices.

## When to Use This

- ✅ Your applications emit distributed traces to Tempo
- ✅ You need to debug latency or identify slow operations
- ✅ You want to correlate errors with specific traces

## Prerequisites

- Tempo instance receiving traces from your applications
- Grafana with a Tempo datasource configured (recommended) OR direct Tempo API access

## Configuration

HolmesGPT supports three ways to connect to Tempo. Pick the one that matches your setup:

| Setup | When to use |
|-------|-------------|
| [Self-Hosted Tempo via Grafana Proxy](#self-hosted-tempo-via-grafana-proxy) (recommended) | You run your own Grafana with a Tempo datasource configured |
| [Self-Hosted Tempo - Direct Connection](#self-hosted-tempo-direct-connection) | Self-hosted Tempo without Grafana, including multi-tenant setups needing `X-Scope-OrgID` |
| [Grafana Cloud](#grafana-cloud) | Your Grafana Cloud stack (queries Tempo via your Grafana Cloud Grafana) |

### Self-Hosted Tempo via Grafana Proxy

HolmesGPT queries your self-hosted Tempo through your Grafana instance's datasource proxy. Recommended when you already have Grafana — it handles authentication and you only need one API key. This is also the only mode that produces clickable "View in Grafana" links in Holmes's responses.

**Required:**

- A [Grafana service account token](https://grafana.com/docs/grafana/latest/administration/service-accounts/) with:
    - Basic role → Viewer
    - Data sources → Reader
- Tempo datasource UID from Grafana

See this [video](https://www.loom.com/share/f969ab3af509444693802254ab040791?sid=aa8b3c65-2696-4f69-ae47-bb96e8e03c47) for a walkthrough of creating the service account token.

**Find your Tempo datasource UID:**

```bash
# Port forward to Grafana
kubectl port-forward svc/robusta-grafana 3000:80

# Get Tempo datasource UID
curl -s -u <username>:<password> http://localhost:3000/api/datasources | jq '.[] | select(.type == "tempo") | .uid'
```

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: <your grafana url>  # e.g. http://grafana.monitoring.svc.cluster.local
          api_key: <your grafana service account token>
          grafana_datasource_uid: <the UID of the tempo data source in Grafana>
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "The payments DB is very slow, check tempo for any trace data"
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Grafana service account token:

    ```bash
    kubectl create secret generic grafana-tempo-api-key \
      --from-literal=api-key=your-grafana-service-account-token \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: GRAFANA_TEMPO_API_KEY
        valueFrom:
          secretKeyRef:
            name: grafana-tempo-api-key
            key: api-key

    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: <your grafana url>  # e.g. http://grafana.monitoring.svc.cluster.local
          api_key: "{{ env.GRAFANA_TEMPO_API_KEY }}"
          grafana_datasource_uid: <the UID of the tempo data source in Grafana>
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Grafana service account token:

    ```bash
    kubectl create secret generic grafana-tempo-api-key \
      --from-literal=api-key=your-grafana-service-account-token \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: GRAFANA_TEMPO_API_KEY
          valueFrom:
            secretKeyRef:
              name: grafana-tempo-api-key
              key: api-key
      toolsets:
        grafana/tempo:
          enabled: true
          config:
            api_url: <your grafana url>  # e.g. http://grafana.monitoring.svc.cluster.local
            api_key: "{{ env.GRAFANA_TEMPO_API_KEY }}"
            grafana_datasource_uid: <the UID of the tempo data source in Grafana>
    ```

    --8<-- "snippets/helm_upgrade_command.md"

### Self-Hosted Tempo - Direct Connection

HolmesGPT connects directly to a self-hosted Tempo API endpoint without going through Grafana.

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: http://tempo.monitoring.svc.cluster.local:3200
          additional_headers:
            X-Scope-OrgID: "<tenant id>"  # Only needed for multi-tenant Tempo
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    No Kubernetes secret is needed in this mode — direct Tempo connections don't carry an API key.

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: http://tempo.monitoring.svc.cluster.local:3200
          additional_headers:
            X-Scope-OrgID: "<tenant id>"  # Only needed for multi-tenant Tempo
    ```

=== "Robusta Helm Chart"

    No Kubernetes secret is needed in this mode — direct Tempo connections don't carry an API key.

    ```yaml
    holmes:
      toolsets:
        grafana/tempo:
          enabled: true
          config:
            api_url: http://tempo.monitoring.svc.cluster.local:3200
            additional_headers:
              X-Scope-OrgID: "<tenant id>"  # Only needed for multi-tenant Tempo
    ```

### Grafana Cloud

Query Tempo through your Grafana Cloud Grafana instance's datasource proxy. Same flow as the self-hosted proxy option, just pointed at your Grafana Cloud URL.

**Required:**

- Your Grafana Cloud Grafana URL (e.g., `https://myorg.grafana.net`)
- A Grafana Cloud service account token with:
    - Basic role → Viewer
    - Data sources → Reader
- Tempo datasource UID from your Grafana Cloud Grafana

**Find your Tempo datasource UID:**

In your Grafana Cloud Grafana UI → Connections → Data sources → click on the Tempo datasource. The UID appears in the URL. Or via the API:

```bash
curl -s -H "Authorization: Bearer <service-account-token>" https://<your-stack>.grafana.net/api/datasources | jq '.[] | select(.type == "tempo") | .uid'
```

=== "Holmes CLI"

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: https://<your-stack>.grafana.net
          api_key: <grafana cloud service account token>
          grafana_datasource_uid: <the UID of the Tempo datasource>
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Grafana Cloud service account token:

    ```bash
    kubectl create secret generic grafana-cloud-tempo-api-key \
      --from-literal=api-key=your-grafana-cloud-service-account-token \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: GRAFANA_CLOUD_TEMPO_API_KEY
        valueFrom:
          secretKeyRef:
            name: grafana-cloud-tempo-api-key
            key: api-key

    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: https://<your-stack>.grafana.net
          api_key: "{{ env.GRAFANA_CLOUD_TEMPO_API_KEY }}"
          grafana_datasource_uid: <the UID of the Tempo datasource>
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Grafana Cloud service account token:

    ```bash
    kubectl create secret generic grafana-cloud-tempo-api-key \
      --from-literal=api-key=your-grafana-cloud-service-account-token \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: GRAFANA_CLOUD_TEMPO_API_KEY
          valueFrom:
            secretKeyRef:
              name: grafana-cloud-tempo-api-key
              key: api-key
      toolsets:
        grafana/tempo:
          enabled: true
          config:
            api_url: https://<your-stack>.grafana.net
            api_key: "{{ env.GRAFANA_CLOUD_TEMPO_API_KEY }}"
            grafana_datasource_uid: <the UID of the Tempo datasource>
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Advanced Configuration

### SSL Verification

For self-signed certificates, you can disable SSL verification:

```yaml
toolsets:
  grafana/tempo:
    enabled: true
    config:
      api_url: https://tempo.internal
      verify_ssl: false  # Disable SSL verification (default: true)
```

### External URL

Only applies to the **Self-Hosted Tempo via Grafana Proxy** setup. If HolmesGPT reaches Grafana through an internal URL but you want the clickable "View in Grafana" links in responses to use a public URL:

```yaml
toolsets:
  grafana/tempo:
    enabled: true
    config:
      api_url: http://grafana.monitoring.svc.cluster.local  # Internal URL for API calls
      api_key: <your grafana API key>
      grafana_datasource_uid: <tempo datasource uid>
      external_url: https://grafana.example.com  # URL used in clickable links
```

### Custom Label Mappings

Tempo uses resource attributes to identify Kubernetes resources. If your setup uses non-default attribute names, you can customize the mappings:

```yaml
toolsets:
  grafana/tempo:
    enabled: true
    config:
      api_url: https://grafana.example.com
      api_key: <your grafana API key>
      grafana_datasource_uid: <tempo datasource uid>
      labels:
        pod: "k8s.pod.name"           # default
        namespace: "k8s.namespace.name"  # default
        deployment: "k8s.deployment.name"  # default
        node: "k8s.node.name"         # default
        service: "service.name"       # default
```

## Example Usage

### Finding Slow Traces

```bash
holmes ask "Find traces where the payment service is taking longer than 1 second"
```

Holmes will use TraceQL to search for slow operations:

```
{resource.service.name="payment" && duration > 1s}
```

### Analyzing Errors

```bash
holmes ask "Show me traces with HTTP 500 errors in the frontend service"
```

Holmes will search using:

```
{resource.service.name="frontend" && span.http.status_code = 500}
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| tempo_fetch_traces_comparative_sample | Fetches statistics and samples of fast/slow/typical traces for performance analysis |
| tempo_search_traces_by_query | Search traces using TraceQL query language (recommended) |
| tempo_search_traces_by_tags | Search traces using logfmt-encoded tags (legacy) |
| tempo_query_trace_by_id | Retrieve detailed trace information by trace ID |
| tempo_search_tag_names | Discover available tag names across traces |
| tempo_search_tag_values | Get all values for a specific tag |
| tempo_query_metrics_instant | Compute a single TraceQL metric value across time range |
| tempo_query_metrics_range | Get time series data from TraceQL metrics queries |
