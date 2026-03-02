# Confluence (MCP)

This integration uses the community-maintained [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) MCP server. It provides access to Confluence for searching and retrieving documentation, enabling Holmes to find runbooks, search internal documentation, and retrieve page content during investigations.

!!! note "Confluence vs Confluence (MCP)"
    HolmesGPT has a built-in [Confluence toolset](confluence.md) that provides basic page fetching. This MCP server provides richer functionality including CQL search, page comments, and optional write operations.

## Prerequisites

Before configuring the Confluence MCP server, you need an Atlassian API token.

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Set a label (e.g., "Holmes MCP")
4. Click **Create**
5. **Copy the token immediately** - it won't be shown again

You'll also need:

- Your Confluence instance URL (e.g., `https://your-company.atlassian.net/wiki`)
- The email address associated with your Atlassian account

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the Confluence MCP server first, then configure Holmes to connect to it.

    **Step 1: Create the Confluence Credentials Secret**

    ```bash
    kubectl create namespace holmes-mcp

    kubectl create secret generic confluence-mcp-credentials \
      --from-literal=confluence-username=<YOUR_EMAIL> \
      --from-literal=confluence-api-token=<YOUR_API_TOKEN> \
      -n holmes-mcp
    ```

    **Step 2: Deploy the Confluence MCP Server**

    Create a file named `confluence-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: confluence-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: confluence-mcp-server
      template:
        metadata:
          labels:
            app: confluence-mcp-server
        spec:
          containers:
          - name: confluence-mcp
            image: ghcr.io/sooperset/mcp-atlassian:latest
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: TRANSPORT
              value: "sse"
            - name: CONFLUENCE_URL
              value: "https://your-company.atlassian.net/wiki"
            - name: CONFLUENCE_USERNAME
              valueFrom:
                secretKeyRef:
                  name: confluence-mcp-credentials
                  key: confluence-username
            - name: CONFLUENCE_API_TOKEN
              valueFrom:
                secretKeyRef:
                  name: confluence-mcp-credentials
                  key: confluence-api-token
            - name: ENABLED_TOOLS
              value: "confluence_search,confluence_get_page,confluence_get_page_content,confluence_get_comments"
            - name: READ_ONLY_MODE
              value: "true"
            resources:
              requests:
                memory: "128Mi"
                cpu: "100m"
              limits:
                memory: "256Mi"
            readinessProbe:
              httpGet:
                path: /healthz
                port: 8000
              initialDelaySeconds: 5
              periodSeconds: 10
            livenessProbe:
              httpGet:
                path: /healthz
                port: 8000
              initialDelaySeconds: 10
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: confluence-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: confluence-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f confluence-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      confluence:
        description: "Confluence documentation search and retrieval"
        config:
          url: "http://confluence-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: sse
        llm_instructions: |
          Use the Confluence MCP to search and retrieve documentation.
          Before every investigation, search Confluence for matching runbooks.
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Confluence credentials:

    ```bash
    kubectl create secret generic confluence-mcp-credentials \
      --from-literal=confluence-username=<YOUR_EMAIL> \
      --from-literal=confluence-api-token=<YOUR_API_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      confluenceMcp:
        enabled: true
        auth:
          secretName: "confluence-mcp-credentials"
        config:
          url: "https://your-company.atlassian.net/wiki"
    ```

    To customize how Holmes uses Confluence, you can provide your own LLM instructions:

    ```yaml
    mcpAddons:
      confluenceMcp:
        enabled: true
        auth:
          secretName: "confluence-mcp-credentials"
        config:
          url: "https://your-company.atlassian.net/wiki"
        llmInstructions: |
          Use the Confluence MCP to search and retrieve documentation.
          Before every investigation, search Confluence for matching runbooks.
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Confluence credentials:

    ```bash
    kubectl create secret generic confluence-mcp-credentials \
      --from-literal=confluence-username=<YOUR_EMAIL> \
      --from-literal=confluence-api-token=<YOUR_API_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        confluenceMcp:
          enabled: true
          auth:
            secretName: "confluence-mcp-credentials"
          config:
            url: "https://your-company.atlassian.net/wiki"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

| Tool | Description |
|------|-------------|
| `confluence_search` | Search pages using CQL (Confluence Query Language) |
| `confluence_get_page` | Retrieve page details by ID |
| `confluence_get_page_content` | Get the full content of a page |
| `confluence_get_comments` | Read comments on a page |
| `confluence_create_page` | Create a new page (disabled in read-only mode) |
| `confluence_update_page` | Update an existing page (disabled in read-only mode) |

## Testing the Connection

```bash
holmes ask "Search Confluence for runbook pages"
```

## Common Use Cases

```bash
holmes ask "Find the runbook for database failover procedures in Confluence"
```

```bash
holmes ask "Search Confluence for documentation about the payment service architecture"
```

```bash
holmes ask "Look up the incident response procedures in our Confluence wiki"
```

## Additional Resources

- [Atlassian API Tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
- [Confluence MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/confluence)
