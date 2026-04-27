# VictoriaLogs

Connect HolmesGPT to [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/) for log analysis. Provides search, stream discovery, and field-value enumeration over your log database using LogsQL.

## When to Use This

- ✅ You aggregate logs in VictoriaLogs
- ✅ You want HolmesGPT to query historical logs by stream label, field, or text
- ✅ You need to inspect log distributions over time (hits/buckets)

## Prerequisites

- A reachable VictoriaLogs HTTP endpoint (default port: `9428`)

--8<-- "snippets/toolsets_that_provide_logging.md"

## Configuration

```yaml-toolset-config
toolsets:
  victorialogs:
    enabled: true
    config:
      api_url: http://victorialogs.monitoring.svc:9428
```

### Authentication

VictoriaLogs supports basic authentication and bearer tokens.

**Basic auth:**

```yaml-toolset-config
toolsets:
  victorialogs:
    enabled: true
    config:
      api_url: https://victorialogs.example.com
      username: holmes
      password: "{{ env.VICTORIALOGS_PASSWORD }}"
```

**Bearer token:**

```yaml-toolset-config
toolsets:
  victorialogs:
    enabled: true
    config:
      api_url: https://victorialogs.example.com
      bearer_token: "{{ env.VICTORIALOGS_TOKEN }}"
```

### Multi-tenancy

VictoriaLogs uses `AccountID` and `ProjectID` headers for tenant routing. Set them via `headers`:

```yaml-toolset-config
toolsets:
  victorialogs:
    enabled: true
    config:
      api_url: https://victorialogs.example.com
      headers:
        AccountID: "0"
        ProjectID: "0"
```

### External URL for clickable links

If HolmesGPT calls an internal API URL but you want results to link to a public dashboard:

```yaml-toolset-config
toolsets:
  victorialogs:
    enabled: true
    config:
      api_url: http://victorialogs.internal:9428
      external_url: https://logs.example.com
```

## Common Use Cases

```text
Show error logs for the checkout service in the last 30 minutes
```

```text
Which services produced the most errors in the last hour?
```

```text
List the streams in namespace "payments" between 10:00 and 11:00 UTC
```
