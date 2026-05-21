# Kubernetes (MCP)

--8<-- "snippets/kubernetes_toolset_picker.md"

The [Kubernetes MCP server](https://github.com/containers/kubernetes-mcp-server) gives Holmes access to Kubernetes clusters via the MCP protocol, with support for OAuth/OIDC authentication. It is intended to **replace** the built-in `kubernetes/core` and `kubernetes/logs` toolsets — the Helm examples below disable those to avoid overlap.

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

In this mode the MCP server validates OAuth tokens and passes them through to the Kubernetes API server, so each user's calls hit the API with their own identity. The ServiceAccount ClusterRoleBinding is not needed — permissions come from the OAuth token.

Two pieces of config drive the flow:

- **Server-side** (`mcpAddons.kubernetes.config.serverConfig`) — TOML that the MCP server itself uses to validate incoming bearer tokens.
- **Holmes-side** (`mcpAddons.kubernetes.config.oauth`) — tells Holmes which OAuth endpoints to send users to. Without this, Holmes can't drive the browser login flow.

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

### Step 3: Store the client secret

Create a Kubernetes Secret with the Entra ID client secret you copied in Step 2.6, then expose it on the Holmes pod as `MCP_OAUTH_CLIENT_SECRET`. The Helm values in Step 4 reference it via `{{ env.MCP_OAUTH_CLIENT_SECRET }}` so the secret never appears in your values file.

```bash
kubectl create secret generic mcp-oauth-credentials \
  --from-literal=client-secret='<CLIENT_SECRET>' \
  -n YOUR_NAMESPACE \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Step 4: Deploy

=== "Holmes Helm Chart"

    Add the following to your `values.yaml` (replace `<TENANT_ID>` and `<CLIENT_ID>`):

    ```yaml
    # Inject the OAuth client secret as an env var that the chart reads via Jinja.
    additionalEnvVars:
      - name: MCP_OAUTH_CLIENT_SECRET
        valueFrom:
          secretKeyRef:
            name: mcp-oauth-credentials
            key: client-secret

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

          # Server-side: how the MCP server validates incoming JWTs.
          # The chart bakes this into a Secret mounted at /etc/kubernetes-mcp/config.toml.
          serverConfig: |
            require_oauth = true
            authorization_url = "https://login.microsoftonline.com/<TENANT_ID>/v2.0"
            oauth_audience    = "6dae42f8-4368-4678-94ff-3960e28e3630"
            oauth_scopes      = ["6dae42f8-4368-4678-94ff-3960e28e3630/.default", "openid", "profile"]
            issuer_url        = "https://sts.windows.net/<TENANT_ID>/"

          # Holmes-side: how Holmes drives the browser OAuth flow for end users.
          oauth:
            enabled: true
            client_id:     "<CLIENT_ID>"
            client_secret: "{{ env.MCP_OAUTH_CLIENT_SECRET }}"
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add the following to your `generated_values.yaml` (replace `<TENANT_ID>` and `<CLIENT_ID>`):

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MCP_OAUTH_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: mcp-oauth-credentials
              key: client-secret

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

            serverConfig: |
              require_oauth = true
              authorization_url = "https://login.microsoftonline.com/<TENANT_ID>/v2.0"
              oauth_audience    = "6dae42f8-4368-4678-94ff-3960e28e3630"
              oauth_scopes      = ["6dae42f8-4368-4678-94ff-3960e28e3630/.default", "openid", "profile"]
              issuer_url        = "https://sts.windows.net/<TENANT_ID>/"

            oauth:
              enabled: true
              client_id:     "<CLIENT_ID>"
              client_secret: "{{ env.MCP_OAUTH_CLIENT_SECRET }}"

    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Step 5: Verify

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=k8s-mcp-server
```

When you ask Holmes a Kubernetes question for the first time, the Robusta UI will open a Microsoft login window. After signing in, Holmes uses your Azure-issued token for every `kubernetes_*` call — RBAC is enforced per user on the API server.

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
