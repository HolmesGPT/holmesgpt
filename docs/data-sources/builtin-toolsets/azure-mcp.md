# Azure (MCP)

The Azure MCP server provides comprehensive access to Azure services through the Azure CLI. It enables Holmes to investigate Azure infrastructure issues, analyze Activity Log events, examine network configurations, troubleshoot AKS clusters, investigate database issues, and much more.

## Overview

The Azure MCP server is deployed as a separate pod in your cluster when using the Holmes or Robusta Helm charts. For CLI users, you'll need to deploy the MCP server manually and configure Holmes to connect to it.

The server runs in your Kubernetes cluster and can investigate both Azure infrastructure and AKS-hosted workloads. It supports multiple authentication methods including Workload Identity (recommended for AKS), Service Principal, and Managed Identity.

## Prerequisites

Before deploying the Azure MCP server, ensure you have:

- An Azure subscription with appropriate permissions
- For AKS clusters: Workload Identity or Managed Identity configured (recommended)
- For Service Principal auth: Client ID and Client Secret
- Azure RBAC roles assigned based on your investigation needs (see IAM Configuration below)

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the Azure MCP server first, then configure Holmes to connect to it.

    ### Step 1: Deploy the Azure MCP Server

    Create a file named `azure-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: azure-mcp-sa
      namespace: holmes-mcp
      labels:
        azure.workload.identity/use: "true"
      # For Workload Identity, add annotations:
      # annotations:
      #   azure.workload.identity/client-id: "YOUR_CLIENT_ID"
      #   azure.workload.identity/tenant-id: "YOUR_TENANT_ID"
    ---
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: azure-mcp-config
      namespace: holmes-mcp
    data:
      AZURE_TENANT_ID: "YOUR_TENANT_ID"
      AZURE_SUBSCRIPTION_ID: "YOUR_SUBSCRIPTION_ID"
      AZ_AUTH_METHOD: "workload-identity"  # Options: workload-identity, managed-identity, service-principal
      READ_ONLY_MODE: "true"
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: azure-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: azure-mcp-server
      template:
        metadata:
          labels:
            app: azure-mcp-server
            azure.workload.identity/use: "true"
        spec:
          serviceAccountName: azure-mcp-sa
          containers:
          - name: azure-mcp
            image: me-west1-docker.pkg.dev/robusta-development/development/azure-cli-mcp:1.0.1
            imagePullPolicy: IfNotPresent
            args:
              - "--transport"
              - "streamable-http"
              - "--host"
              - "0.0.0.0"
              - "--port"
              - "8000"
              - "--readonly"
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: AZURE_TENANT_ID
              valueFrom:
                configMapKeyRef:
                  name: azure-mcp-config
                  key: AZURE_TENANT_ID
            - name: AZURE_SUBSCRIPTION_ID
              valueFrom:
                configMapKeyRef:
                  name: azure-mcp-config
                  key: AZURE_SUBSCRIPTION_ID
            - name: AZ_AUTH_METHOD
              valueFrom:
                configMapKeyRef:
                  name: azure-mcp-config
                  key: AZ_AUTH_METHOD
            - name: AZURE_CLIENT_ID
              value: "YOUR_CLIENT_ID"  # For workload identity or managed identity
            # For service principal authentication, add:
            # - name: AZURE_CLIENT_SECRET
            #   valueFrom:
            #     secretKeyRef:
            #       name: azure-mcp-creds
            #       key: AZURE_CLIENT_SECRET
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
            livenessProbe:
              httpGet:
                path: /health
                port: http
              initialDelaySeconds: 30
              periodSeconds: 30
            readinessProbe:
              httpGet:
                path: /health
                port: http
              initialDelaySeconds: 10
              periodSeconds: 10
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: azure-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: azure-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f azure-mcp-deployment.yaml
    ```

    ### Step 2: Configure Azure Authentication

    Choose one of these authentication methods:

    **Option A: Workload Identity (Recommended for AKS)**

    Follow the [Workload Identity setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/azure#workload-identity-setup-for-aks) to:
    1. Enable Workload Identity on your AKS cluster
    2. Create a managed identity with appropriate permissions
    3. Establish federated identity credentials
    4. Configure the ServiceAccount annotations

    **Option B: Managed Identity**

    For clusters with node-level managed identity:
    1. Ensure your AKS nodes have a managed identity assigned
    2. Update the deployment to use `authMethod: managed-identity`
    3. Set the AZURE_CLIENT_ID to your managed identity's client ID

    **Option C: Service Principal**

    For service principal authentication:
    1. Create a service principal and assign appropriate roles
    2. Create a Kubernetes secret with the credentials:

    ```bash
    kubectl create secret generic azure-mcp-creds \
      --from-literal=AZURE_CLIENT_ID=YOUR_CLIENT_ID \
      --from-literal=AZURE_CLIENT_SECRET=YOUR_CLIENT_SECRET \
      -n holmes-mcp
    ```

    3. Update the deployment to reference the secret (uncomment the secret reference in the YAML above)
    4. Set `AZ_AUTH_METHOD: "service-principal"` in the ConfigMap

    ### Step 3: Configure Holmes CLI

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      azure_api:
        description: "Azure API MCP Server - comprehensive Azure service access via Azure CLI"
        url: "http://azure-mcp-server.holmes-mcp.svc.cluster.local:8000"
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

    ### Step 4: Port Forwarding (Optional for Local Testing)

    If running Holmes CLI locally and need to access the MCP server:

    ```bash
    kubectl port-forward -n holmes-mcp svc/azure-mcp-server 8000:8000
    ```

    Then update the URL in config.yaml to:
    ```yaml
    url: "http://localhost:8000"
    ```

=== "Holmes Helm Chart"

    ### Workload Identity Authentication (Recommended for AKS)

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

    ### Service Principal Authentication

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

    ### Managed Identity Authentication

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

    ### Workload Identity Authentication (Recommended for AKS)

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

    ### Service Principal Authentication

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

    ### Managed Identity Authentication

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

The Azure MCP server requires appropriate Azure RBAC roles to investigate resources. The specific roles depend on what you want Holmes to investigate:

**Recommended Roles for Common Scenarios:**

- **Read-Only Investigation (Minimum):**
  - Reader role on the subscription or specific resource groups
  - Provides read access to all resources but cannot make changes

- **AKS Troubleshooting:**
  - Reader role for general AKS investigation
  - Azure Kubernetes Service Cluster User Role (for kubectl access via az aks get-credentials)
  - Log Analytics Reader (if using Container Insights)

- **Network Investigation:**
  - Reader role
  - Network Contributor (if you need to run network diagnostics)

- **Comprehensive Investigation:**
  - Reader role on subscription
  - Log Analytics Reader
  - Monitoring Reader
  - Cost Management Reader (for cost analysis)

**Setup Script:**

For Workload Identity setup with appropriate roles, use the helper script:

```bash
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/azure/setup-workload-identity.sh
bash setup-workload-identity.sh
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

The Azure MCP server can query across multiple subscriptions within the same tenant:

```bash
# List all accessible subscriptions
az account list --output table

# Switch subscription context
az account set --subscription "subscription-name-or-id"

# Query specific subscription
az vm list --subscription "subscription-id"
```

Holmes can automatically discover and switch between subscriptions during investigations.

## Testing the Connection

After deploying the Azure MCP server, verify it's working:

### Test 1: Check Pod Status

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server
```

### Test 2: Check Logs

```bash
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=azure-mcp-server
```

### Test 3: Health Check

```bash
kubectl port-forward -n YOUR_NAMESPACE svc/RELEASE_NAME-azure-mcp-server 8000:8000
curl http://localhost:8000/health
```

### Test 4: Ask Holmes

```bash
holmes ask "Can you list all resource groups in my Azure subscription?"
```

## Capabilities

The Azure MCP server provides access to all Azure services through the Azure CLI. Common investigation patterns include:

### Activity Log Investigation
- Query recent API calls and configuration changes
- Find who made specific changes
- Correlate changes with issue timelines
- Audit security events

### AKS Cluster Investigation
- Check cluster status and configuration
- Review node pool health
- Investigate pod scheduling issues
- Analyze cluster diagnostics

### Networking Troubleshooting
- Examine Network Security Groups
- Review Virtual Network configurations
- Check Application Gateway and Load Balancer status
- Investigate connectivity issues

### Database Issues
- Check database server status (SQL, PostgreSQL, MySQL)
- Review firewall rules and network access
- Analyze connection policies
- Monitor failover group status

### Certificate Management
- Review certificates in Key Vault
- Check certificate expiration dates
- Investigate Application Gateway SSL configuration
- Analyze cert-manager integration for AKS

### Storage Investigation
- Check storage account configuration
- Review network rules and private endpoints
- Analyze storage metrics
- Investigate access issues

### Cost Analysis
- Query current costs by resource group
- Review budgets and spending trends
- Get cost optimization recommendations
- Identify expensive resources

### Historical Log Analysis
- Query Log Analytics for deleted pod logs
- Investigate past incidents using Azure Monitor
- Retrieve logs for resources that no longer exist

## Common Use Cases

### AKS Pod Networking Issues

```
"Pods in namespace production can't reach Azure SQL database"
```

Holmes will:
1. Check pod network configuration
2. Examine NSG rules on AKS subnet
3. Review Azure SQL firewall rules
4. Check Activity Log for recent network changes
5. Provide root cause analysis

### Certificate Expiration Problems

```
"Our ingress is showing TLS errors since yesterday"
```

Holmes will:
1. Check certificate status in Key Vault
2. Review Application Gateway SSL configuration
3. Examine Activity Log for certificate operations
4. Verify cert-manager configuration
5. Identify expired or misconfigured certificates

### AKS Cluster Upgrade Issues

```
"After AKS upgrade, some pods are failing to schedule"
```

Holmes will:
1. Check cluster and node pool status
2. Review upgrade history in Activity Log
3. Examine node resource capacity
4. Check for version compatibility issues
5. Provide remediation steps

### Database Connection Timeouts

```
"Applications intermittently can't connect to PostgreSQL since 2 PM"
```

Holmes will:
1. Check PostgreSQL server status
2. Review firewall rules and VNet rules
3. Query Activity Log for configuration changes around 2 PM
4. Examine connection policy settings
5. Correlate timing with any infrastructure changes

### Cost Spike Investigation

```
"Our Azure costs increased 50% last week"
```

Holmes will:
1. Query cost data by resource group and service
2. Identify resources with increased usage
3. Review Activity Log for new resource deployments
4. Provide cost breakdown and recommendations

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
