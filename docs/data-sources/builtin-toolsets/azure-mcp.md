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

### Creating a Service Principal (non-AKS clusters)

On non-AKS clusters (e.g. AWS EKS, GKE, self-managed) workload identity is not available, so use **service-principal** authentication — the `authMethod: "service-principal"` option shown in [Step 2](#step-2-deploy-with-helm). If you don't already have service-principal credentials, create one in the target tenant:

```bash
# Log into the target tenant (skip if you are already in it)
az login --tenant YOUR_TENANT_ID

# Create the service principal (no role yet - RBAC is assigned separately below)
az ad sp create-for-rbac --name "holmes-azure-mcp" --skip-assignment
```

The command prints the values you need:

```json
{
  "appId":    "...",   // -> AZURE_CLIENT_ID
  "password": "...",   // -> AZURE_CLIENT_SECRET (shown only once)
  "tenant":   "..."    // -> AZURE_TENANT_ID
}
```

Then:

1. Grant the service principal the roles from the [RBAC table](#step-1-set-up-azure-rbac-roles) (at minimum **Reader**) on the target subscription:

    ```bash
    az role assignment create \
      --assignee YOUR_CLIENT_ID \
      --role Reader \
      --scope /subscriptions/YOUR_SUBSCRIPTION_ID
    ```

2. Put `appId`/`password` into the `azure-mcp-creds` secret and set `tenantId`/`subscriptionId` in the **Service Principal** Helm values from [Step 2](#step-2-deploy-with-helm).

### Multi-Subscription Access

Holmes can automatically discover and switch between subscriptions within the same tenant. Just ensure your identity has the appropriate roles in each subscription.

### Adding Another Azure Account (Different Credentials)

The Helm chart deploys a single Azure MCP server. When you need Holmes to reach **another Azure account, tenant, or subscription that uses different credentials** (for example a separate service principal per environment), deploy an additional, standalone MCP server from the ready-made manifest below and register it with Holmes as an extra `mcp_servers` entry.

The manifest ([`examples/azure-mcp-additional-instance.yaml`](https://github.com/HolmesGPT/holmesgpt/blob/master/examples/azure-mcp-additional-instance.yaml)) is self-contained: it creates a Secret, ConfigMap, ServiceAccount, Deployment, Service, and (optionally) a NetworkPolicy — everything the extra server needs.

!!! warning "Every instance needs a unique name"

    All resource names are built from a `replaceme` placeholder. You **must** replace it with a unique name (Step 2) before applying. Deploying a second copy without changing it will **overwrite** the first instance's Secret, Deployment, Service, etc. The namespace placeholder is also mandatory so nothing lands in `default` by accident.

**Step 1: Download the manifest**

```bash
curl -O https://raw.githubusercontent.com/HolmesGPT/holmesgpt/master/examples/azure-mcp-additional-instance.yaml
```

**Step 2: Set a unique name and the namespace (required)**

The manifest uses two placeholders that must be replaced before applying:

- `replaceme` → a **unique, DNS-safe** name for this instance (lowercase letters, digits, `-`), e.g. `prod`, `dev`, `tenant-a`. Every resource name and label is derived from it, so a unique value guarantees this instance never collides with another.
- `NAMESPACE_REPLACE_ME` → the **same namespace the `robusta`/`holmes` release is installed in** (where the Holmes pod runs). Deploying into Holmes's own namespace keeps things simple: the Service resolves and the bundled NetworkPolicy matches the Holmes pods out of the box. It is set explicitly on every resource so nothing lands in `default` by accident.

Not sure which namespace that is? Find it with:

```bash
kubectl get pods -A -l app.kubernetes.io/name=holmes
```

For example, if Holmes runs in the `monitoring` namespace and you want to name this instance `prod`, replace both placeholders in one shot:

```bash
sed -i '' 's/replaceme/prod/g; s/NAMESPACE_REPLACE_ME/monitoring/g' azure-mcp-additional-instance.yaml   # macOS
# sed -i 's/replaceme/prod/g; s/NAMESPACE_REPLACE_ME/monitoring/g' azure-mcp-additional-instance.yaml     # Linux
```

That example yields the `prod` instance in the `monitoring` namespace — i.e. `azure-prod-secret`, `azure-prod-configmap`, `azure-prod-serviceaccount`, `azure-prod-deployment`, `azure-prod-service`, and `azure-prod-networkpolicy`.

**Step 3: Set the credentials in the Secret**

Open the downloaded file and find the `Secret` block. **Leave `name` and `namespace` alone** — Step 2 already set them. Edit **only** the two credential values, replacing `YOUR_CLIENT_ID` and `YOUR_CLIENT_SECRET` with this account's service principal (create one with [Creating a Service Principal](#creating-a-service-principal-non-aks-clusters)):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: azure-replaceme-secret      # leave as-is (Step 2 set this, e.g. azure-prod-secret)
  namespace: NAMESPACE_REPLACE_ME   # leave as-is (Step 2 set this, e.g. monitoring)
type: Opaque
stringData:
  AZURE_CLIENT_ID: "YOUR_CLIENT_ID"          # <-- EDIT: service principal appId
  AZURE_CLIENT_SECRET: "YOUR_CLIENT_SECRET"  # <-- EDIT: service principal password
```

**Step 4: Set the account details in the ConfigMap**

In the same file, find the `ConfigMap` block. Again **leave `name` and `namespace` alone**. Edit **only** `YOUR_TENANT_ID` and `YOUR_SUBSCRIPTION_ID`; keep `AZ_AUTH_METHOD: "service-principal"` unless you know you need a different method:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: azure-replaceme-configmap   # leave as-is (Step 2 set this, e.g. azure-prod-configmap)
  namespace: NAMESPACE_REPLACE_ME   # leave as-is (Step 2 set this, e.g. monitoring)
data:
  AZURE_TENANT_ID: "YOUR_TENANT_ID"              # <-- EDIT: service principal tenant
  AZURE_SUBSCRIPTION_ID: "YOUR_SUBSCRIPTION_ID"  # <-- EDIT: subscription to investigate
  AZ_AUTH_METHOD: "service-principal"            # leave as-is
  READ_ONLY_MODE: "true"                         # "false" to allow writes (use with caution)
```

**Step 5: (Optional) Remove the NetworkPolicy**

The manifest includes a NetworkPolicy that restricts the server to Holmes traffic. If your cluster does not enforce NetworkPolicies, delete that block (the last document in the file).

**Step 6: Apply the manifest**

```bash
kubectl apply -f azure-mcp-additional-instance.yaml

# verify it comes up (use the name/namespace you chose in Step 2)
kubectl get pods -n monitoring -l app=azure-prod
kubectl logs  -n monitoring -l app=azure-prod
```

**Step 7: Register the server with Holmes (Helm values)**

Add an `mcp_servers` entry pointing at the new Service, then upgrade the release. The examples below use the same `prod` / `monitoring` values from Step 2 — **adjust them to match the name and namespace you chose**:

- The `url` host is `<name>-service.<namespace>.svc.cluster.local`. With the Step 2 example (`prod` / `monitoring`) that is `azure-prod-service.monitoring.svc.cluster.local`. If you named the instance `dev` in the `holmes` namespace, use `azure-dev-service.holmes.svc.cluster.local` instead.
- Use a **unique** `mcp_servers` key per instance (e.g. `azure_api_prod`, `azure_api_dev`) so multiple accounts don't overwrite each other.

=== "Holmes Helm Chart"

    ```yaml
    mcp_servers:
      azure_api_prod:
        description: "Azure API MCP Server (prod account) - comprehensive Azure service access. Execute any Azure CLI commands."
        config:
          url: "http://azure-prod-service.monitoring.svc.cluster.local:8000/mcp"
          mode: streamable-http
          icon_url: "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/microsoft-azure.svg"
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      mcp_servers:
        azure_api_prod:
          description: "Azure API MCP Server (prod account) - comprehensive Azure service access. Execute any Azure CLI commands."
          config:
            url: "http://azure-prod-service.monitoring.svc.cluster.local:8000/mcp"
            mode: streamable-http
            icon_url: "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/microsoft-azure.svg"
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

Once Holmes restarts, the account is available as the `azure_api_prod` toolset. To add another account, download a **fresh copy** of the manifest and repeat from Step 2 with a **different** unique name (e.g. `dev`) and a new `mcp_servers` key.

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
