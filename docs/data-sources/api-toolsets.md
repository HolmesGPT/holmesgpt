# HTTP Connectors

HTTP connectors enable HolmesGPT to make authenticated HTTP requests to external APIs and services. This is useful for integrating with SaaS platforms, internal APIs, and any service that provides an HTTP REST API.

Unlike MCP servers which require custom server implementations, HTTP connectors work directly with existing HTTP APIs using standard authentication methods.

## When to Use HTTP Connectors

**Use HTTP connectors when:**
- You need to integrate with an existing HTTP API (Confluence, Jira, etc.)
- The available MCP servers don't satisfy your requirements
- The API requires authentication (API keys, tokens, credentials)
- You want fine-grained control over which endpoints are accessible

## Configuration

HTTP connectors are configured using `type: http` in your toolsets configuration.

### Basic Structure

```yaml
toolsets:
  confluence-api:
    type: http
    enabled: true
    config:
      endpoints:
        - hosts:
            - "*.atlassian.net"
          paths: ["*"]
          methods: ["GET", "PUT", "POST", "DELETE"]
          auth:
            type: basic
            username: "{{ env.CONFLUENCE_USER }}"
            password: "{{ env.CONFLUENCE_API_KEY }}"
      verify_ssl: true
      timeout_seconds: 30
    llm_instructions: |
      ### Confluence REST API
      You can query Confluence using the REST API.
      The base URL is: {{ env.CONFLUENCE_BASE_URL }}
      Common endpoints:
      - GET /wiki/rest/api/content/search?cql={query} - Search using CQL
      - GET /wiki/rest/api/content/{contentId}?expand=ancestors - Get page with ancestor hierarchy

      To get parent page information, use the expand parameter: `?expand=ancestors`
      The ancestors array will contain the parent page details.
```

## Key Features

- **Endpoint Whitelisting**: Control exactly which API endpoints HolmesGPT can access
- **Multiple Authentication Methods**: Support for Basic Auth, Bearer tokens, and custom headers
- **Multi-Instance Support**: Configure multiple instances of the same API with different credentials
- **Custom Instructions**: Provide API-specific guidance to improve LLM tool usage
- **[Header Propagation](header-propagation.md)**: Forward HTTP headers from incoming requests to backend APIs using `extra_headers` templates


### Configuration Fields

#### Toolset Level

- **`type`** (required): Must be `http` for HTTP connectors
- **`enabled`**: Whether the toolset is active
- **`config`**: HTTP-specific configuration (see below)
- **`llm_instructions`**: Custom instructions for the LLM about how to use this API

#### Config Section

