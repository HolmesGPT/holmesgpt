# GCP (MCP)

The GCP MCP servers provide comprehensive access to Google Cloud Platform services through secure, read-only interfaces. They enable Holmes to investigate GCP infrastructure issues, analyze audit logs, examine security configurations, troubleshoot service-specific problems, and retrieve historical data from deleted resources.

## Overview

!!! info "Prerequisites"
    You need to configure GCP service account credentials before installing the MCP servers. See the [Service Account Configuration](#service-account-configuration) section for setup instructions.

The GCP MCP addon consists of three specialized servers:

- **gcloud MCP**: General GCP management via gcloud CLI commands, supporting multi-project queries
- **Observability MCP**: Cloud Logging, Monitoring, Trace, and Error Reporting - can retrieve historical logs for deleted Kubernetes resources
- **Storage MCP**: Cloud Storage operations and management

When using the Holmes or Robusta Helm charts, these servers are deployed as separate pods in your cluster. For CLI users, you'll need to deploy the MCP servers manually and configure Holmes to connect to them.

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the GCP MCP servers first, then configure Holmes to connect to them.

    ### Step 1: Deploy the GCP MCP Servers

    Create a file named `gcp-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: Secret
    metadata:
      name: gcp-sa-key
      namespace: holmes-mcp
    data:
      # Add your base64-encoded service account key here
      key.json: YOUR_BASE64_ENCODED_KEY
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
            imagePullPolicy: Always
            ports:
            - containerPort: 8000
              name: gcloud
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            - name: GCP_PROJECT_ID
              value: "your-project-id"  # Optional: set default project
            - name: GCP_REGION
              value: "us-central1"      # Optional: set default region
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
                cpu: "250m"

          - name: observability-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-observability-mcp:1.0.0
            imagePullPolicy: Always
            ports:
            - containerPort: 8001
              name: observability
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
                cpu: "250m"

          - name: storage-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-storage-mcp:1.0.0
            imagePullPolicy: Always
            ports:
            - containerPort: 8002
              name: storage
            env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: "/var/secrets/gcp/key.json"
            volumeMounts:
            - name: gcp-key
              mountPath: /var/secrets/gcp
              readOnly: true
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
                cpu: "250m"

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
        protocol: TCP
        name: gcloud
      - port: 8001
        targetPort: 8001
        protocol: TCP
        name: observability
      - port: 8002
        targetPort: 8002
        protocol: TCP
        name: storage
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f gcp-mcp-deployment.yaml
    ```

    ### Step 2: Configure GCP Service Account

    You need to create a GCP service account with appropriate permissions. Use the automated setup script from the [holmes-mcp-integrations repository](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/gcp):

    ```bash
    # Clone the repository
    git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
    cd holmes-mcp-integrations/servers/gcp

    # Run the setup script
    ./setup-gcp-service-account.sh \
      --project your-project-id \
      --k8s-namespace holmes-mcp
    ```

    This script will:
    - Create a GCP service account
    - Grant ~50 optimized read-only roles for incident response
    - Generate a service account key
    - Create the Kubernetes secret (`gcp-sa-key`)

    ### Step 3: Configure Holmes CLI

    Add the MCP server configurations to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      gcp_gcloud:
        description: "Google Cloud management via gcloud CLI"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: "sse"
        llm_instructions: |
          Use this server to investigate GCP infrastructure and GKE issues across multiple projects.

          Pass gcloud arguments as an array. Do NOT include 'gcloud' itself.
          - Correct: args: ["compute", "instances", "list", "--project", "my-project"]

          ALWAYS include --project flag for resource queries to support multi-project investigations.

          Common investigations:
          - GKE issues: Node pools, cluster configs, workload identity
          - Networking: VPC peering, firewall rules, Cloud NAT, load balancers, SSL certificates
          - IAM/Auth: Permission errors, service account bindings, OAuth scopes
          - Resources: Quotas, disk space, compute capacity
          - Configuration changes: Audit logs (use with observability MCP)

      gcp_observability:
        description: "GCP Observability - logs, metrics, traces, errors"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8001/sse"
          mode: "sse"
        llm_instructions: |
          Use this server for GCP-level logs, metrics, traces, and monitoring.

          KEY ADVANTAGE: Can retrieve historical logs for deleted Kubernetes resources (pods, jobs, etc.)
          that are no longer available via kubectl.

          CRITICAL: Always answer log questions with actual log entries first.
          After providing results, include markdown links to Logs Explorer:
          [Descriptive Name](https://console.cloud.google.com/logs/query;query=YOUR_FILTER?project=PROJECT)

          Common patterns:
          - Deleted pod logs: filter='resource.labels.pod_name="POD_NAME"'
          - OOM kills: filter='jsonPayload.reason="OOMKilling"'
          - Audit trail: filter='logName=~"cloudaudit"'
          - Permission issues: filter='protoPayload.status.code=7'

      gcp_storage:
        description: "Google Cloud Storage operations"
        config:
          url: "http://gcp-mcp-server.holmes-mcp.svc.cluster.local:8002/sse"
          mode: "sse"
        llm_instructions: |
          Use this server for Cloud Storage bucket and object operations.

          Common investigations:
          - Access denied: Check bucket IAM and object ACLs
          - Missing data: Use list_objects(versions=true) and check lifecycle rules
          - Cost analysis: Review storage classes and find large/old objects
    ```

    ### Step 4: Port Forwarding (Optional for Local Testing)

    If running Holmes CLI locally and need to access the MCP server:

    ```bash
    kubectl port-forward -n holmes-mcp svc/gcp-mcp-server 8000:8000 8001:8001 8002:8002
    ```

    Then update the URLs in config.yaml to use localhost:
    ```yaml
    url: "http://localhost:8000/sse"  # For gcloud
    url: "http://localhost:8001/sse"  # For observability
    url: "http://localhost:8002/sse"  # For storage
    ```

=== "Holmes Helm Chart"

    The MCP servers use a GCP service account key for authentication. You need to create the service account and secret first - see the Service Account Configuration section below for setup details.

    Add the following configuration to your `values.yaml` file:

    ```yaml
    mcpAddons:
      gcp:
        enabled: true

        # Reference the secret created by setup script
        serviceAccountKey:
          secretName: "gcp-sa-key"

        # Optional: specify primary project/region
        config:
          project: "your-primary-project"  # Optional
          region: "us-central1"            # Optional

        # Enable the MCP servers you need
        gcloud:
          enabled: true
        observability:
          enabled: true
        storage:
          enabled: true
    ```

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml).

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    The MCP servers use a GCP service account key for authentication. You need to create the service account and secret first - see the Service Account Configuration section below for setup details.

    Add the following configuration to your `generated_values.yaml`:

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    # Add the Holmes MCP addon configuration
    holmes:
      mcpAddons:
        gcp:
          enabled: true

          # Reference the secret created by setup script
          serviceAccountKey:
            secretName: "gcp-sa-key"

          # Optional: specify primary project/region
          config:
            project: "your-primary-project"  # Optional
            region: "us-central1"            # Optional

          # Enable the MCP servers you need
          gcloud:
            enabled: true
          observability:
            enabled: true
          storage:
            enabled: true
    ```

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml).

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Service Account Configuration

### Creating a GCP Service Account

The GCP MCP servers require a service account with appropriate read-only permissions. We provide an automated script that handles all the setup:

1. **Clone the repository and run the setup script:**

   ```bash
   git clone https://github.com/robusta-dev/holmes-mcp-integrations.git
   cd holmes-mcp-integrations/servers/gcp

   # Single project setup
   ./setup-gcp-service-account.sh \
     --project your-project-id \
     --k8s-namespace holmes  # Or your namespace

   # Multi-project setup (for cross-project investigations)
   ./setup-gcp-service-account.sh \
     --project primary-project \
     --other-projects dev-project,staging-project,prod-project \
     --k8s-namespace holmes
   ```

2. **What the script does:**
   - Creates a GCP service account
   - Grants ~50 optimized read-only IAM roles for incident response
   - Generates a service account key
   - Creates a Kubernetes secret (`gcp-sa-key`) with the key

### IAM Permissions

The setup script grants ~50 optimized read-only roles designed for incident response and troubleshooting:

**What's Included:**
- ✅ Complete audit log visibility (who changed what)
- ✅ Full networking troubleshooting (firewalls, load balancers, SSL)
- ✅ Database and BigQuery metadata (schemas, configurations)
- ✅ Security findings and IAM analysis
- ✅ Container and Kubernetes visibility
- ✅ Monitoring, logging, and tracing

**Security Boundaries:**
- ❌ NO actual data access (cannot read storage objects or BigQuery data)
- ❌ NO secret values (only metadata)
- ❌ NO write permissions

Key roles include:
- `roles/browser` - Navigate project hierarchy
- `roles/logging.privateLogViewer` - Audit and data access logs
- `roles/compute.viewer` - VMs, firewalls, load balancers
- `roles/container.viewer` - GKE clusters and workloads
- `roles/monitoring.viewer` - Metrics and alerts
- `roles/iam.securityReviewer` - IAM policies
- `roles/storage.legacyBucketReader` - Bucket metadata (no object access)
- `roles/bigquery.metadataViewer` - Table schemas only

For the complete list and setup details, see the [GCP MCP setup documentation](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/gcp).

### Manual Setup (Alternative)

If you prefer to set up manually:

```bash
# Create service account
gcloud iam service-accounts create holmes-gcp-mcp \
  --display-name="Holmes GCP MCP Service Account"

# Grant essential roles (example)
PROJECT_ID=your-project
SA_EMAIL=holmes-gcp-mcp@${PROJECT_ID}.iam.gserviceaccount.com

for role in browser compute.viewer container.viewer logging.privateLogViewer monitoring.viewer; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/${role}"
done

# Create key
gcloud iam service-accounts keys create key.json \
  --iam-account=${SA_EMAIL}

# Create Kubernetes secret
kubectl create secret generic gcp-sa-key \
  --from-file=key.json \
  --namespace=holmes
```

## Capabilities

### gcloud MCP
- **Multi-project support**: Query resources across multiple GCP projects
- **GKE management**: Cluster configs, node pools, workload identity
- **Networking**: VPCs, firewalls, load balancers, Cloud NAT, SSL certificates
- **IAM & Security**: Service accounts, IAM policies, OAuth scopes
- **Compute resources**: VMs, disks, snapshots, images
- **Infrastructure**: Cloud SQL, Pub/Sub, Cloud Run, Cloud Functions

### Observability MCP
- **Historical data**: Retrieve logs from deleted Kubernetes resources
- **Cloud Logging**: Query logs with complex filters
- **Cloud Monitoring**: Metrics and time series data
- **Cloud Trace**: Distributed tracing across services
- **Error Reporting**: Application error statistics
- **Audit logs**: Track who made what changes and when
- **Alert policies**: Review monitoring configurations

### Storage MCP
- **Bucket operations**: List, describe, check IAM policies
- **Object management**: List objects (including versions), check ACLs
- **Lifecycle policies**: Review automatic deletion/archival rules
- **Cost analysis**: Identify large objects and storage classes
- **Access troubleshooting**: Debug permission issues
- **Compliance**: Check encryption and retention settings

## Example Usage

### Investigating Deleted Pod Logs
```
"Show me logs from the payment-service pod that was OOMKilled this morning"
```
Holmes will use the Observability MCP to retrieve historical logs even though the pod no longer exists.

### Cross-Project Resource Discovery
```
"List all GKE clusters across our dev, staging, and prod projects"
```
Holmes will use the gcloud MCP to query multiple projects.

### Audit Trail Investigation
```
"Who modified the firewall rules in the last 24 hours?"
```
Holmes will query Cloud Audit logs to find configuration changes.

### Storage Access Issues
```
"Why is my application getting 403 errors accessing the data-bucket?"
```
Holmes will check bucket IAM policies and object ACLs to identify permission issues.

### SSL Certificate Problems
```
"Check the SSL certificates on our load balancers"
```
Holmes will examine certificate configurations and expiration dates.

## Troubleshooting

### Authentication Errors
```bash
# Check if secret is mounted
kubectl exec -n holmes deployment/holmes-gcp-mcp-server -c gcloud-mcp -- \
  ls -la /var/secrets/gcp/

# Verify authentication
kubectl exec -n holmes deployment/holmes-gcp-mcp-server -c gcloud-mcp -- \
  gcloud auth list
```

### Permission Denied
If you get permission errors, verify the service account has the necessary roles:
```bash
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:holmes-gcp-mcp@"
```

### Pod Not Starting
```bash
# Check pod events
kubectl describe pod -n holmes -l app.kubernetes.io/component=gcp-mcp-server

# Check logs
kubectl logs -n holmes deployment/holmes-gcp-mcp-server --all-containers
```

### gcloud MCP Version Issues
The gcloud MCP requires version 550.0.0+ to work correctly. The provided Docker images include the correct version.

## Security Best Practices

1. **Use least privilege**: The setup script only grants read-only roles without data access
2. **Rotate keys regularly**: Re-run the setup script every 90 days
3. **Delete local keys**: Remove key files after creating Kubernetes secret
4. **Monitor usage**: Check audit logs for service account activity
5. **Enable network policies**: Set `networkPolicy.enabled: true` in Helm values

## Additional Resources

- **Setup Scripts and Documentation**: [holmes-mcp-integrations/servers/gcp](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/gcp)
- **Service Account Setup Script**: [setup-gcp-service-account.sh](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/gcp/setup-gcp-service-account.sh)
- **Helm Values Reference**: [values.yaml](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml)
