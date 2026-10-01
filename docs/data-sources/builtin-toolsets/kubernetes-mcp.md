# Kubernetes (MCP)

--8<-- "snippets/kubernetes_toolset_picker.md"

The [Kubernetes MCP server](https://github.com/containers/kubernetes-mcp-server) gives Holmes access to Kubernetes clusters via the MCP protocol, with support for OAuth/OIDC authentication. It is intended to **replace** the built-in `kubernetes/core` and `kubernetes/logs` toolsets — the Helm examples below disable those to avoid overlap.

## Which setup do I need?

| Mode | Clusters Holmes can see | Authentication | Best for |
|------|------------------------|----------------|----------|
| **[Single Cluster](#single-cluster-serviceaccount)** | Just the cluster Holmes runs in | Pod's own ServiceAccount | Single-cluster setups, simplest path |
| **[Multiple Clusters](#multiple-clusters-mounted-kubeconfig)** | Many clusters from one Holmes pod | Pre-issued tokens in a mounted kubeconfig | Investigating prod + staging + dev from one place |

## Single Cluster (ServiceAccount)

The simplest setup. The MCP server runs in the same cluster it monitors and authenticates with its own ServiceAccount — no tokens to mint, no external IdP to configure.

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


### Step 2: Verify

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=k8s-mcp-server
```

## Multiple Clusters (Mounted Kubeconfig)

Use this mode when you want **one Holmes pod to investigate multiple Kubernetes clusters** from a single place. Holmes still runs inside one "home" cluster, but instead of using its in-pod ServiceAccount it authenticates to every target cluster (including, optionally, its home cluster) using credentials packed into a kubeconfig file you mount as a Secret. Every applicable MCP tool exposes a `context` argument so the LLM can pick which cluster to query for each step of an investigation.

### Step 1: Generate a kubeconfig for Holmes

A kubeconfig is a YAML file containing three lists: `clusters` (API server URL + CA cert), `users` (credentials), and `contexts` (a named pairing of one cluster with one user). The k8s-mcp-server uses the **context name** as the cluster identifier — pick names you'd be comfortable seeing in tool calls (e.g. `prod-eu`, `staging`, `dev`).

For each cluster you want Holmes to access, you need a ServiceAccount with a long-lived token. Cloud auth plugins like `aws-iam-authenticator`, `gke-gcloud-auth-plugin`, and `kubelogin` **do not work inside the MCP server pod** — you must use static credentials.

Run the following against **each target cluster** (switch your local `kubectl` context first). Edit `CLUSTER_NAME` to a unique short name per cluster before each run.

??? info "Don't have a Holmes ServiceAccount on the target cluster yet?"
    Render and apply one from the Helm chart first — this gives the SA the same read-only role Holmes normally runs with (nodes, metrics, RBAC inspection, Prometheus CRDs, no Secrets):

    ```bash
    helm template robusta \
      https://robusta-charts.storage.googleapis.com/holmes-0.31.1.tgz \
      --show-only templates/holmesgpt-service-account.yaml \
      --set createServiceAccount=true \
      --set k8sRBAC=false \
      --namespace default > sa.yaml

    kubectl apply -f sa.yaml
    ```

    This creates `robusta-holmes-service-account` in the `default` namespace plus `robusta-holmes-cluster-role` and `robusta-holmes-cluster-role-binding`. Bump the chart version (`0.31.1`) to whatever is current.

    **If the cluster already has a Holmes release with this name:** `kubectl apply` will warn about a missing `kubectl.kubernetes.io/last-applied-configuration` annotation and "configure" the existing objects. The resources are functionally identical, but you've now created a co-management situation between Helm and `kubectl apply`. To keep them separate, change the release name in the `helm template` command (e.g. `helm template holmes-mcp …`) so it renders `holmes-mcp-holmes-*` resources alongside Helm's `robusta-holmes-*` ones. Update `SA_NAME` below to match.

Now mint a long-lived token for the SA and append a context to `./holmes-kubeconfig`:

```bash
#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME=prod                                 # appears in MCP tool calls
SA_NAME=robusta-holmes-service-account            # SA to mint a token for
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

# Write the CA to a temp file rather than process substitution
# (<(...) doesn't work under sh/dash).
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

### Step 2: Set the default context In the Kubeconfig file

```bash
# List what's available — pick one name from the output
KUBECONFIG=./holmes-kubeconfig kubectl config get-contexts -o name

# Set it (replace <DEFAULT_CLUSTER> with one of the names above)
KUBECONFIG=./holmes-kubeconfig kubectl config use-context <DEFAULT_CLUSTER>

# Confirm
KUBECONFIG=./holmes-kubeconfig kubectl config current-context
```


### Step 3: Create the kubeconfig Secret

```bash
kubectl create secret generic k8s-mcp-kubeconfig \
  --from-file=kubeconfig=holmes-kubeconfig \
  -n YOUR_NAMESPACE
```

To validate the secret:

```bash
kubectl get secret k8s-mcp-kubeconfig -n YOUR_NAMESPACE \
  -o jsonpath='{.data.kubeconfig}' | base64 -d \
  | grep -E '^current-context: \S' \
  && echo "OK: Secret populated with a default context" \
  || echo "FAIL: Secret is empty or missing current-context — fix before deploying"
```

### Step 4: Deploy

Adjust your values.yaml file in the holmes "hub" cluster where you want multi-cluster access:

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

        llmInstructions: |
          This MCP server provides direct access to Kubernetes clusters for advanced cluster operations and troubleshooting. This instance is connected to MULTIPLE Kubernetes clusters via kubeconfig contexts.

          ## MANDATORY FIRST STEP — read this before any other tool call

          Before doing ANYTHING else for a Kubernetes question (resource lookup, log retrieval, event check, status check, "is X running?", "why is X failing?", etc.):

          1. Call `configuration_contexts_list` to enumerate every cluster context. Do this FIRST, on every fresh investigation, even if the user named a cluster or you think you already know which cluster applies. No exceptions.
          2. Treat the returned list as the complete search space. The resource the user is asking about may live on ANY of these clusters.
          3. For every subsequent tool call, pass the explicit `context` argument. Never rely on an implicit default.

          ## Multi-cluster search procedure (when a resource is not on the first cluster)

          If any resource lookup returns "not found" on a given context:

          - **Immediately** re-issue the same lookup against every OTHER context returned by `configuration_contexts_list`.
          - Do this WITHOUT pausing, WITHOUT asking the user "should I check other clusters?", and WITHOUT explaining what you're about to do. Just do it.
          - Only after all contexts have been queried may you conclude that a resource truly does not exist.
          - If a tool call against one context fails (auth, network, timeout), say so explicitly and CONTINUE with the remaining contexts. One failure must not short-circuit the search.

          ## Forbidden behaviors (your answer is incorrect if you do any of these)

          - Skipping `configuration_contexts_list` and jumping straight to a resource query.
          - Reporting "resource not found" without having queried EVERY context from `configuration_contexts_list`.
          - Asking the user "should I check the other clusters?" — the answer is always yes; do it without asking.
          - Assuming the first/default cluster is the only one to check.
          - Omitting the cluster name from your final answer when reporting findings.

          ## Required output discipline

          Every finding must be labeled with the cluster/context name it came from.

          - Correct: "Found `payment-service` deployment on cluster **prod-eu** (3/3 ready). It does NOT exist on **prod-us** or **prod-ap**."
          - Incorrect: "Found payment-service deployment, 3/3 ready." (missing cluster attribution)

          ## When to Use This MCP Server

          Use the Kubernetes MCP when investigating:
          - Pod failures, crash loops, or scheduling issues
          - Resource consumption and node capacity problems
          - Deployment rollout issues or scaling problems
          - Kubernetes events and cluster-level diagnostics
          - Helm release status and management

          ## Investigation Workflow

          1. **List clusters FIRST** — call `configuration_contexts_list` (mandatory; see top of this document). All subsequent tool calls must include an explicit `context`.
          2. **List namespaces** on the candidate cluster(s) to identify where the resource of interest could live.
          3. **Check events**: look for warnings and errors. If the resource was not on the first cluster, fan out and check events on every other cluster too.
          4. **Inspect pods**: get status, logs, resource usage — from the cluster where the resource actually exists.
          5. **Examine resources**: get detailed definitions to identify misconfigurations.
          6. **Check node health**: review node status and resource consumption on the relevant cluster.

          ## Important Guidelines

          - Always specify BOTH the namespace AND the cluster `context` when querying namespaced resources.
          - Check events first — they often reveal the root cause quickly.
          - Use pod logs to understand application-level failures.
          - Compare resource requests/limits with actual usage via top commands.
          - When investigating scheduling issues, check node capacity and taints on the cluster where the pod lives.

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

          serverConfig: |
            disabled_tools = ["configuration_view"]
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```


The `llmInstructions` block above helps holmes with multi-cluster awareness.

## Common Use Cases

```bash
holmes ask "List all pods in CrashLoopBackOff across all namespaces"
```

```bash
holmes ask "What events are happening in the production namespace?"
```

```bash
holmes ask "Show me the resource requests and limits for all deployments in namespace backend"
```

```bash
holmes ask "Why is the checkout-api pod not scheduling?"
```
