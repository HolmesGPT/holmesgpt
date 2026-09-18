# F5 Distributed Cloud

Connect HolmesGPT to [F5 Distributed Cloud (XC)](https://www.f5.com/products/distributed-cloud-services) to investigate WAF security events, bot defense, HTTP request logs, and load balancer configuration. Query which applications are under attack, why requests are being blocked, and whether origin servers are healthy.

## Prerequisites

- An F5 Distributed Cloud tenant (e.g. `https://your-tenant.console.ves.volterra.io`)
- An API token

## Creating an API Token

1. Log in to your F5 Distributed Cloud Console
2. Navigate to **Administration** > **Personal Management** > **Credentials**
3. Click **Add Credentials**, select **API Token** as the credential type, and set an expiry date
4. Copy the generated token - you'll use it as the `api_token` in the configuration below

For detailed instructions, see the [F5 Distributed Cloud Credentials documentation](https://docs.cloud.f5.com/docs-v2/administration/how-tos/user-mgmt/Credentials).

!!! important
    API requests inherit the RBAC of the user that created the token. Create the token from a user with a read-only (monitor) role - HolmesGPT only needs read access.

To verify your token:

```bash
curl -s "https://<your-tenant>.console.ves.volterra.io/api/web/namespaces" \
  -H "Authorization: APIToken <your-api-token>"
```

You should receive a JSON response listing your namespaces.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      f5xc:
        enabled: true
        config:
          api_url: <your tenant URL>  # e.g. https://acmecorp.console.ves.volterra.io
          api_token: <your API token>
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Which of my applications had WAF security events in the last 24 hours?"
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your F5 XC API token:

    ```bash
    kubectl create secret generic f5xc-credentials \
      --from-literal=api-token=your-f5xc-api-token \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: F5XC_API_TOKEN
        valueFrom:
          secretKeyRef:
            name: f5xc-credentials
            key: api-token

    toolsets:
      f5xc:
        enabled: true
        config:
          api_url: <your tenant URL>  # e.g. https://acmecorp.console.ves.volterra.io
          api_token: "{{ env.F5XC_API_TOKEN }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your F5 XC API token:

    ```bash
    kubectl create secret generic f5xc-credentials \
      --from-literal=api-token=your-f5xc-api-token \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: F5XC_API_TOKEN
          valueFrom:
            secretKeyRef:
              name: f5xc-credentials
              key: api-token
      toolsets:
        f5xc:
          enabled: true
          config:
            api_url: <your tenant URL>  # e.g. https://acmecorp.console.ves.volterra.io
            api_token: "{{ env.F5XC_API_TOKEN }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

### Optional Fields

| Option | Default | Description |
|--------|---------|-------------|
| `verify_ssl` | `true` | Whether to verify SSL certificates when calling the F5 XC API |
| `timeout_seconds` | `30` | Timeout in seconds for F5 XC API requests |
| `default_limit` | `100` | Default maximum number of events/logs returned by query tools (capped at 500, the API's per-page maximum) |

## Multiple Instances

```multi-instance
toolset: f5xc
name: F5 Distributed Cloud
config: |
  api_url: <your tenant URL>
  api_token: <your API token>
```

## Common Use Cases

```
Which of my applications had WAF security events in the last 24 hours?
```

```
Why are requests to app.example.com getting blocked?
```

```
Show me the top attacking IPs across all namespaces today
```

```
Are there 5xx errors on the checkout load balancer in the last hour?
```

## Advanced: HTTP Connector Alternative

The built-in toolset covers the most common troubleshooting endpoints. If you need access to other parts of the [F5 XC API](https://docs.cloud.f5.com/docs-v2/api) (e.g. DNS zones, CDN, sites), you can use an [HTTP connector](../api-toolsets.md) instead of - or alongside - the built-in toolset:

```yaml
toolsets:
  f5xc-api:
    type: http
    enabled: true
    config:
      endpoints:
        - hosts:
            - "https://*.console.ves.volterra.io"
          paths:
            - "/api/web/*"
            - "/api/config/*"
            - "/api/data/*"
          methods: ["GET", "POST"]  # POST is required for log/event query endpoints
          auth:
            type: header
            name: "Authorization"
            value: "APIToken {{ env.F5XC_API_TOKEN }}"
      verify_ssl: true
      timeout_seconds: 30
    llm_instructions: |
      ### F5 Distributed Cloud API
      The base URL is: {{ env.F5XC_TENANT_URL }}
      Key endpoints:
      - GET /api/web/namespaces - list namespaces
      - GET /api/config/namespaces/{namespace}/http_loadbalancers - list HTTP load balancers (add ?report_fields for full specs)
      - GET /api/config/namespaces/{namespace}/origin_pools - list origin pools
      - POST /api/data/namespaces/{namespace}/app_security/events - query WAF/bot/API security events.
        Body: {"namespace": "...", "query": "{sec_event_type=\"waf_sec_event\"}", "start_time": "<RFC3339>", "end_time": "<RFC3339>", "limit": 100, "sort": "DESCENDING", "aggs": {}}
      - POST /api/data/namespaces/{namespace}/access_logs - query HTTP request logs (same body; useful query labels: rsp_code_class, vh_name)
      IMPORTANT: the vh_name label is 'ves-io-http-loadbalancer-<lb-name>', not the plain load balancer name.
      The 'events'/'logs' arrays in responses contain JSON-encoded strings - parse them to read fields.
```

Set the environment variables before running HolmesGPT:

```bash
export F5XC_TENANT_URL="https://your-tenant.console.ves.volterra.io"
export F5XC_API_TOKEN="your-api-token"
```

Note that the HTTP connector exposes a single generic request tool; the built-in `f5xc` toolset provides curated tools with parameter validation, query-size limits, and better guidance for the LLM, so prefer it for the endpoints it covers.

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| f5xc_list_namespaces | List all namespaces in the tenant |
| f5xc_list_http_load_balancers | List HTTP load balancers in a namespace, optionally with full specs (domains, routes, WAF policy) |
| f5xc_get_http_load_balancer | Get the full configuration of a single HTTP load balancer |
| f5xc_list_origin_pools | List origin pools (backend server groups), optionally with origin servers and health checks |
| f5xc_query_security_events | Query WAF, bot defense, API security and service policy events, per namespace or tenant-wide |
| f5xc_aggregate_security_events | Count security events by field (top attack types, attacking IPs, targeted apps) |
| f5xc_query_request_logs | Query HTTP request (access) logs with response codes, paths, and timing breakdowns |
