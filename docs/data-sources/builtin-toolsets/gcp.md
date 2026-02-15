# GCP (MCP)

Connect Holmes to Google Cloud Platform for investigating infrastructure issues, audit logs, and retrieving historical data from deleted resources.

## Overview

Choose your setup path based on your environment:

- **[GKE with Workload Identity](#gke-with-workload-identity)** - Recommended for GKE clusters (no key management)
- **[Service Account Key](#service-account-key)** - Works anywhere (CLI, EKS, AKS, on-premise)

??? info "How it works"
    The GCP MCP addon consists of three specialized servers:

    - **gcloud MCP**: General GCP management via gcloud CLI commands, supporting multi-project queries
    - **Observability MCP**: Cloud Logging, Monitoring, Trace, and Error Reporting - can retrieve historical logs for deleted Kubernetes resources
    - **Storage MCP**: Cloud Storage operations and management

## GKE with Workload Identity

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

## Service Account Key

If you're not using GKE, or prefer not to use Workload Identity, you can authenticate with a service account key instead. This works in any environment but requires managing and rotating key files.

=== "Holmes CLI"

    **Step 1: Create GCP Service Account**

    ```bash
    git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
    cd holmes-mcp-integrations/servers/gcp

    ./setup-gcp-service-account.sh \
      --project your-project-id \
      --k8s-namespace holmes-mcp
    ```

    The script creates a service account with ~50 read-only IAM roles, generates a key, and creates a Kubernetes secret (`gcp-sa-key`).

    ??? note "Manual Setup"
        ```bash
        gcloud iam service-accounts create holmes-gcp-mcp \
          --display-name="Holmes GCP MCP Service Account"

        PROJECT_ID=your-project
        SA_EMAIL=holmes-gcp-mcp@${PROJECT_ID}.iam.gserviceaccount.com

        for role in browser compute.viewer container.viewer logging.privateLogViewer monitoring.viewer; do
          gcloud projects add-iam-policy-binding ${PROJECT_ID} \
            --member="serviceAccount:${SA_EMAIL}" \
            --role="roles/${role}"
        done

        gcloud iam service-accounts keys create key.json --iam-account=${SA_EMAIL}
        kubectl create secret generic gcp-sa-key --from-file=key.json --namespace=holmes-mcp
        ```

    **Step 2: Deploy the MCP Servers**

    Create `gcp-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: gcp-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: gcp-mcp-server
      template:
        metadata:
          labels:
            app: gcp-mcp-server
        spec:
          containers:
          - name: gcloud-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-cli-mcp:1.0.7
            ports:
            - containerPort: 8000
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
          - name: observability-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-observability-mcp:1.0.0
            ports:
            - containerPort: 8001
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
          - name: storage-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-storage-mcp:1.0.0
            ports:
            - containerPort: 8002
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
          volumes:
          - name: gcp-key
            secret:
              secretName: gcp-sa-key
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: gcp-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: gcp-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        name: gcloud
      - port: 8001
        targetPort: 8001
        name: observability
      - port: 8002
        targetPort: 8002
        name: storage
    ```

    ```bash
    kubectl apply -f gcp-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      gcp_gcloud:
        description: "Google Cloud management via gcloud CLI"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: "sse"
      gcp_observability:
        description: "GCP Observability - logs, metrics, traces"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8001/sse"
          mode: "sse"
      gcp_storage:
        description: "Google Cloud Storage operations"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8002/sse"
          mode: "sse"
    ```

    **For local testing**, port-forward and use localhost URLs:

    ```bash
    kubectl port-forward -n holmes-mcp svc/gcp-mcp-server 8000:8000 8001:8001 8002:8002
    ```

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

## Troubleshooting

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

Replace `YOUR_NAMESPACE` with `holmes-mcp` (CLI), `holmes` (Holmes Helm), or `robusta` (Robusta Helm).
