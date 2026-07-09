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

### Multiple Azure MCP Instances

When you need to connect to multiple Azure tenants or subscriptions with different credentials, deploy multiple Azure MCP instances. Each instance runs in its own pod with its own configuration, service account, and network policies.

**Key Points for Multiple Instances:**

- **Instance Name**: The `name` field must be unique across all instances (used for resource naming)
- **Unique Ports**: Each instance must have a unique `service.port` to avoid conflicts
- **Unique Service Accounts**: Recommended to use unique service account names per instance
- **Unique Credentials**: Each instance can have its own tenant ID, subscription ID, and credentials
- **Backward Compatible**: The single `azure` configuration still works — you only need `azureInstances` when deploying multiple instances
- **Secrets are Optional**: Secrets are only required when using `service-principal` authentication. For `workload-identity` and `managed-identity`, no secrets are needed.

#### Example 1: Multiple Instances with Workload Identity (AKS - No Secrets Required)

Use this for AKS clusters with Workload Identity enabled. No secrets are needed. Each instance can have its own custom LLM instructions to guide Holmes' investigation behavior.

```yaml
mcpAddons:
  # Disable the single instance (backward compatible)
  azure:
    enabled: false

  # Configure multiple instances with Workload Identity
  azureInstances:
    - name: "prod"
      enabled: true

      serviceAccount:
        create: true
        name: "azure-prod-mcp-sa"
        annotations:
          azure.workload.identity/client-id: "prod-client-id"
          azure.workload.identity/tenant-id: "prod-tenant-id"

      image: "azure-cli-mcp:1.0.2"
      registry: "us-central1-docker.pkg.dev/genuine-flight-317411/mcp"

      config:
        tenantId: "prod-tenant-id"
        subscriptionId: "prod-subscription-id"
        clientId: "prod-client-id"
        authMethod: "workload-identity"
        readOnlyMode: true
        timeout: "120"

      service:
        port: 8000

      resources:
        requests:
          memory: "256Mi"
          cpu: "100m"
        limits:
          memory: "512Mi"

      networkPolicy:
        enabled: false

      # Custom instructions for production investigations
      llmInstructions: |
        ## Production Azure Investigation Guidelines
        
        **CRITICAL**: You are investigating PRODUCTION resources. Exercise extreme caution.
        
        **Before making any changes:**
        1. Check Azure Activity Log for recent changes (last 24 hours)
        2. Verify change windows and maintenance schedules
        3. Always report findings to the on-call engineer before suggesting fixes
        4. For critical systems, request approval before proceeding
        
        **Focus areas for prod:**
        - Check Azure Monitor alerts and metrics
        - Review cost anomalies (may indicate issues)
        - Verify RBAC role assignments haven't been modified
        - Check for service outages in Activity Log

    - name: "staging"
      enabled: true

      serviceAccount:
        create: true
        name: "azure-staging-mcp-sa"
        annotations:
          azure.workload.identity/client-id: "staging-client-id"
          azure.workload.identity/tenant-id: "staging-tenant-id"

      image: "azure-cli-mcp:1.0.2"
      registry: "us-central1-docker.pkg.dev/genuine-flight-317411/mcp"

      config:
        tenantId: "staging-tenant-id"
        subscriptionId: "staging-subscription-id"
        clientId: "staging-client-id"
        authMethod: "workload-identity"
        readOnlyMode: true
        timeout: "120"

      service:
        port: 8001  # Each instance needs a unique port

      resources:
        requests:
          memory: "256Mi"
          cpu: "100m"
        limits:
          memory: "512Mi"

      networkPolicy:
        enabled: false

      # Custom instructions for staging investigations
      llmInstructions: |
        ## Staging Azure Investigation Guidelines
        
        **SAFE TO EXPERIMENT**: You are investigating STAGING resources. 
        
        **Investigation approach:**
        1. Feel free to gather detailed diagnostics and check configurations
        2. You can safely query resources without worry of impacting production
        3. Focus on testing, validation, and reproducing issues
        4. Check Azure Monitor for metrics and diagnostics
        
        **Use for:**
        - Testing Azure configurations before production deployment
        - Reproducing reported issues in a safe environment
        - Validating fixes and changes
        - Learning Azure resource behavior
```

