# Sentry (MCP)

The Sentry MCP server provides access to Sentry for error tracking and monitoring. It enables Holmes to search issues, retrieve stack traces, analyze error patterns, and investigate application crashes.

## Prerequisites

Before configuring the Sentry MCP server, you need a Sentry Auth Token.

1. Go to **Settings** → **Auth Tokens** in your [Sentry account](https://sentry.io/settings/auth-tokens/)
2. Click **Create New Token**
3. Select the following scopes:
      - `org:read`
      - `project:read`
      - `issue:read`
      - `event:read`
      - `member:read`
      - `alerts:read`
      - `team:read`
4. Click **Create Token**
5. **Copy the token immediately** - it won't be shown again

!!! note "Self-hosted Sentry"
    If you're using a self-hosted Sentry instance, you'll also need your Sentry host URL (e.g., `https://sentry.mycompany.com`).

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the Sentry MCP server first, then configure Holmes to connect to it.

    **Step 1: Create the Sentry Token Secret**

    ```bash
    kubectl create namespace holmes-mcp

    kubectl create secret generic sentry-mcp-token \
      --from-literal=token=<YOUR_SENTRY_AUTH_TOKEN> \
      -n holmes-mcp
    ```

    **Step 2: Deploy the Sentry MCP Server**

    Create a file named `sentry-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: sentry-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: sentry-mcp-server
      template:
        metadata:
          labels:
            app: sentry-mcp-server
        spec:
          containers:
          - name: sentry-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/mcp/sentry-mcp:1.0.1
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: SENTRY_AUTH_TOKEN
              valueFrom:
                secretKeyRef:
                  name: sentry-mcp-token
                  key: token
            # Uncomment for self-hosted Sentry:
            # - name: SENTRY_HOST
            #   value: "https://sentry.mycompany.com"
            resources:
              requests:
                memory: "128Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 5
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 10
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: sentry-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: sentry-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f sentry-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      sentry:
        description: "Sentry error tracking and monitoring"
        config:
          url: "http://sentry-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: sse
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Sentry auth token:

    ```bash
    kubectl create secret generic sentry-mcp-token \
      --from-literal=token=<YOUR_SENTRY_AUTH_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      sentry:
        enabled: true
        auth:
          secretName: "sentry-mcp-token"
    ```

    To customize how Holmes uses Sentry, you can provide your own LLM instructions:

    ```yaml
    mcpAddons:
      sentry:
        enabled: true
        auth:
          secretName: "sentry-mcp-token"
        llmInstructions: |
          Use the Sentry MCP to investigate application errors and crashes.
          When investigating, always start by listing projects, then search for relevant issues,
          and retrieve full stack traces before drawing conclusions.
    ```

    For self-hosted Sentry, add the host configuration:

    ```yaml
    mcpAddons:
      sentry:
        enabled: true
        auth:
          secretName: "sentry-mcp-token"
        config:
          host: "https://sentry.mycompany.com"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Sentry auth token:

    ```bash
    kubectl create secret generic sentry-mcp-token \
      --from-literal=token=<YOUR_SENTRY_AUTH_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        sentry:
          enabled: true
          auth:
            secretName: "sentry-mcp-token"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

| Tool | Description |
|------|-------------|
| `list_organizations` | List accessible Sentry organizations |
| `list_projects` | List projects in an organization |
| `list_issues` | List issues for a project |
| `get_issue` | Get detailed issue information |
| `get_issue_events` | Get events for a specific issue |
| `get_event` | Get event details with full stack trace |
| `search_errors` | Search issues using Sentry query syntax |
| `resolve_issue` | Mark an issue as resolved |
| `assign_issue` | Assign an issue to a team member |

## Testing the Connection

```bash
holmes ask "List the Sentry projects in my organization"
```

## Common Use Cases

```bash
holmes ask "What are the most frequent unresolved errors in our backend project?"
```

```bash
holmes ask "Show me the stack trace for the latest crash in the payments service"
```

```bash
holmes ask "Are there any new error patterns that appeared in the last 24 hours?"
```

## Additional Resources

- [Sentry Auth Tokens](https://docs.sentry.io/account/auth-tokens/)
- [Sentry MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/sentry)
