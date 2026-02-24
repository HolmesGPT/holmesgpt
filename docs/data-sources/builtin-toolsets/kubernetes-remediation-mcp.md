# Kubernetes Remediation (MCP)

The Kubernetes Remediation MCP server provides safe kubectl command execution with layered security controls. It enables Holmes to not only diagnose Kubernetes issues but also **remediate** them — restarting pods, scaling deployments, draining nodes, and more.

!!! warning "Write operations"
    Unlike the built-in read-only Kubernetes toolset, this MCP server can execute write operations (edit, patch, delete, scale, drain, etc.). Configure the `allowedCommands` setting carefully to match your security requirements.

## Prerequisites

For CLI deployments, you'll need to create the RBAC resources manually. For Helm deployments, the chart creates them automatically.

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the Kubernetes Remediation MCP server with appropriate RBAC.

    **Step 1: Create RBAC Resources**

    Create a file named `k8s-remediation-rbac.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: k8s-remediation-mcp-sa
      namespace: holmes-mcp
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: k8s-remediation-mcp
    roleRef:
      apiGroup: rbac.authorization.k8s.io
      kind: ClusterRole
      name: cluster-admin  # Use a more restrictive role in production
    subjects:
    - kind: ServiceAccount
      name: k8s-remediation-mcp-sa
      namespace: holmes-mcp
    ```

    ```bash
    kubectl apply -f k8s-remediation-rbac.yaml
    ```

    **Step 2: Deploy the MCP Server**

    Create a file named `k8s-remediation-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: k8s-remediation-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: k8s-remediation-mcp-server
      template:
        metadata:
          labels:
            app: k8s-remediation-mcp-server
        spec:
          serviceAccountName: k8s-remediation-mcp-sa
          containers:
          - name: k8s-remediation-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/mcp/kubernetes-remediation-mcp:1.0.0
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: KUBECTL_ALLOWED_COMMANDS
              value: "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate"
            - name: KUBECTL_TIMEOUT
              value: "60"
            resources:
              requests:
                memory: "64Mi"
                cpu: "50m"
              limits:
                memory: "128Mi"
            securityContext:
              readOnlyRootFilesystem: true
              runAsNonRoot: true
              runAsUser: 1000
              allowPrivilegeEscalation: false
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
      name: k8s-remediation-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: k8s-remediation-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    ```bash
    kubectl apply -f k8s-remediation-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      kubernetes_remediation:
        description: "Kubernetes remediation - execute kubectl commands"
        config:
          url: "http://k8s-remediation-mcp-server.holmes-mcp.svc.cluster.local:8000/mcp"
          mode: streamable-http
        restricted_tools:
          - "*"
        approval_required_tools:
          - "*"
    ```

    `restricted_tools: ["*"]` means all tools from this MCP server can only be called during a runbook invocation (prevents ad-hoc write operations). `approval_required_tools: ["*"]` means all tools require user confirmation before execution.

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
        # Tools that can only be called after a runbook invocation
        # Use ["*"] to restrict all tools, or specify tool names like ["kubectl", "run_image"]
        restrictedTools:
          - "*"
        # Tools that require user confirmation before execution
        # Use ["*"] to require approval for all tools, or specify tool names
        approvalRequiredTools:
          - "*"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        kubernetesRemediation:
          enabled: true
          # Tools that can only be called after a runbook invocation
          # Use ["*"] to restrict all tools, or specify tool names like ["kubectl", "run_image"]
          restrictedTools:
            - "*"
          # Tools that require user confirmation before execution
          # Use ["*"] to require approval for all tools, or specify tool names
          approvalRequiredTools:
            - "*"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Security Controls

The MCP server implements multiple security layers:

| Control | Description |
|---------|-------------|
| **Restricted tools** | By default, all tools require a runbook invocation to be called — prevents ad-hoc write operations |
| **Approval required** | By default, all tools require user confirmation before execution |
| **Command allowlist** | Only explicitly allowed kubectl subcommands can execute |
| **Flag blocklist** | Flags like `--kubeconfig`, `--context`, `--token` are always blocked |
| **Shell injection protection** | Shell metacharacters are rejected |
| **Image allowlist** | The `run_image` tool only allows pre-approved container images |
| **RBAC enforcement** | Kubernetes RBAC restricts which resources can be accessed |
| **Command timeout** | Commands are killed after a configurable timeout (default: 60s) |

## Available Tools

| Tool | Description |
|------|-------------|
| `kubectl` | Execute a validated kubectl command. Args are passed as a list (e.g., `["get", "pods", "-n", "production"]`) |
| `run_image` | Run a temporary pod with a pre-approved image (disabled by default) |
| `get_config` | Get the current MCP server configuration for debugging |

## Common Use Cases

```bash
holmes ask "Restart the payment-service deployment in the production namespace"
```

```bash
holmes ask "Scale the web-frontend deployment to 5 replicas"
```

```bash
holmes ask "Cordon the problematic node and drain it safely"
```

```bash
holmes ask "The checkout-api pods are crashlooping - investigate and fix"
```

## Additional Resources

- [Kubernetes Remediation MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/kubernetes-remediation)
