# Azure (MCP)

The Azure MCP server gives Holmes **read-only access to any Azure API** you permit via RBAC. This means Holmes can query VMs, AKS, SQL databases, Activity Log, Azure Monitor, networking, storage, and hundreds of other Azure services - limited only by the roles you assign.

## Overview

- **Helm users**: The MCP server pod is deployed automatically when you enable the addon
- **CLI users**: The MCP server runs locally on your machine as a subprocess

## Configuration

=== "Holmes CLI"

    The same [Azure API MCP server](https://github.com/Azure/azure-api-mcp) used in-cluster can run locally on your machine in stdio mode.

    **Prerequisites:** Go 1.24+ and [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) must be installed.

    **Step 1: Build the server**

    ```bash
    git clone https://github.com/Azure/azure-api-mcp.git
    cd azure-api-mcp
    go build -o azure-api-mcp ./cmd/server
    sudo mv azure-api-mcp /usr/local/bin/
    ```

    **Step 2: Authenticate**

    ```bash
    az login
    az account show  # verify correct subscription
    ```

    **Step 3: Configure Holmes CLI**

    Add to `~/.holmes/config.yaml`:

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

    **Step 4: Test it**

    ```bash
    holmes ask "List all resource groups in my Azure subscription"
    ```

=== "Holmes Helm Chart"

    **Workload Identity Authentication (Recommended for AKS)**

    The recommended approach for AKS clusters is to use Workload Identity. This provides secure, passwordless authentication.

    ```yaml
    mcpAddons:
      azure:
        enabled: true

        # Service account configuration
        serviceAccount:
          create: true
          name: "azure-api-mcp-sa"
          annotations:
            azure.workload.identity/client-id: "YOUR_CLIENT_ID"
            azure.workload.identity/tenant-id: "YOUR_TENANT_ID"

        # Azure configuration
        config:
          tenantId: "YOUR_TENANT_ID"
          subscriptionId: "YOUR_SUBSCRIPTION_ID"
          authMethod: "workload-identity"
          clientId: "YOUR_CLIENT_ID"
          readOnlyMode: true  # Recommended for safety
    ```

    **Setup Steps:**

    1. Follow the [Workload Identity setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/azure#workload-identity-setup-for-aks)
    2. Create a managed identity and assign Azure RBAC roles
    3. Configure federated identity credentials
    4. Deploy with the configuration above

    **Service Principal Authentication**

    For non-AKS clusters or if Workload Identity is not available:

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

        # Reference to existing secret with credentials
        secretName: "azure-mcp-creds"
    ```

    Create the secret before deploying:

    ```bash
    kubectl create secret generic azure-mcp-creds \
      --from-literal=AZURE_CLIENT_ID=YOUR_CLIENT_ID \
      --from-literal=AZURE_CLIENT_SECRET=YOUR_CLIENT_SECRET \
      -n YOUR_NAMESPACE
    ```

    **Managed Identity Authentication**

    For AKS clusters with node-level managed identity:

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

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L162).

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Workload Identity Authentication (Recommended for AKS)**

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    # Add the Holmes MCP addon configuration
    holmes:
      mcpAddons:
        azure:
          enabled: true

          # Service account configuration
          serviceAccount:
            create: true
            name: "azure-api-mcp-sa"
            annotations:
              azure.workload.identity/client-id: "YOUR_CLIENT_ID"
              azure.workload.identity/tenant-id: "YOUR_TENANT_ID"

          # Azure configuration
          config:
            tenantId: "YOUR_TENANT_ID"
            subscriptionId: "YOUR_SUBSCRIPTION_ID"
            authMethod: "workload-identity"
            clientId: "YOUR_CLIENT_ID"
            readOnlyMode: true
    ```

    **Service Principal Authentication**

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

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

    **Managed Identity Authentication**

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

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

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L162).

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## IAM Configuration

### Azure RBAC Roles

Assign roles based on what you want Holmes to investigate. At minimum, assign **Reader** on the subscription. For broader investigations, add more roles:

| Role | Purpose |
|------|---------|
| Reader | Read-only access to all resources (minimum) |
| Azure Kubernetes Service Cluster User Role | kubectl access via `az aks get-credentials` |
| Log Analytics Reader | Container Insights and Azure Monitor logs |
| Monitoring Reader | Azure Monitor metrics |
| Cost Management Reader | Cost analysis |

**Setup Script:**

```bash
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/azure/setup-azure-identity.sh
bash setup-azure-identity.sh --auth-method workload-identity \
  --resource-group YOUR_RESOURCE_GROUP \
  --aks-cluster YOUR_AKS_CLUSTER \
  --all-subscriptions
```

This script will:

1. Create a managed identity
2. Assign appropriate RBAC roles
3. Configure federated identity credentials
4. Output the configuration values for your Helm chart

**Manual Role Assignment:**

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

### Multi-Subscription Access

Holmes can automatically discover and switch between subscriptions within the same tenant. Just ensure your identity has the appropriate roles in each subscription.

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

## Testing the Connection

After deploying the Azure MCP server, verify it's working:

```bash
# Check pod status
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server

# Check logs
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server

# Health check
kubectl port-forward -n YOUR_NAMESPACE svc/RELEASE_NAME-azure-mcp-server 8000:8000
curl http://localhost:8000/health

# Ask Holmes
holmes ask "Can you list all resource groups in my Azure subscription?"
```

## Troubleshooting

### Authentication Issues

**Problem:** Pod logs show authentication errors

**Solutions:**

1. For Workload Identity: Verify federated identity credentials are configured correctly
   ```bash
   az identity federated-credential list \
     --identity-name YOUR_IDENTITY_NAME \
     --resource-group YOUR_RG
   ```

2. For Service Principal: Verify secret exists and contains correct credentials
   ```bash
   kubectl get secret azure-mcp-creds -n YOUR_NAMESPACE -o yaml
   ```

3. Check service account annotations
   ```bash
   kubectl get sa azure-api-mcp-sa -n YOUR_NAMESPACE -o yaml
   ```

### Permission Errors

**Problem:** Holmes reports "AuthorizationFailed" or "Forbidden" errors

**Solution:** Verify RBAC role assignments

```bash
# Check role assignments for your managed identity or service principal
az role assignment list --assignee YOUR_CLIENT_ID --output table
```

### Connection Timeouts

**Problem:** Holmes can't connect to the MCP server

**Solutions:**

1. Verify the service is running
   ```bash
   kubectl get svc -n YOUR_NAMESPACE | grep azure-mcp
   ```

2. Check network policy isn't blocking traffic
   ```bash
   kubectl get networkpolicy -n YOUR_NAMESPACE
   ```

3. Test connectivity from Holmes pod
   ```bash
   kubectl exec -it HOLMES_POD -n YOUR_NAMESPACE -- \
     curl http://RELEASE_NAME-azure-mcp-server.YOUR_NAMESPACE.svc.cluster.local:8000/health
   ```

### Subscription Access Issues

**Problem:** Can't query certain subscriptions

**Solution:** Verify your identity has access to all required subscriptions

```bash
# List accessible subscriptions
az account list --output table

# Check role assignments in specific subscription
az role assignment list \
  --assignee YOUR_CLIENT_ID \
  --subscription SUBSCRIPTION_ID
```

## Additional Resources

- [Azure MCP Server GitHub Repository](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/azure)
- [Workload Identity Setup Guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/azure#workload-identity-setup-for-aks)
- [Azure CLI Reference](https://learn.microsoft.com/en-us/cli/azure/)
- [Azure Workload Identity Documentation](https://azure.github.io/azure-workload-identity/docs/)