#### Example 2: Multiple Instances with Service Principal (Non-AKS Clusters - Secrets Required)

Use this for non-AKS clusters or when you prefer service principal authentication. **Requires creating secrets.**

**Step 1: Create secrets for each instance**

```bash
# Create secret for prod instance
kubectl create secret generic azure-prod-mcp-creds \
  --from-literal=AZURE_CLIENT_ID=prod-client-id \
  --from-literal=AZURE_CLIENT_SECRET=prod-client-secret \
  -n YOUR_NAMESPACE

# Create secret for staging instance
kubectl create secret generic azure-staging-mcp-creds \
  --from-literal=AZURE_CLIENT_ID=staging-client-id \
  --from-literal=AZURE_CLIENT_SECRET=staging-client-secret \
  -n YOUR_NAMESPACE
```

**Step 2: Configure Helm chart**

```yaml
mcpAddons:
  azure:
    enabled: false

  azureInstances:
    - name: "prod"
      enabled: true

      serviceAccount:
        create: true
        name: "azure-prod-mcp-sa"

      image: "azure-cli-mcp:1.0.2"
      registry: "us-central1-docker.pkg.dev/genuine-flight-317411/mcp"

      config:
        tenantId: "prod-tenant-id"
        subscriptionId: "prod-subscription-id"
        authMethod: "service-principal"
        readOnlyMode: true
        timeout: "120"

      secretName: "azure-prod-mcp-creds"  # Secret created in Step 1

      service:
        port: 8000

      resources:
        requests:
          memory: "256Mi"
          cpu: "100m"
        limits:
          memory: "512Mi"

      networkPolicy:
        enabled: false

      # Custom instructions for production investigations
      llmInstructions: |
        ## Production Azure Investigation Guidelines
        
        **CRITICAL**: You are investigating PRODUCTION resources. Exercise extreme caution.
        
        **Before making any changes:**
        1. Check Azure Activity Log for recent changes (last 24 hours)
        2. Verify change windows and maintenance schedules
        3. Always report findings to the on-call engineer before suggesting fixes
        4. For critical systems, request approval before proceeding
        
        **Focus areas for prod:**
        - Check Azure Monitor alerts and metrics
        - Review cost anomalies (may indicate issues)
        - Verify RBAC role assignments haven't been modified
        - Check for service outages in Activity Log

    - name: "staging"
      enabled: true

      serviceAccount:
        create: true
        name: "azure-staging-mcp-sa"

      image: "azure-cli-mcp:1.0.2"
      registry: "us-central1-docker.pkg.dev/genuine-flight-317411/mcp"

      config:
        tenantId: "staging-tenant-id"
        subscriptionId: "staging-subscription-id"
        authMethod: "service-principal"
        readOnlyMode: true
        timeout: "120"

      secretName: "azure-staging-mcp-creds"  # Secret created in Step 1

      service:
        port: 8001

      resources:
        requests:
          memory: "256Mi"
          cpu: "100m"
        limits:
          memory: "512Mi"

      networkPolicy:
        enabled: false

      # Custom instructions for staging investigations
      llmInstructions: |
        ## Staging Azure Investigation Guidelines
        
        **SAFE TO EXPERIMENT**: You are investigating STAGING resources. 
        
        **Investigation approach:**
        1. Feel free to gather detailed diagnostics and check configurations
        2. You can safely query resources without worry of impacting production
        3. Focus on testing, validation, and reproducing issues
        4. Check Azure Monitor for metrics and diagnostics
        
        **Use for:**
        - Testing Azure configurations before production deployment
        - Reproducing reported issues in a safe environment
        - Validating fixes and changes
        - Learning Azure resource behavior
```

