# Loki

Connect HolmesGPT to Loki for log analysis through Grafana or direct API access. Provides access to historical logs and advanced log queries.

## When to Use This

- ✅ Your Kubernetes logs are centralized in Loki
- ✅ You need historical log data beyond what's in pods
- ✅ You want advanced log search capabilities

## Prerequisites

- Loki instance with logs from your Kubernetes cluster
- Grafana with Loki datasource configured (recommended) OR direct Loki API access

--8<-- "snippets/toolsets_that_provide_logging.md"

## Configuration

HolmesGPT supports three ways to connect to Loki. Pick the one that matches your setup:

| Setup | When to use |
|-------|-------------|
| [Self-Hosted Loki via Grafana Proxy](#self-hosted-loki-via-grafana-proxy) (recommended) | You run your own Grafana with a Loki datasource configured |
| [Self-Hosted Loki - Direct Connection](#self-hosted-loki-direct-connection) | Self-hosted Loki without Grafana, including multi-tenant setups needing `X-Scope-OrgID` |
| [Grafana Cloud](#grafana-cloud) | Your Grafana Cloud stack (queries Loki via your Grafana Cloud Grafana) |

### Self-Hosted Loki via Grafana Proxy

HolmesGPT queries your self-hosted Loki through your Grafana instance's datasource proxy. Recommended when you already have Grafana — it handles authentication and you only need one API key. This is also the only mode that produces clickable "View in Grafana" links in Holmes's responses.

**Required:**

- [Grafana service account token](https://grafana.com/docs/grafana/latest/administration/service-accounts/) with Viewer role
- Loki datasource UID from Grafana

**Find your Loki datasource UID:**

```bash
# Port forward to Grafana
kubectl port-forward svc/grafana 3000:80

# Get Loki datasource UID
curl -s -u admin:admin http://localhost:3000/api/datasources | jq '.[] | select(.type == "loki") | .uid'
```

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://grafana.monitoring.svc.cluster.local  # Your Grafana URL
      api_key: <your grafana API key>
      grafana_datasource_uid: <the UID of the loki data source in Grafana>
```

### Self-Hosted Loki - Direct Connection

HolmesGPT connects directly to a self-hosted Loki API endpoint without going through Grafana.

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.monitoring.svc.cluster.local:3100
      additional_headers:
        X-Scope-OrgID: "<tenant id>"  # Only needed for multi-tenant Loki
```

### Grafana Cloud

Query Loki through your Grafana Cloud Grafana instance's datasource proxy. Same flow as the self-hosted proxy option, just pointed at your Grafana Cloud URL.

**Required:**

- Your Grafana Cloud Grafana URL (e.g., `https://myorg.grafana.net`)
- A Grafana Cloud service account token with Viewer role
- Loki datasource UID from your Grafana Cloud Grafana

**Find your Loki datasource UID:**

In your Grafana Cloud Grafana UI → Connections → Data sources → click on the Loki datasource. The UID appears in the URL. Or via the API:

```bash
curl -s -H "Authorization: Bearer <service-account-token>" https://<your-stack>.grafana.net/api/datasources | jq '.[] | select(.type == "loki") | .uid'
```

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: https://<your-stack>.grafana.net
      api_key: <grafana cloud service account token>
      grafana_datasource_uid: <the UID of the Loki datasource>
```

## Advanced Configuration

### SSL Verification

For self-signed certificates, you can disable SSL verification:

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: https://loki.internal
      verify_ssl: false  # Disable SSL verification (default: true)
```

### External URL

Only applies to the **Self-Hosted Loki via Grafana Proxy** setup. If HolmesGPT reaches Grafana through an internal URL but you want the clickable "View in Grafana" links in responses to use a public URL:

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://grafana.monitoring.svc.cluster.local  # Internal URL for API calls
      api_key: <your grafana API key>
      grafana_datasource_uid: <loki datasource UID>
      external_url: https://grafana.example.com  # URL used in clickable links
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_pod_logs | Fetches pod logs from Loki |
