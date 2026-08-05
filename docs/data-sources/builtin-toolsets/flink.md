# Apache Flink

Connect HolmesGPT to the Flink JobManager monitoring REST API.

## Prerequisites

- A reachable Flink JobManager REST endpoint

## Configuration

```yaml-toolset-config
toolsets:
  flink:
    enabled: true
    config:
      api_url: http://flink-jobmanager.streaming.svc:8081
      max_items: 100
```

For authenticated gateways, set `bearer_token`, basic-auth `username` and
`password`, or templated `extra_headers`.

## Multiple Instances

```multi-instance
toolset: flink
name: Apache Flink
config: |
  api_url: http://flink-jobmanager.streaming.svc:8081
```

## Common Use Cases

```text
Why did the latest Flink job fail?
```

```text
Check whether checkpoint duration or failed checkpoints explain the lag
```
