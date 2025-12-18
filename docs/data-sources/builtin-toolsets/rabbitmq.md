# RabbitMQ

By enabling this toolset, HolmesGPT will be able to detect RabbitMQ partitions, memory alerts, and disk alerts and suggest mitigations.

This toolset follows a two-step process to detect partition:

1. The nodes and partitioning status is obtained by fetching information from the configured `management_url`.
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
          management_url: <http://rabbitmq.rabbitmq:15672>
```

## Advanced configuration

Below is the full list of options for this toolset:

```yaml
rabbitmq/core:
  enabled: true
  config:
    clusters:
      - id: rabbitmq # must be unique across all configured clusters
        username: <user>
        password: <password>
        management_url: <http://rabbitmq.rabbitmq:15672>
        request_timeout_seconds: 30 # timeout for HTTP requests
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| get_rabbitmq_cluster_status | Get cluster status and partition information |
| get_rabbitmq_node_info | Get detailed information about RabbitMQ nodes |
| get_rabbitmq_queue_info | Get information about queues |
| get_rabbitmq_exchange_info | Get information about exchanges |
| get_rabbitmq_memory_usage | Get memory usage statistics |
| get_rabbitmq_disk_usage | Get disk usage statistics |
