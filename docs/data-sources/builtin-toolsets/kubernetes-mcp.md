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

## Multi-Cluster Setup (Mounted Kubeconfig)

Use this mode when you want **one Holmes pod to investigate multiple Kubernetes clusters** from a single place. Holmes still runs inside one "home" cluster, but instead of using its in-pod ServiceAccount it authenticates to every target cluster (including, optionally, its home cluster) using credentials packed into a kubeconfig file you mount as a Secret.

**How this differs from the other two modes:**

- **In-Cluster (ServiceAccount)** — Holmes can only see the cluster it's deployed in. Auth is the pod's own ServiceAccount.
- **OAuth / OIDC** — Holmes runs in one cluster; each end-user's identity is passed through to the API server. Cluster-side RBAC is enforced per user.
- **Multi-Cluster (this mode)** — Holmes uses pre-issued tokens (one per cluster) bundled in a kubeconfig. Every applicable MCP tool exposes a `context` argument so the LLM can pick which cluster to query for each step of an investigation.

### Step 1: Generate a kubeconfig for Holmes

A kubeconfig is a YAML file containing three lists: `clusters` (API server URL + CA cert), `users` (credentials), and `contexts` (a named pairing of one cluster with one user). The k8s-mcp-server uses the **context name** as the cluster identifier — pick names you'd be comfortable seeing in tool calls (e.g. `prod-eu`, `staging`, `dev`).

For each cluster you want Holmes to access, you need a ServiceAccount with a long-lived token. Cloud auth plugins like `aws-iam-authenticator`, `gke-gcloud-auth-plugin`, and `kubelogin` **do not work inside the MCP server pod** — you must use static credentials.

Run the following against **each target cluster** (switch your local `kubectl` context first). Edit `CLUSTER_NAME` to a unique short name per cluster before each run.

=== "Existing ServiceAccount (default)"

    Use this when the target cluster already has an SA you want to reuse — typically `robusta-holmes-service-account` if Robusta is already Helm-installed there. This script mints a long-lived token for that SA and appends a context to `./holmes-kubeconfig`.

    ```bash
    #!/usr/bin/env bash
    set -euo pipefail

    CLUSTER_NAME=prod                                 # appears in MCP tool calls
    SA_NAME=robusta-holmes-service-account            # existing SA to mint a token for
    SA_NAMESPACE=default                              # namespace of that SA
    KUBECONFIG_OUT=./holmes-kubeconfig
    TOKEN_SECRET="${SA_NAME}-mcp-token"

    # Sanity-check that the SA exists.
    if ! kubectl get serviceaccount "$SA_NAME" -n "$SA_NAMESPACE" >/dev/null 2>&1; then
      echo "ServiceAccount $SA_NAMESPACE/$SA_NAME not found." >&2
      exit 1
    fi

    # Create a long-lived token Secret bound to the SA (K8s 1.24+).
    cat <<EOF | kubectl apply -f -
    apiVersion: v1
    kind: Secret
    metadata:
      name: ${TOKEN_SECRET}
      namespace: ${SA_NAMESPACE}
      annotations:
        kubernetes.io/service-account.name: ${SA_NAME}
    type: kubernetes.io/service-account-token
    EOF

    sleep 2
    TOKEN=$(kubectl get secret "$TOKEN_SECRET" -n "$SA_NAMESPACE" \
      -o jsonpath='{.data.token}' | base64 -d)
    CA_B64=$(kubectl get secret "$TOKEN_SECRET" -n "$SA_NAMESPACE" \
      -o jsonpath='{.data.ca\.crt}')
    SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

    # Append this cluster to $KUBECONFIG_OUT. Write the CA to a temp file
    # rather than process substitution (<(...) doesn't work under sh/dash).
    CA_FILE=$(mktemp)
    trap 'rm -f "$CA_FILE"' EXIT
    echo "$CA_B64" | base64 -d > "$CA_FILE"

    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-cluster "$CLUSTER_NAME" \
      --server="$SERVER" \
      --certificate-authority="$CA_FILE" \
      --embed-certs=true
    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-credentials "holmes-$CLUSTER_NAME" \
      --token="$TOKEN"
    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-context "$CLUSTER_NAME" \
      --cluster="$CLUSTER_NAME" --user="holmes-$CLUSTER_NAME"

    # Verify the new context can talk to the API server.
    KUBECONFIG="$KUBECONFIG_OUT" kubectl --context="$CLUSTER_NAME" \
      get pods -A --request-timeout=10s | head -5
    ```

