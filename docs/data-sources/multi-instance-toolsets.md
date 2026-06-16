# Multiple Instances

Many built-in toolsets can connect to **more than one instance** of the same system — for example two Grafana stacks (prod and staging), several Elasticsearch clusters, or per-team Datadog accounts. HolmesGPT queries the right one during an investigation, and you can ask it to compare across them.

This page describes the shared behaviour. Each toolset's own page shows a ready-to-copy example in its **Multiple Instances** section.

## Configuring instances

A toolset that supports multiple instances accepts either a single flat config (the original format) **or** a list of `instances:`, each with a unique `name`:

=== "Single instance (flat)"

    ```yaml
    toolsets:
      grafana/dashboards:
        enabled: true
        config:
          api_url: https://prod-grafana.example.com
          api_key: <your api key>
    ```

=== "Multiple instances"

    ```yaml
    toolsets:
      grafana/dashboards:
        enabled: true
        config:
          instances:
            - name: prod
              api_url: https://prod-grafana.example.com
              api_key: <prod api key>
            - name: staging
              api_url: https://staging-grafana.example.com
              api_key: <staging api key>
    ```

The fields allowed inside each `instances:` entry are exactly the toolset's normal config fields — see the toolset's own page for the full list.

## Shared defaults

Any config field set **outside** `instances:` (at the top level of `config:`) becomes a default that every instance inherits unless the instance overrides it. This keeps settings that are common to all instances in one place:

```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:
      verify_ssl: false          # inherited by every instance below
      instances:
        - name: prod
          api_url: https://prod-grafana.example.com
          api_key: <prod api key>
        - name: staging
          api_url: https://staging-grafana.example.com
          api_key: <staging api key>
          verify_ssl: true        # overrides the default for this instance only
```

Credentials are kept per-instance: a field group like `api_key` / `username` / `password` is only inherited from the top level when an instance provides none of them, so one instance's credentials never leak into another.

## The `instance` parameter

When **more than one** instance is configured, HolmesGPT adds an `instance` parameter to every tool of that toolset. The model chooses which instance to query and HolmesGPT routes the call to that instance's connection and credentials. You can steer it in your question:

```bash
holmes ask "Check the prod Grafana for dashboards tagged 'kubernetes'"
```

With a **single** instance — whether configured flat or as a one-entry `instances:` list — no `instance` parameter is added and the tools behave exactly as before.

## Discovering instances

When more than one instance is configured, HolmesGPT also exposes a `<toolset>_list_instances` tool (for example `grafana_dashboards_list_instances`) so the model can discover the configured instance names and their health before querying.

## Health reporting

Each instance is health-checked independently when the toolset loads. The toolset is considered available if **at least one** instance is healthy; instances that fail are reported individually so a single misconfigured or unreachable instance doesn't disable the others.

## Backwards compatibility

Existing single-instance configs keep working unchanged — `instances:` is purely additive. Upgrading to multiple instances only requires moving your existing fields into an `instances:` entry with a `name`.

## Toolsets that support multiple instances

- [Elasticsearch](builtin-toolsets/elasticsearch.md) (data and cluster)
- [Grafana Dashboards](builtin-toolsets/grafanadashboards.md), [Grafana Loki](builtin-toolsets/grafanaloki.md), [Grafana Tempo](builtin-toolsets/grafanatempo.md)
- [Prometheus](builtin-toolsets/prometheus.md)
- [Datadog](builtin-toolsets/datadog.md) (logs, metrics, traces, general)
- [Coralogix](builtin-toolsets/coralogix-logs.md)
- [VictoriaLogs](builtin-toolsets/victorialogs.md)
- [New Relic](builtin-toolsets/newrelic.md)
- [Azure SQL](builtin-toolsets/azure-sql.md)
- [MongoDB Atlas](builtin-toolsets/mongodb-atlas.md)
- [ServiceNow](builtin-toolsets/servicenow.md)
- [Confluence](builtin-toolsets/confluence.md)

!!! note "RabbitMQ and Kafka"

    The [RabbitMQ](builtin-toolsets/rabbitmq.md) and [Kafka](builtin-toolsets/kafka.md) toolsets have their own multi-cluster configuration using a `clusters:` list (with a `cluster_id` / `kafka_cluster_name` tool parameter) rather than the generic `instances:` format described here.
