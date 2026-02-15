# MCP Servers

HolmesGPT can integrate with MCP (Model Context Protocol) servers to access external data sources and tools in real time.

## Transport Modes

HolmesGPT supports three MCP transport modes:

1. **`streamable-http`** (Recommended): Modern HTTP-based transport. Use this for new integrations.
2. **`stdio`**: Direct process communication via standard input/output. Supported directly in CLI; supported on Kubernetes via [Supergateway](https://github.com/supercorp-ai/supergateway).
3. **`sse`** (Deprecated): Legacy Server-Sent Events transport. Use `streamable-http` instead.

## Streamable-HTTP (Recommended)

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      dynatrace:
        description: "Dynatrace observability platform"
        config:
          url: "http://dynatrace-mcp:8000/mcp/messages"
          mode: streamable-http
          headers:
            Authorization: "Bearer {{ env.DYNATRACE_API_KEY }}"
          icon_url: "https://cdn.simpleicons.org/dynatrace/1496FF"  # Optional: icon for UI
        # llm_instructions tells Holmes WHEN and HOW to use this server
        llm_instructions: "Use Dynatrace to investigate application performance issues, analyze distributed traces, and query infrastructure metrics. Prefer this over Prometheus for APM data."
    ```

    ```bash
    holmes ask "What services have high error rates in Dynatrace?"
    ```

=== "Holmes Helm Chart"

    Add to your Helm values:

    ```yaml
    additionalEnvVars:
      - name: DYNATRACE_API_KEY
        valueFrom:
          secretKeyRef:
            name: mcp-credentials
            key: api_key

    mcp_servers:
      dynatrace:
        description: "Dynatrace observability platform"
        config:
          url: "http://dynatrace-mcp:8000/mcp/messages"
          mode: streamable-http
          headers:
            Authorization: "Bearer {{ env.DYNATRACE_API_KEY }}"
          icon_url: "https://cdn.simpleicons.org/dynatrace/1496FF"  # Optional: icon for UI
        # llm_instructions tells Holmes WHEN and HOW to use this server
        llm_instructions: "Use Dynatrace to investigate application performance issues, analyze distributed traces, and query infrastructure metrics. Prefer this over Prometheus for APM data."
    ```

    ```bash
    helm upgrade holmes robusta/holmes --values=values.yaml
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: DYNATRACE_API_KEY
          valueFrom:
            secretKeyRef:
              name: mcp-credentials
              key: api_key

      mcp_servers:
        dynatrace:
          description: "Dynatrace observability platform"
          config:
            url: "http://dynatrace-mcp:8000/mcp/messages"
            mode: streamable-http
            headers:
              Authorization: "Bearer {{ env.DYNATRACE_API_KEY }}"
            icon_url: "https://cdn.simpleicons.org/dynatrace/1496FF"  # Optional: icon for UI
          # llm_instructions tells Holmes WHEN and HOW to use this server
          llm_instructions: "Use Dynatrace to investigate application performance issues, analyze distributed traces, and query infrastructure metrics. Prefer this over Prometheus for APM data."
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

The URL path depends on your MCP server (e.g., `/mcp/messages`, `/mcp`, or a custom path). Check your server's documentation.

## Stdio

Stdio mode runs MCP servers as subprocesses, communicating via standard input/output.

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      ticket_db:
        description: "Internal ticket database"
        config:
          mode: stdio
          command: "python3"
          args:
            - "/path/to/my_mcp_server.py"
          env:
            CUSTOM_VAR: "value"
        # llm_instructions tells Holmes WHEN and HOW to use this server
        llm_instructions: "Use this server to query the internal ticket database. Search for related incidents by error message or service name."
    ```

    ```bash
    holmes ask "Find tickets related to payment service errors"
    ```

    Ensure required dependencies (e.g., `mcp`, `fastmcp` packages) are installed in your environment.

=== "Holmes Helm Chart"

    !!! warning "Stdio requires Supergateway for Kubernetes"
        Stdio mode cannot run directly in the Holmes container due to missing dependencies. Run your stdio MCP server in a separate pod using [Supergateway](https://github.com/supercorp-ai/supergateway) to expose it as HTTP.

    **Create a Docker image with your MCP server:**

    ```dockerfile
    FROM supercorp/supergateway:latest

    USER root
    # Install your MCP server dependencies
    # Example: RUN apk add --no-cache python3 py3-pip
    # Example: RUN pip3 install --no-cache-dir --break-system-packages your-mcp-package
    USER node

    EXPOSE 8000
    # Replace with your MCP server command. Examples:
    #   CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    #   CMD ["--port", "8000", "--stdio", "python3", "/app/stdio_server.py"]
    #   CMD ["--port", "8000", "--stdio", "npx", "-y", "@your-org/your-mcp-server@latest"]
    CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    ```

    **Deploy the MCP server pod:**

    ```yaml
    apiVersion: v1
    kind: Pod
    metadata:
      name: ticket-db-mcp
      labels:
        app: ticket-db-mcp
    spec:
      containers:
        - name: supergateway
          image: your-registry/your-mcp-server:latest
          ports:
            - containerPort: 8000
          args:
            - "--stdio"
            # Replace with your MCP server command
            # Examples: "python3 -m your_mcp_module", "python3 /app/stdio_server.py", "npx -y @your-org/your-mcp-server@latest"
            - "python3 -m your_mcp_module"
            - "--port"
            - "8000"
            - "--logLevel"
            - "debug"
          env:
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: mcp-credentials
                  key: api_key
          stdin: true
          tty: true
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: ticket-db-mcp
    spec:
      selector:
        app: ticket-db-mcp
      ports:
        - protocol: TCP
          port: 8000
          targetPort: 8000
      type: ClusterIP
    ```

    **Connect Holmes to the MCP server:**

    ```yaml
    mcp_servers:
      ticket_db:
        description: "Internal ticket database"
        config:
          url: "http://ticket-db-mcp.default.svc.cluster.local:8000/sse"
          mode: sse
        # llm_instructions tells Holmes WHEN and HOW to use this server
        llm_instructions: "Use this server to query the internal ticket database. Search for related incidents by error message or service name."
    ```

    ```bash
    helm upgrade holmes robusta/holmes --values=values.yaml
    ```

=== "Robusta Helm Chart"

    !!! warning "Stdio requires Supergateway for Kubernetes"
        Stdio mode cannot run directly in the Holmes container due to missing dependencies. Run your stdio MCP server in a separate pod using [Supergateway](https://github.com/supercorp-ai/supergateway) to expose it as HTTP.

    **Create a Docker image with your MCP server:**

    ```dockerfile
    FROM supercorp/supergateway:latest

    USER root
    # Install your MCP server dependencies
    # Example: RUN apk add --no-cache python3 py3-pip
    # Example: RUN pip3 install --no-cache-dir --break-system-packages your-mcp-package
    USER node

    EXPOSE 8000
    # Replace with your MCP server command. Examples:
    #   CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    #   CMD ["--port", "8000", "--stdio", "python3", "/app/stdio_server.py"]
    #   CMD ["--port", "8000", "--stdio", "npx", "-y", "@your-org/your-mcp-server@latest"]
    CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    ```

    **Deploy the MCP server pod:**

    ```yaml
    apiVersion: v1
    kind: Pod
    metadata:
      name: ticket-db-mcp
      labels:
        app: ticket-db-mcp
    spec:
      containers:
        - name: supergateway
          image: your-registry/your-mcp-server:latest
          ports:
            - containerPort: 8000
          args:
            - "--stdio"
            # Replace with your MCP server command
            # Examples: "python3 -m your_mcp_module", "python3 /app/stdio_server.py", "npx -y @your-org/your-mcp-server@latest"
            - "python3 -m your_mcp_module"
            - "--port"
            - "8000"
            - "--logLevel"
            - "debug"
          env:
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: mcp-credentials
                  key: api_key
          stdin: true
          tty: true
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: ticket-db-mcp
    spec:
      selector:
        app: ticket-db-mcp
      ports:
        - protocol: TCP
          port: 8000
          targetPort: 8000
      type: ClusterIP
    ```

    **Connect Holmes to the MCP server:**

    ```yaml
    holmes:
      mcp_servers:
        ticket_db:
          description: "Internal ticket database"
          config:
            url: "http://ticket-db-mcp.default.svc.cluster.local:8000/sse"
            mode: sse
          # llm_instructions tells Holmes WHEN and HOW to use this server
          llm_instructions: "Use this server to query the internal ticket database. Search for related incidents by error message or service name."
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## SSE (Deprecated)

SSE transport is deprecated. Use `streamable-http` for new integrations.

=== "Holmes CLI"

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      legacy_analytics:
        description: "Legacy analytics platform (SSE transport)"
        config:
          url: "http://analytics-mcp:8000/sse"
          mode: sse
        llm_instructions: "Query historical analytics data. Use for trend analysis over periods longer than 30 days."
    ```

=== "Holmes Helm Chart"

    ```yaml
    mcp_servers:
      legacy_analytics:
        description: "Legacy analytics platform (SSE transport)"
        config:
          url: "http://analytics-mcp:8000/sse"
          mode: sse
        llm_instructions: "Query historical analytics data. Use for trend analysis over periods longer than 30 days."
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      mcp_servers:
        legacy_analytics:
          description: "Legacy analytics platform (SSE transport)"
          config:
            url: "http://analytics-mcp:8000/sse"
            mode: sse
          llm_instructions: "Query historical analytics data. Use for trend analysis over periods longer than 30 days."
    ```

The URL should end with `/sse`. If it doesn't, HolmesGPT will automatically append it.

## Advanced Configuration

**Dynamic Headers with Request Context**

MCP servers can use dynamic headers populated from the incoming HTTP request. This is useful for passing per-request authentication tokens.

=== "Holmes CLI"

    Not applicable - request context is only available when running Holmes as a server.

=== "Holmes Helm Chart"

    ```yaml
    mcp_servers:
      customer_data:
        description: "Customer data API (requires per-request auth)"
        config:
          url: "http://customer-api:8000/mcp"
          mode: streamable-http
          extra_headers:
            X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
        llm_instructions: "Query customer account details and subscription status. Use when investigating user-reported issues."
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      mcp_servers:
        customer_data:
          description: "Customer data API (requires per-request auth)"
          config:
            url: "http://customer-api:8000/mcp"
            mode: streamable-http
            extra_headers:
              X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
          llm_instructions: "Query customer account details and subscription status. Use when investigating user-reported issues."
    ```

When making requests to HolmesGPT, include the required header:

```bash
curl -X POST http://holmes-server/api/investigate \
  -H "X-Auth-Token: your-auth-token-here" \
  -H "Content-Type: application/json" \
  -d '{"question": "Check system status"}'
```

Header lookups are case-insensitive. You can also use environment variables (`{{ env.MY_VAR }}`) or combine them (`Bearer {{ request_context.headers['token'] }}`).

## Configuration Format Migration

The MCP server configuration format has been updated. The `url` field must now be inside the `config` section.

**Old format (deprecated):**

```yaml
mcp_servers:
  my_server:
    url: "http://example.com:8000/mcp/messages"
    description: "My server"
```

**New format:**

```yaml
mcp_servers:
  my_server:
    description: "My server"
    config:
      url: "http://example.com:8000/mcp/messages"
      mode: streamable-http
```

The old format still works but will log a migration warning.