=== "Create a new ServiceAccount"

    Use this when the target cluster doesn't have a Holmes SA yet. We render the same ServiceAccount + ClusterRole + ClusterRoleBinding that the Holmes Helm chart installs (so the SA gets exactly the read-only role Holmes normally runs with — nodes, metrics, RBAC inspection, Prometheus CRDs, no Secrets), apply it, then run the same token + kubeconfig script as the other tab.

    **Render and apply the SA, ClusterRole and ClusterRoleBinding from the Helm chart:**

    ```bash
    helm template robusta \
      https://robusta-charts.storage.googleapis.com/holmes-0.31.1.tgz \
      --show-only templates/holmesgpt-service-account.yaml \
      --set createServiceAccount=true \
      --set k8sRBAC=false \
      --namespace default > sa.yaml

    kubectl apply -f sa.yaml
    ```

    This creates `robusta-holmes-service-account` in the `default` namespace plus `robusta-holmes-cluster-role` and `robusta-holmes-cluster-role-binding`. The chart version (`0.31.1`) can be bumped to whatever is current.

    > **On clusters that already have Robusta installed via Helm:** `kubectl apply` will warn about a missing `kubectl.kubernetes.io/last-applied-configuration` annotation and "configure" the existing objects. The resources are functionally identical, but you've now created a co-management situation between Helm and `kubectl apply`. To keep them separate, change the release name in the `helm template` command (e.g. `helm template holmes-mcp …`) so it renders `holmes-mcp-holmes-*` resources alongside Helm's `robusta-holmes-*` ones. Update `SA_NAME` below to match.

    **Then generate the token and kubeconfig** (same as the other tab):

    ```bash
    CLUSTER_NAME=prod
    SA_NAME=robusta-holmes-service-account
    SA_NAMESPACE=default
    KUBECONFIG_OUT=./holmes-kubeconfig
    TOKEN_SECRET="${SA_NAME}-mcp-token"

    cat <<EOF | kubectl apply -f -
    apiVersion: v1
    kind: Secret
    metadata:
      name: ${TOKEN_SECRET}
      namespace: ${SA_NAMESPACE}
      annotations:
        kubernetes.io/service-account.name: ${SA_NAME}
    type: kubernetes.io/service-account-token
    EOF

    sleep 2
    TOKEN=$(kubectl get secret "$TOKEN_SECRET" -n "$SA_NAMESPACE" \
      -o jsonpath='{.data.token}' | base64 -d)
    CA_B64=$(kubectl get secret "$TOKEN_SECRET" -n "$SA_NAMESPACE" \
      -o jsonpath='{.data.ca\.crt}')
    SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

    CA_FILE=$(mktemp)
    trap 'rm -f "$CA_FILE"' EXIT
    echo "$CA_B64" | base64 -d > "$CA_FILE"

    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-cluster "$CLUSTER_NAME" \
      --server="$SERVER" \
      --certificate-authority="$CA_FILE" \
      --embed-certs=true
    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-credentials "holmes-$CLUSTER_NAME" \
      --token="$TOKEN"
    KUBECONFIG="$KUBECONFIG_OUT" kubectl config set-context "$CLUSTER_NAME" \
      --cluster="$CLUSTER_NAME" --user="holmes-$CLUSTER_NAME"

    # Verify the new context can talk to the API server.
    KUBECONFIG="$KUBECONFIG_OUT" kubectl --context="$CLUSTER_NAME" \
      get pods -A --request-timeout=10s | head -5
    ```

**Common pitfalls:**

- **Network reachability** — the Holmes pod must be able to reach every cluster's API server. Public LBs, private LBs over VPN, and `kubectl` proxy URLs all work; localhost endpoints (like `kind`'s `https://127.0.0.1:PORT`) do not.
- **Embedded certs** — always use `--embed-certs=true`. File-path references won't resolve inside the pod.
- **Cloud auth plugins** — replace `exec` blocks (EKS/GKE/AKS) with the SA token approach above. The MCP server image doesn't carry cloud CLIs.
- **Context names** — these are what the LLM sees; keep them short and descriptive.

### Step 2: Create the kubeconfig Secret

```bash
kubectl create secret generic k8s-mcp-kubeconfig \
  --from-file=kubeconfig=./holmes-kubeconfig \
  -n YOUR_NAMESPACE
```

### Step 3: Deploy

Two settings make this mode work and are easy to miss:

- `serviceAccount.createClusterRoleBinding: false` — Holmes is authenticating with the kubeconfig's tokens, not the pod's own ServiceAccount, so no in-cluster RBAC is needed.
- `extraArgs: ["--kubeconfig", "/etc/kubernetes/kubeconfig", "--cluster-provider", "kubeconfig"]` — **required**. When the MCP server detects it's running in a pod it defaults to the in-cluster strategy and ignores any mounted kubeconfig. These flags force the kubeconfig provider so multi-cluster discovery works. Setting just `KUBECONFIG` as an env var is not enough.

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
          createClusterRoleBinding: false  # auth comes from kubeconfig tokens

        config:
          readOnly: true

          kubeconfig:
            secretName: "k8s-mcp-kubeconfig"
            secretKey: "kubeconfig"

          # Required — overrides in-cluster auto-detection
          extraArgs:
            - "--kubeconfig"
            - "/etc/kubernetes/kubeconfig"
            - "--cluster-provider"
            - "kubeconfig"
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
            createClusterRoleBinding: false  # auth comes from kubeconfig tokens

          config:
            readOnly: true

            kubeconfig:
              secretName: "k8s-mcp-kubeconfig"
              secretKey: "kubeconfig"

            extraArgs:
              - "--kubeconfig"
              - "/etc/kubernetes/kubeconfig"
              - "--cluster-provider"
              - "kubeconfig"
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Step 4: Verify

Confirm the MCP server sees every context you added:

```bash
kubectl port-forward -n YOUR_NAMESPACE svc/holmes-k8s-mcp-server 8000:8000
# In another terminal:
curl -s http://localhost:8000/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"configuration_contexts_list","arguments":{}}}'
```

You should get back every context name from your kubeconfig. Once that's good, ask Holmes something like "list pods in the staging cluster" and watch it pass `context: staging` into its tool calls.

> **Including the home cluster:** if you want Holmes to also be able to investigate the cluster it's running in, add that cluster to the kubeconfig too — the in-cluster ServiceAccount is no longer used in this mode.

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
3. Under **Authentication > Platform configurations**, add a **Web** platform with the redirect URI matching your Robusta region:

    ```robusta-region
    https://platform.robusta.dev/oauth/callback.html
    ```

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