#### Example 3: Mixed Setup (Workload Identity + Service Principal)

Use this when you have multiple clusters or authentication methods. Each instance maintains its own custom LLM instructions.

```yaml
mcpAddons:
  azure:
    enabled: false

  azureInstances:
    # AKS cluster with Workload Identity - no secret
    - name: "prod-aks"
      enabled: true

      serviceAccount:
        create: true
        annotations:
          azure.workload.identity/client-id: "prod-client-id"
          azure.workload.identity/tenant-id: "prod-tenant-id"

      config:
        tenantId: "prod-tenant-id"
        subscriptionId: "prod-subscription-id"
        clientId: "prod-client-id"
        authMethod: "workload-identity"
        readOnlyMode: true

      service:
        port: 8000

      llmInstructions: |
        ## Production AKS on Azure Investigation
        
        You have access to a production AKS cluster running on Azure.
        Be cautious - focus on diagnostics, not changes.
        
        Priority checks:
        - Node health and capacity
        - Pod resource constraints
        - Network connectivity issues
        - Azure storage and networking

    # Non-AKS cluster with Service Principal - requires secret
    - name: "staging-on-prem"
      enabled: true

      serviceAccount:
        create: true

      config:
        tenantId: "staging-tenant-id"
        subscriptionId: "staging-subscription-id"
        authMethod: "service-principal"
        readOnlyMode: true

      secretName: "azure-staging-mcp-creds"  # Secret required for this instance

      service:
        port: 8001

      llmInstructions: |
        ## Staging On-Premises Azure Investigation
        
        You have access to on-premises staging infrastructure integrated with Azure.
        This is a lower-risk environment - safe to investigate thoroughly.
        
        Investigation focus:
        - Azure-to-on-prem connectivity
        - Hybrid network configuration
        - Resource synchronization
        - Staging test results
```

**Secret Creation for Mixed Setup:**

```bash
# Only create secret for instances using service-principal auth
kubectl create secret generic azure-staging-mcp-creds \
  --from-literal=AZURE_CLIENT_ID=staging-client-id \
  --from-literal=AZURE_CLIENT_SECRET=staging-client-secret \
  -n YOUR_NAMESPACE
```

#### Custom LLM Instructions Per Instance

Each Azure instance can have its own custom `llmInstructions` field to guide Holmes' investigation behavior. This is optional — if omitted, Holmes uses the default Azure MCP instructions.

**Use custom instructions to:**
- **Distinguish environments**: Tell Holmes if it's investigating production vs staging
- **Set investigation scope**: Limit what Holmes should investigate per tenant
- **Define escalation paths**: Specify when to report findings vs making changes
- **Provide context**: Include environment-specific priorities and known issues

**Example: Production vs Staging Instructions**

```yaml
azureInstances:
  - name: "prod"
    # ... config ...
    llmInstructions: |
      # CRITICAL: Production environment
      - Always verify changes in Activity Log first
      - Report findings before taking action
      - Check maintenance windows before suggesting changes
  
  - name: "staging"
    # ... config ...
    llmInstructions: |
      # SAFE: Staging environment  
      - Feel free to investigate thoroughly
      - Safe to gather detailed diagnostics
      - No approval needed for diagnostics
```

If you omit `llmInstructions`, Holmes uses the default Azure MCP instructions (which cover general Azure investigation patterns).

#### When to Use Each Authentication Method

| Method | Best For | Secret Required | Example |
|--------|----------|-----------------|---------|
| `workload-identity` | AKS clusters | ❌ No | Production AKS environments |
| `service-principal` | Non-AKS clusters, legacy setups | ✅ Yes | On-premises, EKS, GKE |
| `managed-identity` | AKS with node-level managed identity | ❌ No | Simplified AKS setup |

When using multiple instances, Holmes will route requests to the appropriate instance based on the context (tenant/subscription). You can specify which instance to use in your investigation prompt:

```
"List all resource groups in the prod tenant"
"Get VM details from the staging subscription"
```

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
