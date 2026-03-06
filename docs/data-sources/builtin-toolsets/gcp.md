# GCP (MCP)

Connect Holmes to Google Cloud Platform for investigating infrastructure issues, audit logs, and retrieving historical data from deleted resources.

??? info "How it works"
    The GCP MCP addon consists of three specialized servers:

    - **gcloud MCP**: General GCP management via gcloud CLI commands, supporting multi-project queries
    - **Observability MCP**: Cloud Logging, Monitoring, Trace, and Error Reporting - can retrieve historical logs for deleted Kubernetes resources
    - **Storage MCP**: Cloud Storage operations and management

## Holmes CLI

The official Google Cloud MCP servers run locally on your machine via `npx`. Authentication uses your existing `gcloud` credentials.

**Prerequisites:** Node.js must be installed.

**Step 1: Authenticate**

```bash
gcloud auth login
gcloud auth application-default login
```

**Step 2: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/gcloud-mcp"]
  gcp_observability:
    description: "GCP Observability - Cloud Logging, Monitoring, Trace, Error Reporting"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/observability-mcp"]
  gcp_storage:
    description: "Google Cloud Storage operations"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/storage-mcp"]
```

You can use all three servers together or pick only the ones you need.

**Step 3: Test it**

```bash
holmes ask "List all GKE clusters in my project"
```

## Helm Chart Deployment

For in-cluster deployments, choose an authentication method based on your environment:

- **[GKE with Workload Identity](#gke-with-workload-identity)** — Recommended for GKE clusters (no key management)
- **[Service Account Key](#service-account-key)** — Works anywhere (EKS, AKS, on-premise)

### GKE with Workload Identity

Workload Identity is Google's recommended way to authenticate workloads on GKE. It eliminates service account keys by allowing Kubernetes service accounts to impersonate GCP service accounts.

**Define your variables:**

```bash
PROJECT_ID=your-project-id
CLUSTER_NAME=your-cluster-name
REGION=your-region
```

**Step 1: Enable Workload Identity on Your Cluster**

```bash
gcloud container clusters update ${CLUSTER_NAME} \
  --project ${PROJECT_ID} \
  --workload-pool=${PROJECT_ID}.svc.id.goog \
  --region ${REGION}
```

**Step 2: Enable Workload Identity on Node Pools**

Repeat for each node pool where Holmes pods may run, replacing `<node-pool-name>` with your node pool name:

```bash
gcloud container node-pools update <node-pool-name> \
  --project ${PROJECT_ID} \
  --cluster ${CLUSTER_NAME} \
  --workload-metadata=GKE_METADATA \
  --region ${REGION}
```

**Step 3: Create and Configure GCP Service Account**

```bash
# Create service account
gcloud iam service-accounts create holmes-gcp-mcp \
  --display-name="Holmes GCP MCP Service Account"

# Grant roles (see IAM Permissions Details below for full list)
SA_EMAIL=holmes-gcp-mcp@${PROJECT_ID}.iam.gserviceaccount.com

for role in browser compute.viewer container.viewer logging.privateLogViewer monitoring.viewer; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/${role}"
done
```

??? info "IAM Permissions Details"
    For most users, we recommend granting ~50 read-only roles using the [setup script](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/gcp) with `--skip-key-generation`:

    ```bash
    git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
    cd holmes-mcp-integrations/servers/gcp
    ./setup-gcp-service-account.sh --project ${PROJECT_ID} --skip-key-generation
    ```

    **What's Included:** Audit logs, networking, database metadata, security findings, container visibility, monitoring/logging/tracing.

    **Security Boundaries:** Read-only metadata access. Cannot read storage objects, secret values, or modify resources.

**Step 4: Bind Kubernetes Service Account to GCP Service Account**

Replace `<namespace>` with the Kubernetes namespace where Holmes will be deployed:

```bash
gcloud iam service-accounts add-iam-policy-binding holmes-gcp-mcp@${PROJECT_ID}.iam.gserviceaccount.com \
  --project ${PROJECT_ID} \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT_ID}.svc.id.goog[<namespace>/gcp-mcp-sa]"
