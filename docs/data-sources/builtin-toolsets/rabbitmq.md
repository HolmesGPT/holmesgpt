# RabbitMQ

By enabling this toolset, HolmesGPT will be able to detect RabbitMQ partitions, memory alerts, and disk alerts and suggest mitigations.

This toolset follows a two-step process to detect partition:

1. The nodes and partitioning status is obtained by fetching information from the configured `api_url`.
2. If some nodes are reported as not-running, the toolset will try to contact these nodes individually and deduce any partitioning state for any node that is actually running.

## Configuration

```yaml-toolset-config
toolsets:
  rabbitmq/core:
    enabled: true
    config:
      clusters:
        - id: rabbitmq # must be unique across all configured clusters
          username: <user>
          password: <password>
          api_url: <http://rabbitmq.rabbitmq:15672>
```

## Advanced Configuration

Below is the full list of options for this toolset:

```yaml
rabbitmq/core:
  enabled: true
  config:
    clusters:
      - id: rabbitmq # must be unique across all configured clusters
        username: <user>
        password: <password>
        api_url: <http://rabbitmq.rabbitmq:15672>
        timeout_seconds: 30 # timeout for HTTP requests
        verify_ssl: true # SSL certificate verification (default: true)
```

### SSL Verification

For self-signed certificates, you can disable SSL verification:

```yaml
toolsets:
  rabbitmq/core:
    enabled: true
    config:
      clusters:
        - id: rabbitmq
          api_url: https://rabbitmq.internal:15672
          username: <user>
          password: <password>
          verify_ssl: false  # Disable SSL verification (default: true)
```

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| list_configured_clusters | List all configured RabbitMQ clusters and their IDs |
| get_rabbitmq_cluster_status | Fetch overall cluster status including nodes, listeners, and partitions (split-brain detection) |
