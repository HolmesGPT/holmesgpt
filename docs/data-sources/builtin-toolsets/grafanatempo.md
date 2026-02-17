# Tempo

By enabling this toolset, HolmesGPT will be able to fetch trace information from Tempo to debug performance related issues, like high latency in your application.

## Proxying through Grafana

This is the recommended approach because we intend to add more capabilities to the toolset that are only available with Grafana.

### Prerequisites

A [Grafana service account token](https://grafana.com/docs/grafana/latest/administration/service-accounts/) with the following permissions:

* Basic role -> Viewer
* Data sources -> Reader

Check out this [video](https://www.loom.com/share/f969ab3af509444693802254ab040791?sid=aa8b3c65-2696-4f69-ae47-bb96e8e03c47) on creating a Grafana service account token.

**Getting Grafana URL**

You can find the Grafana URL required for Tempo in your Grafana cloud account settings.

**Obtaining the datasource UID**

You may have multiple Tempo data sources set up in Grafana. HolmesGPT uses a single Tempo datasource to fetch the traces and it needs to know the UID of this datasource.

A simple way to get the datasource UID is to access the Grafana API by running the following request:

```bash
# port forward if you are using Robusta's Grafana from your Kubernetes cluster
kubectl port-forward svc/robusta-grafana 3000:80
# List the Tempo data sources
curl -s -u <username>:<password> http://localhost:3000/api/datasources | jq '.[] | select(.type == "tempo")'
```

This will return something like:

```json
{
    "id": 3,
    "uid": "klja8hsa-8a9c-4b35-1230-7baab22b02ee",
    "orgId": 1,
    "name": "Tempo",
    "type": "tempo",
    "typeName": "Tempo",
    "typeLogoUrl": "/public/app/plugins/datasource/tempo/img/tempo_icon.svg",
    "access": "proxy",
    "url": "http://tempo-query-frontend.tempo:3100",
    "user": "",
    "database": "",
    "basicAuth": false,
    "isDefault": false,
    "jsonData": {
        "tlsSkipVerify": true
    },
    "readOnly": false
}
```

In this case, the Tempo datasource UID is `klja8hsa-8a9c-4b35-1230-7baab22b02ee`.

### Configuration (Grafana Proxy)

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_key: <your grafana service account token>
          api_url: <your grafana url> # e.g. https://acme-corp.grafana.net
          grafana_datasource_uid: <the UID of the tempo data source in Grafana>
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "The payments DB is very slow, check tempo for any trace data"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        grafana/tempo:
          enabled: true
          config:
            api_key: <your grafana API key>
            api_url: <your grafana url> # e.g. https://acme-corp.grafana.net
            grafana_datasource_uid: <the UID of the tempo data source in Grafana>
    ```

## Direct Connection

The toolset can directly connect to a Tempo instance without proxying through a Grafana instance. This is done by not setting the `grafana_datasource_uid` field. Not setting this field makes HolmesGPT assume that it is directly connecting to Tempo.

### Configuration (Direct Connection)

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      grafana/tempo:
        enabled: true
        config:
          api_url: http://tempo.monitoring
          additional_headers:
            X-Scope-OrgID: "<tenant id>" # Set the X-Scope-OrgID if tempo multitenancy is enabled
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        grafana/tempo:
          enabled: true
          config:
            api_url: http://tempo.monitoring
            additional_headers:
              X-Scope-OrgID: "<tenant id>" # Set the X-Scope-OrgID if tempo multitenancy is enabled
    ```

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

If HolmesGPT accesses Tempo through an internal URL but you want clickable links in results to use a different URL:

```yaml
toolsets:
  grafana/tempo:
    enabled: true
    config:
      api_url: http://tempo.internal:3100  # Internal URL for API calls
      external_url: https://tempo.example.com  # URL for links in results
      grafana_datasource_uid: <tempo datasource uid>
```

### Custom Label Mappings

Tempo uses resource attributes to identify Kubernetes resources. If your setup uses different attribute names, you can customize the mappings:

```yaml
toolsets:
  grafana/tempo:
    enabled: true
    config:
      api_url: https://grafana.example.com
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
homles ask "Find traces where the payment service is taking longer than 1 second"
```

Holmes will use TraceQL to search for slow operations:
```
{resource.service.name="payment" && duration > 1s}
```

### Analyzing Errors

```bash
homles ask "Show me traces with HTTP 500 errors in the frontend service"
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
