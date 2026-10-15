# Elasticsearch / OpenSearch

By enabling these toolsets, HolmesGPT can query Elasticsearch and OpenSearch clusters to investigate issues, search logs, analyze cluster health, and more.

These toolsets work with both **Elasticsearch** (including Elastic Cloud) and **OpenSearch** since they share the same REST API.

## Two Toolsets

HolmesGPT provides two separate Elasticsearch toolsets with different permission requirements:

| Toolset | Description | Permissions Required |
|---------|-------------|---------------------|
| `elasticsearch/data` | Search logs, metrics, and documents | Index-level read access |
| `elasticsearch/cluster` | Troubleshoot cluster health issues | Cluster-level monitor access |

Enable only the toolset(s) you need. Most users who just want to search logs only need `elasticsearch/data`.

## Configuration

```yaml-toolset-config
toolsets:
  elasticsearch/data:
    enabled: true
    config:
      api_url: "{{ env.ELASTICSEARCH_URL }}"
      api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
      # Alternative: use basic auth instead of api_key
      # username: "elastic"
      # password: "your-password"
  elasticsearch/cluster:
    enabled: true
    config:
      api_url: "{{ env.ELASTICSEARCH_URL }}"
      api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
      # Alternative: use basic auth instead of api_key
      # username: "elastic"
      # password: "your-password"
---
secret:
  - --from-literal=ELASTICSEARCH_URL="https://your-cluster.es.cloud.io:443"
  - --from-literal=ELASTICSEARCH_API_KEY=your-api-key
```

!!! tip "Enable only what you need"
    You can enable just `elasticsearch/data` or `elasticsearch/cluster` depending on your needs. Most users who just want to search logs only need `elasticsearch/data`.

## Multiple Instances

```multi-instance
toolset: elasticsearch/data
name: Elasticsearch
config: |
  api_url: "https://your-cluster.es.cloud.io:443"
  api_key: "your-api-key"
```

## Authentication

The toolsets support multiple authentication methods:

| Method | Config Fields | Description |
|--------|--------------|-------------|
| API Key | `api_key` | Recommended for Elastic Cloud |
| Basic Auth | `username`, `password` | Username and password |
| mTLS | `client_cert`, `client_key` | Client certificate authentication (e.g., OpenShift Jaeger operator) |
| None | - | For clusters without authentication |

### mTLS (Mutual TLS)

For Elasticsearch clusters that require client certificate authentication (common with the OpenShift Jaeger operator), configure the certificate paths:

In Kubernetes, the certificates are mounted into the Holmes container from a secret, using `additionalVolumes` and `additionalVolumeMounts`.

```yaml-toolset-config
additionalEnvVars:
  - name: ELASTICSEARCH_URL
    value: "https://elasticsearch.jaeger.svc:9200"

additionalVolumes:
  - name: es-certs
    secret:
      secretName: holmes-elasticsearch-mtls

additionalVolumeMounts:
  - name: es-certs
    mountPath: /etc/elasticsearch/certs
    readOnly: true

toolsets:
  elasticsearch/data:
    enabled: true
    config:
      api_url: "{{ env.ELASTICSEARCH_URL }}"
      client_cert: "/etc/elasticsearch/certs/tls.crt"
      client_key: "/etc/elasticsearch/certs/tls.key"
---
named-secrets:
  - name: holmes-elasticsearch-mtls
    keys:
      - --from-file=tls.crt=/path/to/client.crt
      - --from-file=tls.key=/path/to/client.key
cli: |
  ```yaml
  toolsets:
    elasticsearch/data:
      enabled: true
      config:
        api_url: "https://elasticsearch.jaeger.svc:9200"
        client_cert: "/path/to/client.crt"
        client_key: "/path/to/client.key"
  ```
```

If Elasticsearch uses a private CA, use the chart's global [`certificate`](../../reference/helm-configuration.md) value (or `CERTIFICATE` env var for CLI) to trust it. This applies to all outbound HTTPS requests, not just Elasticsearch. See [Environment Variables](../../reference/environment-variables.md#certificate) for details.

### Other Options

| Option | Default | Description |
|--------|---------|-------------|
| `verify_ssl` | `true` | Verify SSL certificates. For custom CAs, use the global `CERTIFICATE` env var instead. |
| `timeout_seconds` | `10` | Request timeout in seconds |

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Toolset | Tool Name | Description |
|---------|-----------|-------------|
| `elasticsearch/data` | elasticsearch_search | Search documents using Elasticsearch Query DSL |
| `elasticsearch/data` | elasticsearch_mappings | Get field mappings for an index |
| `elasticsearch/data` | elasticsearch_list_indices | List indices matching a pattern |
| `elasticsearch/cluster` | elasticsearch_cat | Query _cat APIs (indices, shards, nodes, etc.) |
| `elasticsearch/cluster` | elasticsearch_cluster_health | Get cluster health status |
| `elasticsearch/cluster` | elasticsearch_allocation_explain | Explain shard allocation decisions |
| `elasticsearch/cluster` | elasticsearch_nodes_stats | Get node-level statistics |
| `elasticsearch/cluster` | elasticsearch_index_stats | Get statistics for an index |

## Example Queries

- "Search for ERROR logs in the application-logs index from the last hour"
- "What are the field mappings for the metrics index?"
- "List all indices starting with 'logs-'"
- "What is the cluster health status?"
- "Why are shards unassigned?"
- "Which nodes have high disk usage?"
- "Show me the shards for the logs-* indices"

## OpenSearch Compatibility

These toolsets are fully compatible with OpenSearch clusters. Simply point the `api_url` to your OpenSearch endpoint:

```yaml-toolset-config
toolsets:
  elasticsearch/data:
    enabled: true
    config:
      api_url: "https://your-opensearch-cluster:9200"
      username: "admin"
      password: "your-password"
      verify_ssl: true
```
