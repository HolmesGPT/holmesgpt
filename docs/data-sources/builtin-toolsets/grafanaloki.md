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
      url: https://xxxxxxx.grafana.net # Your Grafana cloud account URL
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
      url: http://loki.logging
      headers:
        X-Scope-OrgID: "<tenant id>" # Set the X-Scope-OrgID if loki multitenancy is enabled

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
```

## Advanced Configuration

### Search Labels

You can tweak the labels used by the toolset to identify Kubernetes resources. This is only needed if your Loki logs settings for `pod` and `namespace` differ from the defaults.

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      url: ...
      labels:
          pod: "pod"
          namespace: "namespace"
```

Use the following commands to list Loki's labels and determine which ones to use:

```bash
# Make Loki accessible locally
kubectl port-forward svc/loki 3100:3100

# List all labels. You may have to add the -H 'X-Scope-OrgID:<org id>' option with a valid org id
curl http://localhost:3100/loki/api/v1/labels
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_pod_logs | Fetches pod logs from Loki |
