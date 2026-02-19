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

Choose one of the following methods:

### Option 1: Through Grafana (Recommended)

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

### Configuration (Grafana Proxy)

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_key: <your grafana API key>
      api_url: https://xxxxxxx.grafana.net # Your Grafana cloud account URL
      grafana_datasource_uid: <the UID of the loki data source in Grafana>

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
```

## Direct Connection

The toolset can directly connect to a Loki instance without proxying through a Grafana instance. This is done by not setting the `grafana_datasource_uid` field. Not setting this field makes HolmesGPT assume that it is directly connecting to Loki.

### Configuration (Direct Connection)

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.logging
      additional_headers:
        X-Scope-OrgID: "<tenant id>" # Set the X-Scope-OrgID if loki multitenancy is enabled

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
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

If HolmesGPT accesses Loki through an internal URL but you want clickable links in results to use a different URL:

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.internal:3100  # Internal URL for API calls
      external_url: https://loki.example.com  # URL for links in results
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_pod_logs | Fetches pod logs from Loki |
