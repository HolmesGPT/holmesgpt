# Prefect (MCP)

The Prefect MCP server provides access to Prefect workflow orchestration for monitoring and troubleshooting. It enables Holmes to inspect flow runs, retrieve logs, check worker health, and investigate failed or crashed workflows.

## Prerequisites

Before configuring the Prefect MCP server, you need:

- Your **Prefect API URL**
- A **Prefect API key** (required for Prefect Cloud, optional for self-hosted)

=== "Prefect Cloud"

    1. Log in to [Prefect Cloud](https://app.prefect.cloud)
    2. Go to your profile → **API Keys**
    3. Click **Create API Key**
    4. Copy the API key
    5. Your API URL follows this format:
    ```
    https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>
    ```
    You can find your account and workspace IDs in the Prefect Cloud URL when logged in.

=== "Self-Hosted Prefect"

    Your API URL is the address of your Prefect server, typically:
    ```
    http://prefect-server:4200/api
    ```
    An API key is optional for self-hosted deployments.

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the Prefect MCP server first, then configure Holmes to connect to it.

    **Step 1: Create the Prefect Credentials Secret**

    ```bash
    kubectl create namespace holmes-mcp

    kubectl create secret generic prefect-mcp-credentials \
      --from-literal=api-url=<YOUR_PREFECT_API_URL> \
      --from-literal=token=<YOUR_PREFECT_API_KEY> \
      -n holmes-mcp
    ```

    **Step 2: Deploy the Prefect MCP Server**

    Create a file named `prefect-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: prefect-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: prefect-mcp-server
      template:
        metadata:
          labels:
            app: prefect-mcp-server
        spec:
          containers:
          - name: prefect-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/mcp/prefect-mcp:1.0.0
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: PREFECT_API_URL
              valueFrom:
                secretKeyRef:
                  name: prefect-mcp-credentials
                  key: api-url
            - name: PREFECT_API_KEY
              valueFrom:
                secretKeyRef:
                  name: prefect-mcp-credentials
                  key: token
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
      name: prefect-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: prefect-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f prefect-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      prefect:
        description: "Prefect workflow orchestration and monitoring"
        config:
          url: "http://prefect-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: sse
        llm_instructions: |
          Use Prefect tools to investigate workflow failures, check flow run status, and troubleshoot orchestration issues.
          When investigating a failed flow run:
            1. First get the flow run details to understand what failed
            2. Retrieve the logs for the failed flow/task run
            3. Check if the deployment is healthy and workers are running
            4. Look at recent runs of the same flow to identify patterns
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Prefect API key:

    ```bash
    kubectl create secret generic prefect-mcp-token \
      --from-literal=token=<YOUR_PREFECT_API_KEY> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      prefect:
        enabled: true
        auth:
          secretName: "prefect-mcp-token"
        config:
          apiUrl: "https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>"
    ```

    To customize how Holmes uses Prefect, you can provide your own LLM instructions:

    ```yaml
    mcpAddons:
      prefect:
        enabled: true
        auth:
          secretName: "prefect-mcp-token"
        config:
          apiUrl: "https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>"
        llmInstructions: |
          Use Prefect tools to investigate workflow failures, check flow run status, and troubleshoot orchestration issues.
          When investigating a failed flow run:
            1. First get the flow run details to understand what failed
            2. Retrieve the logs for the failed flow/task run
            3. Check if the deployment is healthy and workers are running
            4. Look at recent runs of the same flow to identify patterns
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Prefect API key:

    ```bash
    kubectl create secret generic prefect-mcp-token \
      --from-literal=token=<YOUR_PREFECT_API_KEY> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        prefect:
          enabled: true
          auth:
            secretName: "prefect-mcp-token"
          config:
            apiUrl: "https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Testing the Connection

```bash
holmes ask "List the recent flow runs and their statuses"
```

## Common Use Cases

```bash
holmes ask "Why did the data-pipeline flow run fail last night?"
```

```bash
holmes ask "Are there any stuck or backlogged runs in the work pools?"
```

```bash
holmes ask "Show me the logs from the latest failed run of the ETL deployment"
```

```bash
holmes ask "Which workers are currently active and what are they processing?"
```

## Additional Resources

- [Prefect Cloud API Keys](https://docs.prefect.io/cloud/users/api-keys/)
- [Prefect MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/prefect)
