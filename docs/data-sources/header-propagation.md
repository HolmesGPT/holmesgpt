# HTTP Header Propagation

When running HolmesGPT as a server, HTTP headers from incoming requests can be forwarded to toolsets when they make outgoing API calls. This is useful for passing per-request authentication tokens, tenant identifiers, or other contextual headers through to backend services.

Header propagation is supported across all toolset types: [MCP servers](remote-mcp-servers.md), [HTTP connectors](api-toolsets.md), [custom (YAML) toolsets](custom-toolsets.md), and built-in Python toolsets.

!!! note
    Header propagation is only available when running Holmes as a server (Helm deployment). It does not apply when using the CLI directly.

## How It Works

1. A client sends an HTTP request to the Holmes server (e.g., `/api/investigate`)
2. Holmes extracts non-sensitive headers from the request (blocking `Authorization`, `Cookie`, and `Set-Cookie` by default)
3. The extracted headers are available as `request_context` during tool execution
4. Toolsets that opt in render those templates using the request context and forward the resulting values to their tools at invocation time. Toolsets without the config key are unaffected.

## Configuration

The config key is placed inside the `config` section of each toolset and accepts a dictionary of names mapped to [Jinja2](https://jinja.palletsprojects.com/) template strings:

- **`extra_headers`** -- for MCP servers, HTTP connectors, and Python toolsets (values become HTTP headers)
- For custom (YAML) toolsets, `request_context` and `env` are available directly in Jinja2 command/script templates — no extra config key needed

Templates can reference:

- **`{{ request_context.headers['Header-Name'] }}`** -- a header from the incoming HTTP request (case-insensitive lookup)
- **`{{ env.ENV_VAR }}`** -- an environment variable
- **Plain strings** -- static values that don't need rendering

## Toolset Examples

### MCP Servers

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      customer_data:
        description: "Customer data API"
        config:
          url: "http://customer-api:8000/mcp"
          mode: streamable-http
          extra_headers:
            X-Tenant-Id: "{{ request_context.headers['X-Tenant-Id'] }}"
            X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
    ```

=== "Holmes Helm Chart"

    Add to your Holmes Helm values:

    ```yaml
    mcp_servers:
      customer_data:
        description: "Customer data API"
        config:
          url: "http://customer-api:8000/mcp"
          mode: streamable-http
          extra_headers:
            X-Tenant-Id: "{{ request_context.headers['X-Tenant-Id'] }}"
            X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcp_servers:
        customer_data:
          description: "Customer data API"
          config:
            url: "http://customer-api:8000/mcp"
            mode: streamable-http
            extra_headers:
              X-Tenant-Id: "{{ request_context.headers['X-Tenant-Id'] }}"
              X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
    ```

See [MCP Servers -- Dynamic Headers](remote-mcp-servers.md#advanced-configuration) for the full MCP configuration reference.

### HTTP Connectors

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    toolsets:
      internal-api:
        type: http
        enabled: true
        config:
          extra_headers:
            X-Request-Id: "{{ request_context.headers['X-Request-Id'] }}"
            X-Api-Key: "{{ env.INTERNAL_API_KEY }}"
          endpoints:
            - hosts: ["internal-api.corp.net"]
              methods: ["GET"]
    ```

=== "Holmes Helm Chart"

    Add to your Holmes Helm values:

    ```yaml
    toolsets:
      internal-api:
        type: http
        enabled: true
        config:
          extra_headers:
            X-Request-Id: "{{ request_context.headers['X-Request-Id'] }}"
            X-Api-Key: "{{ env.INTERNAL_API_KEY }}"
          endpoints:
            - hosts: ["internal-api.corp.net"]
              methods: ["GET"]
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      toolsets:
        internal-api:
          type: http
          enabled: true
          config:
            extra_headers:
              X-Request-Id: "{{ request_context.headers['X-Request-Id'] }}"
              X-Api-Key: "{{ env.INTERNAL_API_KEY }}"
            endpoints:
              - hosts: ["internal-api.corp.net"]
                methods: ["GET"]
    ```

The rendered headers are merged into every outgoing request after the endpoint's own authentication headers, so they can override defaults when needed.

See [HTTP Connectors](api-toolsets.md) for the full HTTP connector configuration reference.

### Custom (YAML) Toolsets

YAML tool commands and scripts are Jinja2 templates. The variables `request_context` and `env` are available directly — no extra config key needed. Use `request_context.headers['Header-Name']` to access incoming request headers and `env.VAR_NAME` to access environment variables.

=== "Holmes CLI"

    **Configuration File (`toolsets.yaml`):**

    ```yaml
    toolsets:
      internal-api:
        name: "internal-api"
        tools:
          - name: "fetch_data"
            description: "Fetch data from internal API"
            command: 'curl -s -H "X-Auth-Token: {{ request_context.headers[''X-Auth-Token''] }}" https://internal-api.corp.net/data'
    ```

    ```bash
    holmes ask "fetch data from the internal API" --custom-toolsets=toolsets.yaml
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      customToolsets:
        internal-api:
          name: "internal-api"
          tools:
            - name: "fetch_data"
              description: "Fetch data from internal API"
              command: 'curl -s -H "X-Auth-Token: {{ request_context.headers[''X-Auth-Token''] }}" https://internal-api.corp.net/data'
    ```

See [Custom Toolsets](custom-toolsets.md) for the full YAML toolset reference.

### Built-in Python Toolsets

Each built-in Python toolset decides individually whether to support `extra_headers`. Not all toolsets support it — only those whose request method renders `extra_headers` via `render_header_templates()` and merges the result into outgoing HTTP calls.

The following example shows how ServiceNow Tables, one toolset that supports header propagation, is configured:

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    toolsets:
      servicenow/tables:
        config:
          extra_headers:
            X-Correlation-Id: "{{ request_context.headers['X-Correlation-Id'] }}"
          api_key: "{{ env.SERVICENOW_API_KEY }}"
          api_url: "https://instance.service-now.com"
    ```

=== "Holmes Helm Chart"

    Add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: SERVICENOW_API_KEY
        valueFrom:
          secretKeyRef:
            name: servicenow-credentials
            key: api-key

    toolsets:
      servicenow/tables:
        config:
          extra_headers:
            X-Correlation-Id: "{{ request_context.headers['X-Correlation-Id'] }}"
          api_key: "{{ env.SERVICENOW_API_KEY }}"
          api_url: "https://instance.service-now.com"
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: SERVICENOW_API_KEY
          valueFrom:
            secretKeyRef:
              name: servicenow-credentials
              key: api-key

      toolsets:
        servicenow/tables:
          config:
            extra_headers:
              X-Correlation-Id: "{{ request_context.headers['X-Correlation-Id'] }}"
            api_key: "{{ env.SERVICENOW_API_KEY }}"
            api_url: "https://instance.service-now.com"
    ```

For a reference implementation showing how to add `extra_headers` support to a Python toolset, see [`servicenow_tables.py`](https://github.com/HolmesGPT/holmesgpt/blob/master/holmes/plugins/toolsets/servicenow_tables/servicenow_tables.py).

## Sending Headers to Holmes

Include your custom headers alongside the normal request:

```bash
curl -X POST http://holmes-server/api/investigate \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: your-token-here" \
  -H "X-Tenant-Id: tenant-42" \
  -d '{"question": "Check system status"}'
```

## Blocked Headers

By default, the following headers are **not** forwarded from the incoming request to the `request_context`:

- `Authorization`
- `Cookie`
- `Set-Cookie`

You can override this list with the `HOLMES_PASSTHROUGH_BLOCKED_HEADERS` environment variable (comma-separated, case-insensitive):

```bash
# Block only Authorization (allow cookies through)
export HOLMES_PASSTHROUGH_BLOCKED_HEADERS="authorization"

# Block additional headers
export HOLMES_PASSTHROUGH_BLOCKED_HEADERS="authorization,cookie,set-cookie,x-internal-only"
```

## Precedence

When multiple header sources exist, later layers override earlier ones:

1. Toolset's own authentication headers (e.g., API key, bearer token)
2. LLM-provided headers (HTTP connector only, via the `headers` tool parameter)
3. `extra_headers` (rendered templates from the `config` section)
