# Azure (MCP)

The Azure MCP server gives Holmes **read-only access to any Azure API** you permit via RBAC. This means Holmes can query VMs, AKS, SQL databases, Activity Log, Azure Monitor, networking, storage, and hundreds of other Azure services - limited only by the roles you assign.

## Holmes CLI

The [Azure API MCP server](https://github.com/Azure/azure-api-mcp) runs locally on your machine as a subprocess.

**Prerequisites:** [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) must be installed with working credentials (`az account show` should succeed).

**Step 1: Install the server**

=== "go install (recommended)"

    Requires Go 1.24+:

    ```bash
    go install github.com/Azure/azure-api-mcp/cmd/server@latest
    ```

    The binary is installed to `$GOPATH/bin/server`. Rename it for clarity:

    ```bash
    mv "$(go env GOPATH)/bin/server" "$(go env GOPATH)/bin/azure-api-mcp"
    ```

=== "Pre-built binary"

    Download from the [releases page](https://github.com/Azure/azure-api-mcp/releases):

    ```bash
    # Linux (amd64)
    curl -Lo azure-api-mcp https://github.com/Azure/azure-api-mcp/releases/latest/download/azure-api-mcp-linux-amd64
    chmod +x azure-api-mcp
    sudo mv azure-api-mcp /usr/local/bin/

    # macOS (Apple Silicon)
    curl -Lo azure-api-mcp https://github.com/Azure/azure-api-mcp/releases/latest/download/azure-api-mcp-darwin-arm64
    chmod +x azure-api-mcp
    sudo mv azure-api-mcp /usr/local/bin/
    ```

=== "Build from source"

    ```bash
    git clone https://github.com/Azure/azure-api-mcp.git
    cd azure-api-mcp
    go build -o azure-api-mcp ./cmd/server
    sudo mv azure-api-mcp /usr/local/bin/
    ```

**Step 2: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  azure_api:
    description: "Azure API MCP Server - comprehensive Azure service access via Azure CLI"
    config:
      mode: stdio
      command: "azure-api-mcp"
      args: ["--readonly"]
    llm_instructions: |
      IMPORTANT: When investigating issues related to Azure resources or Kubernetes workloads running on Azure,
      you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

      ## Investigation Principles

      **ALWAYS follow this investigation flow:**
      1. First, gather current state and configuration using Azure CLI commands
      2. Check Activity Log for recent changes that might have caused the issue
      3. Collect metrics and logs from Azure Monitor if available
      4. Analyze all gathered data before providing conclusions

      **Never say "check in Azure portal" or "verify in Azure" - instead, use the MCP server to check it yourself.**

      See the Azure MCP documentation for comprehensive investigation patterns and common commands.
```

**Step 3: Test it**

```bash
holmes ask "List all resource groups in my Azure subscription"
```

## Helm Chart Deployment

For in-cluster deployments, first set up Azure RBAC, then choose an authentication method.

### Step 1: Set Up Azure RBAC Roles

Assign roles based on what you want Holmes to investigate. At minimum, assign **Reader** on the subscription:

| Role | Purpose |
|------|---------|
| Reader | Read-only access to all resources (minimum) |
| Azure Kubernetes Service Cluster User Role | kubectl access via `az aks get-credentials` |
| Log Analytics Reader | Container Insights and Azure Monitor logs |
| Monitoring Reader | Azure Monitor metrics |
| Cost Management Reader | Cost analysis |

**Setup Script (recommended):**

```bash
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/azure/setup-azure-identity.sh
bash setup-azure-identity.sh --auth-method workload-identity \
  --resource-group YOUR_RESOURCE_GROUP \
  --aks-cluster YOUR_AKS_CLUSTER \
  --all-subscriptions
```

This script creates a managed identity, assigns RBAC roles, configures federated credentials, and outputs the configuration values for your Helm chart.

??? info "Manual Role Assignment"
    ```bash
    # Assign Reader role to managed identity
    az role assignment create \
      --assignee YOUR_CLIENT_ID \
      --role Reader \
      --scope /subscriptions/YOUR_SUBSCRIPTION_ID

    # Assign Log Analytics Reader for monitoring
    az role assignment create \
      --assignee YOUR_CLIENT_ID \
      --role "Log Analytics Reader" \
      --scope /subscriptions/YOUR_SUBSCRIPTION_ID

    # Assign Cost Management Reader for cost analysis
    az role assignment create \
      --assignee YOUR_CLIENT_ID \
      --role "Cost Management Reader" \
      --scope /subscriptions/YOUR_SUBSCRIPTION_ID
    ```

### Step 2: Deploy with Helm

Choose an authentication method based on your environment:

=== "Holmes Helm Chart"

    Update your `values.yaml` with the appropriate authentication method:

    **Workload Identity (Recommended for AKS)**

    ```yaml
    mcpAddons:
      azure:
        enabled: true

        serviceAccount:
          create: true
          name: "azure-api-mcp-sa"
          annotations:
            azure.workload.identity/client-id: "YOUR_CLIENT_ID"
            azure.workload.identity/tenant-id: "YOUR_TENANT_ID"

        config:
          tenantId: "YOUR_TENANT_ID"
          subscriptionId: "YOUR_SUBSCRIPTION_ID"
          authMethod: "workload-identity"
          clientId: "YOUR_CLIENT_ID"
          readOnlyMode: true
    ```

    **Service Principal** (for non-AKS clusters):

    ```yaml
    mcpAddons:
      azure:
        enabled: true

        serviceAccount:
          create: true
          name: "azure-api-mcp-sa"

        config:
          tenantId: "YOUR_TENANT_ID"
          subscriptionId: "YOUR_SUBSCRIPTION_ID"
          authMethod: "service-principal"
          readOnlyMode: true

        secretName: "azure-mcp-creds"
    ```

    Create the secret before deploying:

    ```bash
    kubectl create secret generic azure-mcp-creds \
      --from-literal=AZURE_CLIENT_ID=YOUR_CLIENT_ID \
      --from-literal=AZURE_CLIENT_SECRET=YOUR_CLIENT_SECRET \
      -n YOUR_NAMESPACE
    ```

    **Managed Identity** (AKS with node-level managed identity):

    ```yaml
    mcpAddons:
      azure:
        enabled: true

        config:
          tenantId: "YOUR_TENANT_ID"
          subscriptionId: "YOUR_SUBSCRIPTION_ID"
          authMethod: "managed-identity"
          clientId: "YOUR_MANAGED_IDENTITY_CLIENT_ID"
          readOnlyMode: true
    ```

    For additional options, see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L162).

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Update your `generated_values.yaml` with the appropriate authentication method:

    **Workload Identity (Recommended for AKS)**

    ```yaml
    holmes:
      mcpAddons:
        azure:
          enabled: true

          serviceAccount:
            create: true
            name: "azure-api-mcp-sa"
            annotations:
              azure.workload.identity/client-id: "YOUR_CLIENT_ID"
              azure.workload.identity/tenant-id: "YOUR_TENANT_ID"

          config:
            tenantId: "YOUR_TENANT_ID"
            subscriptionId: "YOUR_SUBSCRIPTION_ID"
            authMethod: "workload-identity"
            clientId: "YOUR_CLIENT_ID"
            readOnlyMode: true
    ```

    **Service Principal** (for non-AKS clusters):

    ```yaml
    holmes:
      mcpAddons:
        azure:
          enabled: true

          serviceAccount:
            create: true
            name: "azure-api-mcp-sa"

          config:
            tenantId: "YOUR_TENANT_ID"
            subscriptionId: "YOUR_SUBSCRIPTION_ID"
            authMethod: "service-principal"
            readOnlyMode: true

          secretName: "azure-mcp-creds"
    ```

    Create the secret before deploying:

    ```bash
    kubectl create secret generic azure-mcp-creds \
      --from-literal=AZURE_CLIENT_ID=YOUR_CLIENT_ID \
      --from-literal=AZURE_CLIENT_SECRET=YOUR_CLIENT_SECRET \
      -n YOUR_NAMESPACE
    ```

    **Managed Identity** (AKS with node-level managed identity):

    ```yaml
    holmes:
      mcpAddons:
        azure:
          enabled: true

          config:
            tenantId: "YOUR_TENANT_ID"
            subscriptionId: "YOUR_SUBSCRIPTION_ID"
            authMethod: "managed-identity"
            clientId: "YOUR_MANAGED_IDENTITY_CLIENT_ID"
            readOnlyMode: true
    ```

    For additional options, see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L162).

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Multi-Subscription Access

Holmes can automatically discover and switch between subscriptions within the same tenant. Just ensure your identity has the appropriate roles in each subscription.

### Troubleshooting

```bash
# Check pod status
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server

# Check logs
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server

# Verify service account annotations
kubectl get sa azure-api-mcp-sa -n YOUR_NAMESPACE -o yaml

# Check RBAC role assignments
az role assignment list --assignee YOUR_CLIENT_ID --output table

# Test connectivity from Holmes pod
kubectl exec -it HOLMES_POD -n YOUR_NAMESPACE -- \
  curl http://RELEASE_NAME-azure-mcp-server.YOUR_NAMESPACE.svc.cluster.local:8000/health
```

## Example Usage

```
"Pods in namespace production can't reach Azure SQL database"
```

```
"Our ingress is showing TLS errors since yesterday"
```

```
"After AKS upgrade, some pods are failing to schedule"
```

```
"Applications intermittently can't connect to PostgreSQL since 2 PM"
```

```
"Our Azure costs increased 50% last week"
```
