# Kubernetes (MCP)

!!! note "Built-in Kubernetes toolsets are recommended for most users"
    Holmes includes built-in Kubernetes toolsets ([`kubernetes/core`](kubernetes.md), `kubernetes/logs`) that provide comprehensive cluster access — it uses bash to run kubectl commands directly, no additional setup required when deployed in-cluster.

    The Kubernetes MCP addon is for **advanced scenarios**: enterprise environments requiring OAuth/OIDC authentication or centralized access control via identity providers.

The [Kubernetes MCP server](https://github.com/containers/kubernetes-mcp-server) gives Holmes access to Kubernetes clusters via the MCP protocol, with support for OAuth/OIDC authentication.

## In-Cluster Setup (ServiceAccount)

The simplest setup — the MCP server runs in the same cluster it monitors, using a ServiceAccount for authentication.

### Step 1: Deploy

=== "Holmes Helm Chart"

    Add the following to your `values.yaml`:

    ```yaml
    # Disable built-in k8s toolsets to avoid overlap
    toolsets:
      kubernetes/core:
        enabled: false
      kubernetes/logs:
        enabled: false
      bash:
        enabled: false

    mcpAddons:
      kubernetes:
        enabled: true

        serviceAccount:
          create: true
          name: "k8s-mcp-sa"
          createClusterRoleBinding: true
          clusterRole: "view"

        config:
          readOnly: true
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      # Disable built-in k8s toolsets to avoid overlap
      toolsets:
        kubernetes/core:
          enabled: false
        kubernetes/logs:
          enabled: false
        bash:
          enabled: false

      mcpAddons:
        kubernetes:
          enabled: true

          serviceAccount:
            create: true
            name: "k8s-mcp-sa"
            createClusterRoleBinding: true
            clusterRole: "view"

          config:
            readOnly: true
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Step 2: Verify

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=k8s-mcp-server
```

## OAuth / OIDC Setup (Microsoft Entra ID)

Use OAuth/OIDC when cluster access is managed through Microsoft Entra ID (Azure AD) — for example, enterprise environments with centralized SSO.

In this mode, the MCP server validates OAuth tokens and passes them through to the Kubernetes API server. The ServiceAccount RBAC binding is not needed — permissions come from the OAuth token.

### Step 1: Enable Azure AD on your AKS cluster

Your AKS cluster must be configured for Azure AD authentication. Follow the [Microsoft guide to enable Azure AD integration on AKS](https://learn.microsoft.com/en-us/azure/aks/managed-azure-ad).

### Step 2: Create an Entra ID App Registration

1. In the Azure portal, go to **Microsoft Entra ID > App Registrations > New Registration**
2. Enter a name (e.g., `holmes-k8s-mcp`), select **Accounts in this organizational directory only**, and click **Register**
3. Under **Authentication > Platform configurations**, add a **Web** platform with redirect URI: `https://platform.robusta.dev/oauth/callback.html`
4. Under **API Permissions**, add the following delegated permissions:
      - **Azure Kubernetes Service AAD Server** (`6dae42f8-4368-4678-94ff-3960e28e3630`): `user.read`
      - **Microsoft Graph**: `email`, `openid`, `profile`
5. Click **Grant admin consent** for your tenant
6. Under **Certificates & Secrets**, create a new client secret and copy the value
7. From the **Overview** page, note your **Application (client) ID** and **Directory (tenant) ID**

### Step 3: Create the config.toml

Create a `config.toml` file:

```toml
require_oauth = true
authorization_url = "https://login.microsoftonline.com/YOUR_TENANT_ID/v2.0"
oauth_audience = "6dae42f8-4368-4678-94ff-3960e28e3630"
oauth_scopes = ["6dae42f8-4368-4678-94ff-3960e28e3630/.default", "openid", "profile"]
issuer_url = "https://sts.windows.net/YOUR_TENANT_ID/"
```

### Step 4: Create the Kubernetes Secret

```bash
kubectl create secret generic k8s-mcp-oauth-config \
  --from-file=config.toml=/path/to/config.toml \
  -n YOUR_NAMESPACE
```

### Step 5: Deploy

=== "Holmes Helm Chart"

    Add the following to your `values.yaml`:

    ```yaml
    # Disable built-in k8s toolsets to avoid overlap
    toolsets:
      kubernetes/core:
        enabled: false
      kubernetes/logs:
        enabled: false
      bash:
        enabled: false

    mcpAddons:
      kubernetes:
        enabled: true

        serviceAccount:
          create: true
          name: "k8s-mcp-sa"
          createClusterRoleBinding: false  # No RBAC — OAuth token provides permissions

        config:
          readOnly: true
          configSecret:
            secretName: "k8s-mcp-oauth-config"
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      # Disable built-in k8s toolsets to avoid overlap
      toolsets:
        kubernetes/core:
          enabled: false
        kubernetes/logs:
          enabled: false
        bash:
          enabled: false

      mcpAddons:
        kubernetes:
          enabled: true

          serviceAccount:
            create: true
            name: "k8s-mcp-sa"
            createClusterRoleBinding: false  # No RBAC — OAuth token provides permissions

          config:
            readOnly: true
            configSecret:
              secretName: "k8s-mcp-oauth-config"
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Step 6: Verify

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=k8s-mcp-server
```

## Configuration Reference

| Value | Description | Default |
|-------|-------------|---------|
| `enabled` | Enable the Kubernetes MCP addon | `false` |
| `image` | Container image | `kubernetes-mcp-server:0.0.60-oauth` |
| `registry` | Container registry | `us-central1-docker.pkg.dev/genuine-flight-317411/mcp` |
| `serviceAccount.create` | Create a ServiceAccount | `true` |
| `serviceAccount.name` | ServiceAccount name | `k8s-mcp-sa` |
| `serviceAccount.annotations` | ServiceAccount annotations | `{}` |
| `serviceAccount.createClusterRoleBinding` | Bind a ClusterRole to the SA | `true` |
| `serviceAccount.clusterRole` | ClusterRole to bind | `view` |
| `config.readOnly` | Disable all write operations | `true` |
| `config.disableDestructive` | Disable only destructive operations | `false` |
| `config.toolsets` | Comma-separated toolsets to enable (empty = `config,core`) | `""` |
| `config.logLevel` | Log verbosity (0-9, like kubectl) | `""` |
| `config.extraArgs` | Extra CLI arguments | `[]` |
| `config.configSecret.secretName` | Secret containing config.toml (for OAuth) | `""` |
| `config.configSecret.secretKey` | Key in the config secret | `config.toml` |
| `resources` | CPU/memory requests and limits | 128Mi/512Mi |
| `networkPolicy.enabled` | Create a NetworkPolicy | `false` |
| `llmInstructions` | Custom LLM instructions (overrides default) | `""` |

All values are under `mcpAddons.kubernetes` (Holmes chart) or `holmes.mcpAddons.kubernetes` (Robusta chart).

## Common Use Cases

```
"List all pods in CrashLoopBackOff across all namespaces"
```

```
"What events are happening in the production namespace?"
```

```
"Show me the resource requests and limits for all deployments in namespace backend"
```

```
"Why is the checkout-api pod not scheduling?"
```
