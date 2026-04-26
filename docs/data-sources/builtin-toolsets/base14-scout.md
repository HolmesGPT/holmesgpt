# base14 Scout

Connect HolmesGPT to the base14 Scout observability platform for comprehensive access to services, traces, logs, metrics, and alerts via MCP.

## Quick Start

### 1. Get Your Credentials

You need your Scout MCP endpoint URL and either:

- **API Token**: A pre-obtained Bearer token
- **Client ID + Client Secret**: OAuth credentials for client_credentials grant

### 2. Configure HolmesGPT

=== "Holmes CLI"

    Set environment variables:
    ```bash
    export SCOUT_API_URL="https://your-scout-instance/mcp/v1"
    export SCOUT_CLIENT_ID="your-client-id"
    export SCOUT_CLIENT_SECRET="your-client-secret"
    ```

    Or add to your config file (`~/.holmes/config.yaml`):
    ```yaml
    toolsets:
      base14/scout:
        enabled: true
        config:
          api_url: "https://your-scout-instance/mcp/v1"
          client_id: "${SCOUT_CLIENT_ID}"
          client_secret: "${SCOUT_CLIENT_SECRET}"
    ```

=== "Token Auth"

    If you have a pre-obtained Bearer token:
    ```yaml
    toolsets:
      base14/scout:
        enabled: true
        config:
          api_url: "https://your-scout-instance/mcp/v1"
          api_token: "${SCOUT_API_TOKEN}"
    ```

### 3. Test It Works

```bash
holmes ask "What is the current system health?"
```

```bash
holmes ask "What services are available?"
```

```bash
holmes ask "Are there any firing alerts?"
```

## Configuration Reference

```yaml
toolsets:
  base14/scout:
    enabled: true
    config:
      # Required
      api_url: "https://your-scout-instance/mcp/v1"

      # Auth Option 1: Client credentials (recommended)
      client_id: "your-client-id"
      client_secret: "your-client-secret"

      # Auth Option 2: Direct token
      api_token: "your-bearer-token"

      # Optional
      verify_ssl: true          # SSL certificate verification (default: true)
      mode: "streamable-http"   # MCP connection mode (default: streamable-http)
```

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `SCOUT_API_URL` | Scout MCP endpoint URL |
| `SCOUT_CLIENT_ID` | OAuth client ID |
| `SCOUT_CLIENT_SECRET` | OAuth client secret |
| `SCOUT_API_TOKEN` | Pre-obtained Bearer token |

## Authentication

**Client credentials** (recommended): The toolset auto-discovers the OAuth token endpoint from Scout's `/.well-known/oauth-protected-resource` URL, then exchanges `client_id`/`client_secret` for a JWT via the `client_credentials` grant.

**Direct token**: If you already have a Bearer token, pass it via `api_token` and skip the OAuth flow.

## Available Tools

All tools are auto-discovered from the Scout MCP server. The following tools are typically available:

| Tool | Description |
|------|-------------|
| `get_system_health_summary` | Full system overview — alerts, services, and performance stats |
| `list_services` | Discover all known services |
| `get_service_topology` | Service-to-service dependency map |
| `get_service_dependencies` | Upstream/downstream dependencies for a specific service |
| `get_service_profile` | Error rates and latency stats per relationship |
| `get_service_metrics` | Metrics emitted by a service |
| `get_last_n_alerts` | Recent Grafana alert state transitions |
| `discover_spans` | Available span types and attributes for a service |
| `discover_logs` | Available log attributes and severity levels |
| `discover_metrics` | Available metrics, types, and dimensions |
| `query_traces` | Query traces with filtering by span name, status, attributes |
| `query_trace_by_id` | Full trace detail with all spans, events, and links |
| `query_logs` | Query logs with filtering by severity, body, attributes |
| `query_metrics` | Query metric values with aggregation and grouping |

## Common Use Cases

```bash
holmes ask "Investigate the firing alerts and find the root cause"
```

```bash
holmes ask "What is the error rate for the checkout service?"
```

```bash
holmes ask "Show me the service topology and find services with high latency"
```

```bash
holmes ask "Query error traces for the payment service in the last 15 minutes"
```

```bash
holmes ask "Find logs correlated with trace ID abc123"
```