- **`endpoints`**: List of whitelisted endpoint configurations
  - **`hosts`**: List of allowed host patterns. Each entry can be a bare hostname, wildcard subdomain (`*.example.com`), or an origin string that constrains scheme and/or port (see [Host Patterns](#host-patterns) below).
  - **`paths`**: List of allowed URL paths (supports glob patterns like `/api/*`)
  - **`methods`**: List of allowed HTTP methods (`GET`, `POST`, `PUT`, `DELETE`, etc.)
  - **`auth`** (optional): Authentication configuration (see Authentication section)
- **`verify_ssl`** (optional): Whether to verify SSL certificates (default: true)
- **`timeout_seconds`** (optional): Request timeout in seconds (default: 30)
- **`block_internal_ips`** (optional): Reject requests whose host resolves to an internal address (default: false — see [Redirects and internal addresses](#redirects-and-internal-addresses))

### Authentication

Authentication is optional. If your API doesn't require authentication, omit the `auth` field.

HTTP connectors support three authentication types:

#### Basic Authentication

```yaml
auth:
  type: basic
  username: "{{ env.API_USERNAME }}"
  password: "{{ env.API_PASSWORD }}"
```

#### Bearer Token

```yaml
auth:
  type: bearer
  token: "{{ env.API_TOKEN }}"
```

#### Custom Headers

```yaml
auth:
  type: header
  name: "X-API-Key"
  value: "{{ env.API_KEY }}"
```

### Environment Variables

Use Jinja2 template syntax to reference environment variables:

```yaml
username: "{{ env.CONFLUENCE_USER }}"
password: "{{ env.CONFLUENCE_API_KEY }}"
```

## Example: Confluence Integration

!!! tip "Use the dedicated Confluence toolset instead"
    HolmesGPT includes a [dedicated Confluence toolset](builtin-toolsets/confluence.md) with CQL search and support for both Cloud and Data Center. The HTTP connector example below is only needed for advanced use cases not covered by the built-in toolset.

This example shows how to use an HTTP connector with Atlassian Confluence to search pages and retrieve content.

=== "Holmes CLI"

    **Create toolsets.yaml:**

    ```yaml
    toolsets:
      confluence-api:
        type: http
        enabled: true
        config:
          endpoints:
            - hosts:
                - "*.atlassian.net"
              paths: ["*"]
              methods: ["GET", "PUT", "POST", "DELETE"]
              auth:
                type: basic
                username: "{{ env.CONFLUENCE_USER }}"
                password: "{{ env.CONFLUENCE_API_KEY }}"
          verify_ssl: true
          timeout_seconds: 30
        llm_instructions: |
          ### Confluence REST API
          You can query Confluence using the REST API.
          The base URL is: {{ env.CONFLUENCE_BASE_URL }}
          Common endpoints:
          - GET /wiki/rest/api/content/search?cql={query} - Search using CQL
          - GET /wiki/rest/api/content/{contentId}?expand=ancestors - Get page with ancestor hierarchy

          To get parent page information, use the expand parameter: `?expand=ancestors`
          The ancestors array will contain the parent page details.
    ```

    **Set environment variables:**

    ```bash
    export CONFLUENCE_BASE_URL="https://yourcompany.atlassian.net"
    export CONFLUENCE_USER="your-email@example.com"
    export CONFLUENCE_API_KEY="your-api-token"
    ```

    **Run HolmesGPT:**

    ```bash
    holmes ask "search Confluence for runbooks about database issues" --custom-toolsets=toolsets.yaml
    ```

=== "Robusta Helm Chart"

    **Helm Values:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: CONFLUENCE_BASE_URL
          value: https://yourcompany.atlassian.net
        - name: CONFLUENCE_USER
          value: your-email@example.com
        - name: CONFLUENCE_API_KEY
          valueFrom:
            secretKeyRef:
              name: confluence-credentials
              key: api-key

      toolsets:
        confluence-api:
          type: http
          enabled: true
          config:
            endpoints:
              - hosts:
                  - "*.atlassian.net"
                paths: ["*"]
                methods: ["GET", "PUT", "POST", "DELETE"]
                auth:
                  type: basic
                  username: "{{ env.CONFLUENCE_USER }}"
                  password: "{{ env.CONFLUENCE_API_KEY }}"
            verify_ssl: true
            timeout_seconds: 30
          llm_instructions: |
            ### Confluence REST API
            You can query Confluence using the REST API.
            The base URL is: {{ env.CONFLUENCE_BASE_URL }}
            Common endpoints:
            - GET /wiki/rest/api/content/search?cql={query} - Search using CQL
            - GET /wiki/rest/api/content/{contentId}?expand=ancestors - Get page with ancestor hierarchy

            To get parent page information, use the expand parameter: `?expand=ancestors`
            The ancestors array will contain the parent page details.
    ```

## Tool Naming

When you create an HTTP connector with name `my_api`, HolmesGPT automatically creates a tool named `my_api_request` that the LLM can call.

For example:
- Toolset name: `confluence-api` → Tool name: `confluence-api_request`
- Toolset name: `jira-api` → Tool name: `jira-api_request`

## Multiple Instances

You can configure multiple instances of the same API with different credentials or endpoints:

```yaml
toolsets:
  confluence_prod:
    type: http
    config:
      endpoints:
        - hosts: ["prod.atlassian.net"]
          # ... prod configuration

  confluence_dev:
    type: http
    config:
      endpoints:
        - hosts: ["dev.atlassian.net"]
          # ... dev configuration
```

This creates two separate tools: `confluence_prod_request` and `confluence_dev_request`.

## Endpoint Whitelisting

The endpoint whitelist provides security by restricting which APIs the HTTP connector can access.

### Host Patterns

Each entry in `hosts` controls which scheme and port the LLM may use, in addition to the hostname. The form of the entry determines what is allowed:

| Entry | Allowed scheme | Allowed port |
|---|---|---|
| `api.example.com` | any | any |
| `*.example.com` | any | any |
| `api.example.com:8080` | any | `8080` only |
| `https://api.example.com` | `https` only | `443` only |
| `http://api.example.com` | `http` only | `80` only |
| `https://api.example.com:8443` | `https` only | `8443` only |
| `https://*.example.com` | `https` only | `443` only |
| `https://*.example.com:*` | `https` only | any |

**Rules:**

- A bare hostname (or bare wildcard) imposes no scheme or port restriction.
- Adding a port (`host:8080`) pins requests to that port; the scheme stays unrestricted.
- Adding a scheme pins both scheme and port. When no explicit port is written, the scheme's default port is used (`80` for `http`, `443` for `https`).
- To allow a scheme on any port, use the explicit `:*` wildcard (e.g. `https://host:*`).
- Wildcards must be a leading `*.` and match one or more subdomain labels. `*.example.com` matches `api.example.com` and `foo.bar.example.com`, but not `example.com` itself.
- Multiple entries in `hosts` are ORed — a request matches if any entry matches.

**Example:**

```yaml
hosts:
  - "api.example.com"                    # any scheme, any port
  - "*.internal.example.com"             # any subdomain, any scheme, any port
  - "https://jenkins.example.com:8080"   # https on 8080 only
  - "https://*.tools.example.com:*"      # https on any port, any subdomain
```

Malformed entries (unsupported scheme, port out of range, mid-host wildcards, etc.) fail at config-load time with a validation error.

### Path Patterns

Paths use glob pattern matching:

- **Exact path**: `/api/v1/users`
- **Wildcard**: `/api/*` (matches `/api/users`, `/api/v1/data`, etc.)
- **Nested wildcard**: `/api/*/resources/*`

### HTTP Methods

Specify which HTTP methods are allowed:

```yaml
methods: ["GET"]  # Read-only
methods: ["GET", "POST"]  # Read and create
methods: ["GET", "POST", "PUT", "DELETE"]  # Full access
```

### Redirects and internal addresses

The whitelist is enforced on **every hop**, not just the URL the LLM asks for. If a whitelisted host answers with a redirect, the redirect target must itself match the whitelist or the request is refused — so an open redirect on a trusted upstream cannot be used to reach cloud metadata (`169.254.169.254`), an in-cluster service, or localhost.

Alongside that:

- Credentials are dropped when a redirect crosses an origin (a different scheme, host **or** port). This covers `auth` of every type, including the header name you configure for `auth: {type: header}`, plus any `Authorization`/`Cookie` header supplied with the request.
- A redirect target must also allow the method being used, per its own `methods` list.
- Redirect chains are capped at 5 hops.
- The same rules apply to `health_check_url`, except that a health check may redirect within its own origin (so it does not have to satisfy the endpoint's `paths` whitelist).

`block_internal_ips` adds a second, optional layer: the host is resolved and the request is refused if any resolved address is loopback, link-local (including the cloud metadata endpoint), private, reserved, multicast or unspecified. When enabled, the connection is also pinned to the exact IP that was validated, so a DNS rebind between validation and connection cannot swap in an internal address.

It defaults to **false** because whitelisted endpoints are very often in-cluster services:

```yaml
config:
  endpoints:
    - hosts: ["prometheus.monitoring.svc:9090"]   # internal by design
```

Enable it when every configured endpoint is a public host:

```yaml
config:
  block_internal_ips: true
  endpoints:
    - hosts: ["https://api.example.com"]
```

## LLM Instructions

The `llm_instructions` field provides guidance to the LLM about how to use your API. Good instructions include:

**The base URL:**
```yaml
llm_instructions: |
  The base URL is: {{ env.API_BASE_URL }}
```

**Available endpoints and their purpose:**
```yaml
llm_instructions: |
  Common endpoints:
  - GET /api/users/{id} - Get user information
  - GET /api/search?q={query} - Search resources
```

**API-specific guidance:**
```yaml
llm_instructions: |
  When searching, use CQL syntax: space=MYSPACE AND type=page
  Always include the expand parameter to get full page content.
```

**Authentication details** (if needed by the LLM):
```yaml
llm_instructions: |
  Authentication is handled automatically using the configured credentials.
```

## Troubleshooting

### Authentication Errors

**Problem**: `401 Unauthorized` or `403 Forbidden`

**Solutions**:
- Verify credentials are correct
- Check that API token has required permissions
- Ensure environment variables are properly set
- Verify the authentication type matches your API's requirements

### SSL Certificate Errors

**Problem**: SSL verification failures

**Solutions**:
- Set `verify_ssl: false` for internal APIs with self-signed certificates
- Add your CA certificate to the container's trust store

### Request Timeouts

**Problem**: Requests timing out

**Solutions**:
- Increase `timeout_seconds` in config
- Check network connectivity to the API
- Verify the API endpoint is responsive

### Access Denied

**Problem**: Endpoint blocked even though it should be allowed

**Solutions**:
- Check that the host matches your whitelist (including wildcards)
- Verify the path pattern matches the endpoint you're trying to access
- Ensure the HTTP method is in the allowed methods list
- If a `hosts` entry includes a scheme (e.g. `https://...`), make sure the request uses the same scheme and either the scheme's default port or the port you specified — see [Host Patterns](#host-patterns)
- Check HolmesGPT logs for the exact URL being blocked; the error message includes the request's scheme, host, port, and path

### Refused Redirects

**Problem**: `Refusing to follow redirect from ... to ...`

The upstream answered with a redirect whose target is not in the whitelist. This is the SSRF guard working as intended — see [Redirects and internal addresses](#redirects-and-internal-addresses).

**Solutions**:
- If the redirect target is legitimate, add it to `hosts`/`paths` (and to `methods` if the redirect keeps a non-GET method)
- Point the endpoint at the final URL so no redirect is needed
- If the target is *not* something you expect the upstream to redirect to, treat it as a finding rather than a config problem

**Problem**: `Refusing to request ...: host ... resolves to non-routable/internal address`

`block_internal_ips` is enabled and the host resolves to an internal address. Set it to `false` if the endpoint is an in-cluster service.