```

**Step 5: Deploy with Helm**

=== "Holmes Helm Chart"

    Add to your `values.yaml`:

    ```yaml
    mcpAddons:
      gcp:
        enabled: true
        serviceAccount:
          name: gcp-mcp-sa
          annotations:
            iam.gke.io/gcp-service-account: "holmes-gcp-mcp@PROJECT_ID.iam.gserviceaccount.com"
        # Optional: defaults when user doesn't specify. Holmes can query any project the SA has access to.
        config:
          project: "your-primary-project"
          region: "us-central1"
        gcloud:
          enabled: true
        observability:
          enabled: true
        storage:
          enabled: true
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        gcp:
          enabled: true
          serviceAccount:
            name: gcp-mcp-sa
            annotations:
              iam.gke.io/gcp-service-account: "holmes-gcp-mcp@PROJECT_ID.iam.gserviceaccount.com"
          # Optional: defaults when user doesn't specify. Holmes can query any project the SA has access to.
          config:
            project: "your-primary-project"
            region: "us-central1"
          gcloud:
            enabled: true
          observability:
            enabled: true
          storage:
            enabled: true
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Service Account Key

If you're not using GKE, or prefer not to use Workload Identity, you can authenticate with a service account key instead.

=== "Holmes Helm Chart"

    **Step 1: Create GCP Service Account**

    ```bash
    git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
    cd holmes-mcp-integrations/servers/gcp

    ./setup-gcp-service-account.sh \
      --project your-project-id \
      --k8s-namespace holmes
    ```

    The script creates a service account with ~50 read-only IAM roles, generates a key, and creates a Kubernetes secret (`gcp-sa-key`).

    **Step 2: Configure and Deploy**

    Add to your `values.yaml`:

    ```yaml
    mcpAddons:
      gcp:
        enabled: true
        serviceAccountKey:
          secretName: "gcp-sa-key"
        # Optional: defaults when user doesn't specify. Holmes can query any project the SA has access to.
        config:
          project: "your-primary-project"
          region: "us-central1"
        gcloud:
          enabled: true
        observability:
          enabled: true
        storage:
          enabled: true
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Step 1: Create GCP Service Account**

    ```bash
    git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
    cd holmes-mcp-integrations/servers/gcp

    ./setup-gcp-service-account.sh \
      --project your-project-id \
      --k8s-namespace robusta
    ```

    The script creates a service account with ~50 read-only IAM roles, generates a key, and creates a Kubernetes secret (`gcp-sa-key`).

    **Step 2: Configure and Deploy**

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        gcp:
          enabled: true
          serviceAccountKey:
            secretName: "gcp-sa-key"
          # Optional: defaults when user doesn't specify. Holmes can query any project the SA has access to.
          config:
            project: "your-primary-project"
            region: "us-central1"
          gcloud:
            enabled: true
          observability:
            enabled: true
          storage:
            enabled: true
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Troubleshooting

```bash
# Check if secret is mounted
kubectl exec -n YOUR_NAMESPACE deployment/gcp-mcp-server -c gcloud-mcp -- ls -la /var/secrets/gcp/

# Verify authentication
kubectl exec -n YOUR_NAMESPACE deployment/gcp-mcp-server -c gcloud-mcp -- gcloud auth list

# Check service account roles
gcloud projects get-iam-policy PROJECT_ID --flatten="bindings[].members" --filter="bindings.members:holmes-gcp-mcp@"

# Check pod logs
kubectl logs -n YOUR_NAMESPACE deployment/gcp-mcp-server --all-containers
```

## Common Use Cases

```
"Show me logs from the payment-service pod that was OOMKilled this morning"
```

```
"List all GKE clusters across our dev, staging, and prod projects"
```

```
"Who modified the firewall rules in the last 24 hours?"
```

```
"Why is my application getting 403 errors accessing the data-bucket?"
```
