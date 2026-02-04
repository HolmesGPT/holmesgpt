# MCP Servers

HolmesGPT can integrate with MCP (Model Context Protocol) servers to access external data sources and tools in real time.

## Transport Modes

HolmesGPT supports three MCP transport modes:

1. **`streamable-http`** (Recommended): Modern HTTP-based transport. Use this for new integrations.
2. **`stdio`**: Direct process communication via standard input/output. For CLI usage; Kubernetes deployments require Supergateway.
3. **`sse`** (Deprecated): Legacy Server-Sent Events transport. Use `streamable-http` instead.

## Streamable-HTTP (Recommended)

=== "Holmes CLI"

    Create a config file and pass it when running CLI commands.

    **custom_toolset.yaml:**

    ```yaml
    mcp_servers:
      my_server:
        description: "My MCP server"
        config:
          url: "http://example.com:8000/mcp/messages"
          mode: streamable-http
          headers:
            Authorization: "Bearer {{ env.MY_MCP_API_KEY }}"
        llm_instructions: "Use this server to access external data and perform remote operations."
    ```

    ```bash
    holmes ask -t custom_toolset.yaml "Query my MCP server"
    ```

    Alternatively, add the config to `~/.holmes/config.yaml` and run without `-t`.

=== "Holmes Helm Chart"

    Add to your Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MY_MCP_API_KEY
          valueFrom:
            secretKeyRef:
              name: mcp-credentials
              key: api_key

      custom_toolsets:
        mcp_servers:
          my_server:
            description: "My MCP server"
            config:
              url: "http://example.com:8000/mcp/messages"
              mode: streamable-http
              headers:
                Authorization: "Bearer {{ env.MY_MCP_API_KEY }}"
            llm_instructions: "Use this server to access external data and perform remote operations."
    ```

    ```bash
    helm upgrade holmes robusta/holmes --values=values.yaml
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MY_MCP_API_KEY
          valueFrom:
            secretKeyRef:
              name: mcp-credentials
              key: api_key

      custom_toolsets:
        mcp_servers:
          my_server:
            description: "My MCP server"
            config:
              url: "http://example.com:8000/mcp/messages"
              mode: streamable-http
              headers:
                Authorization: "Bearer {{ env.MY_MCP_API_KEY }}"
            llm_instructions: "Use this server to access external data and perform remote operations."
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

The URL path depends on your MCP server (e.g., `/mcp/messages`, `/mcp`, or a custom path). Check your server's documentation.

## Stdio

Stdio mode runs MCP servers as subprocesses, communicating via standard input/output.

=== "Holmes CLI"

    Create a config file and pass it when running CLI commands.

    **custom_toolset.yaml:**

    ```yaml
    mcp_servers:
      my_stdio_server:
        description: "Custom stdio MCP server"
        config:
          mode: stdio
          command: "python3"
          args:
            - "./my_mcp_server.py"
          env:
            CUSTOM_VAR: "value"
        llm_instructions: "Use this server to access custom tools provided by the stdio server."
    ```

    ```bash
    holmes ask -t custom_toolset.yaml "Run my MCP server tools"
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
    CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    ```

    **Deploy the MCP server pod:**

    ```yaml
    apiVersion: v1
    kind: Pod
    metadata:
      name: my-mcp-server
      labels:
        app: my-mcp-server
    spec:
      containers:
        - name: supergateway
          image: your-registry/your-mcp-server:latest
          ports:
            - containerPort: 8000
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
      name: my-mcp-server
    spec:
      selector:
        app: my-mcp-server
      ports:
        - protocol: TCP
          port: 8000
          targetPort: 8000
      type: ClusterIP
    ```

    **Connect Holmes to the MCP server:**

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          my_mcp_server:
            description: "My stdio MCP server via Supergateway"
            config:
              url: "http://my-mcp-server.default.svc.cluster.local:8000/sse"
              mode: sse
            llm_instructions: "Use this server to access custom tools."
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
    CMD ["--port", "8000", "--stdio", "python3", "-m", "your_mcp_module"]
    ```

    **Deploy the MCP server pod:**

    ```yaml
    apiVersion: v1
    kind: Pod
    metadata:
      name: my-mcp-server
      labels:
        app: my-mcp-server
    spec:
      containers:
        - name: supergateway
          image: your-registry/your-mcp-server:latest
          ports:
            - containerPort: 8000
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
      name: my-mcp-server
    spec:
      selector:
        app: my-mcp-server
      ports:
        - protocol: TCP
          port: 8000
          targetPort: 8000
      type: ClusterIP
    ```

    **Connect Holmes to the MCP server:**

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          my_mcp_server:
            description: "My stdio MCP server via Supergateway"
            config:
              url: "http://my-mcp-server.default.svc.cluster.local:8000/sse"
              mode: sse
            llm_instructions: "Use this server to access custom tools."
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## SSE (Deprecated)

SSE transport is deprecated. Use `streamable-http` for new integrations.

=== "Holmes CLI"

    ```yaml
    mcp_servers:
      legacy_server:
        description: "Legacy MCP server using SSE"
        config:
          url: "http://example.com:8000/sse"
          mode: sse
        llm_instructions: "Legacy server."
    ```

=== "Holmes Helm Chart"

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          legacy_server:
            description: "Legacy MCP server using SSE"
            config:
              url: "http://example.com:8000/sse"
              mode: sse
            llm_instructions: "Legacy server."
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          legacy_server:
            description: "Legacy MCP server using SSE"
            config:
              url: "http://example.com:8000/sse"
              mode: sse
            llm_instructions: "Legacy server."
    ```

The URL should end with `/sse`. If it doesn't, HolmesGPT will automatically append it.

## Advanced Configuration

**Dynamic Headers with Request Context**

MCP servers can use dynamic headers populated from the incoming HTTP request. This is useful for passing per-request authentication tokens.

=== "Holmes CLI"

    Not applicable - request context is only available when running Holmes as a server.

=== "Holmes Helm Chart"

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          my_server:
            description: "MCP server with dynamic auth"
            config:
              url: "http://mcp-server:8000/mcp"
              mode: streamable-http
              extra_headers:
                X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
            llm_instructions: "Use this server with per-request authentication."
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      custom_toolsets:
        mcp_servers:
          my_server:
            description: "MCP server with dynamic auth"
            config:
              url: "http://mcp-server:8000/mcp"
              mode: streamable-http
              extra_headers:
                X-Auth-Token: "{{ request_context.headers['X-Auth-Token'] }}"
            llm_instructions: "Use this server with per-request authentication."
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
