# Trino

Connect HolmesGPT to a Trino coordinator for bounded, read-only SQL diagnostics.

## Prerequisites

- A reachable Trino coordinator
- A Trino user with access to the catalogs and schemas Holmes should inspect

## Configuration

```yaml-toolset-config
toolsets:
  trino:
    enabled: true
    config:
      api_url: https://trino.example.com
      trino_user: holmes
      catalog: system
      schema: runtime
      max_rows: 200
      bearer_token: "{{ env.TRINO_TOKEN }}"
```

Use `username` and `password` instead of `bearer_token` when the endpoint uses
HTTP basic authentication. `extra_headers` can supply proxy or gateway headers.

## Multiple Instances

```multi-instance
toolset: trino
name: Trino
config: |
  api_url: https://trino.example.com
  trino_user: holmes
```

## Common Use Cases

```text
Show failed Trino queries from the last hour and summarize their error types
```

```text
Explain the query plan for this SELECT without running it
```
