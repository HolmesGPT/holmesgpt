# gcloud MCP Server Integration for Holmes

This directory contains resources for deploying the gcloud MCP (Model Context Protocol) server for Holmes, enabling comprehensive GCP service queries for investigating Google Cloud infrastructure and Kubernetes issues on GKE.

## Overview

The gcloud MCP server provides Holmes with direct access to GCP APIs through a secure, read-only interface. The server is packaged as a Docker container using Supergateway to expose the stdio-based gcloud MCP as an SSE (Server-Sent Events) API, making it accessible as a remote MCP server within Kubernetes.

## Architecture

```
Holmes → Remote MCP (SSE API) → Supergateway Wrapper → gcloud MCP → GCP APIs
                                       ↓
                         Running in Kubernetes with Workload Identity
```

## Quick Start

```bash
# 1. Set up Workload Identity (one command creates everything)
./setup-workload-identity.sh --project PROJECT --cluster CLUSTER --region REGION

# 2. Build and push Docker image
docker build -t gcr.io/PROJECT/gcloud-mcp-server:latest .
docker push gcr.io/PROJECT/gcloud-mcp-server:latest

# 3. Update deployment with your image and deploy
kubectl apply -f gcloud-mcp-deployment.yaml
kubectl apply -f gcloud-mcp-service.yaml

# 4. Configure Holmes (see Configuration section below)
```

## Files in This Directory

### Core Files

- **`Dockerfile`** - Wraps the stdio-based gcloud MCP with Supergateway to expose it as an SSE API service
  - Base image: `supercorp/supergateway:latest` (provides SSE API wrapper)
  - Installs Google Cloud SDK and Node.js
  - Exposes port 8000 for remote MCP connections
  - Converts stdio interface to HTTP SSE API for remote access

- **`gcp-readonly-roles.txt`** - Comprehensive list of 70+ read-only IAM roles
  - Covers all major GCP services
  - All permissions are read-only (viewer/reader roles)
  - Can be shared across multiple GKE clusters
  - No destructive operations allowed

- **`setup-workload-identity.sh`** - Automated script to set up Workload Identity
  - Creates all necessary GCP and Kubernetes resources
  - Handles the complete Workload Identity setup process
  - Safe: won't overwrite existing resources
  - Usage: `./setup-workload-identity.sh --project PROJECT --cluster CLUSTER --region REGION`

- **`gcloud-mcp-deployment.yaml`** - Kubernetes deployment manifest
  - Configured for Workload Identity
  - Resource limits and health checks
  - Network policy for security

- **`gcloud-mcp-service.yaml`** - Kubernetes service manifest
  - ClusterIP service on port 8000

## Understanding Workload Identity

Workload Identity is the recommended way to access GCP services from GKE. It involves:

1. **GKE Workload Identity Pool**: Establishes trust between your GKE cluster and GCP IAM
2. **GCP Service Account**: The GCP identity with permissions (`holmes-gcloud-mcp@PROJECT.iam`)
3. **Kubernetes Service Account**: Links pods to the GCP service account (`gcloud-mcp-sa`)
4. **IAM Binding**: Allows K8s SA to impersonate GCP SA

### How It Works

1. Pod starts with the Kubernetes service account
2. GCP SDK in the pod reads the service account's GCP annotation
3. Pod exchanges its Kubernetes token for GCP credentials
4. Pod can now make GCP API calls with the granted permissions

## Setup Instructions

### Prerequisites

- GKE cluster with Workload Identity enabled
- `gcloud` CLI configured with appropriate permissions
- `kubectl` configured to access your cluster
- Docker for building the image

### Step 1: Enable Workload Identity on Your Cluster

If not already enabled:

```bash
gcloud container clusters update CLUSTER_NAME \
  --workload-pool=PROJECT.svc.id.goog \
  --region=REGION
```

### Step 2: Run the Automated Setup Script

```bash
# Basic usage
./setup-workload-identity.sh \
  --project my-project \
  --cluster my-cluster \
  --region us-central1

# With custom namespace
./setup-workload-identity.sh \
  --project my-project \
  --cluster my-cluster \
  --region us-central1 \
  --namespace holmes

# See all options
./setup-workload-identity.sh --help
```

The script will:
1. Verify Workload Identity is enabled on your cluster
2. Create a GCP service account (`holmes-gcloud-mcp`)
3. Grant all read-only roles from `gcp-readonly-roles.txt`
4. Create a Kubernetes service account
5. Bind the two accounts together
6. Test the configuration

### Step 3: Build and Push the Docker Image

```bash
# Using Google Container Registry (GCR)
docker build -t gcr.io/PROJECT/gcloud-mcp-server:latest .
docker push gcr.io/PROJECT/gcloud-mcp-server:latest

# Or using Artifact Registry
docker build -t REGION-docker.pkg.dev/PROJECT/REPO/gcloud-mcp-server:latest .
docker push REGION-docker.pkg.dev/PROJECT/REPO/gcloud-mcp-server:latest
```

### Step 4: Deploy to Kubernetes

Update the deployment manifest with your image:

```yaml
# In gcloud-mcp-deployment.yaml
image: gcr.io/YOUR_PROJECT/gcloud-mcp-server:latest
```

Update the ConfigMap with your project:

```yaml
# In gcloud-mcp-deployment.yaml ConfigMap section
data:
  GCP_PROJECT: "your-project-id"
  GCLOUD_REGION: "your-preferred-region"
```

Deploy:

```bash
kubectl apply -f gcloud-mcp-deployment.yaml
kubectl apply -f gcloud-mcp-service.yaml
```

### Step 5: Configure Holmes

Add to your Holmes configuration (`~/.holmes/config.yaml` for CLI or values.yaml for Helm):

```yaml
mcp_servers:
  gcloud:
    description: "GCP MCP Server - comprehensive Google Cloud service access"
    url: "http://gcloud-mcp-server.default.svc.cluster.local:8000"
    mode: "sse"  # or "streamable-http"
    llm_instructions: |
      Use this server to investigate GCP resources and GKE issues.

      Available operations include all gcloud CLI commands:
      - Compute: List/describe instances, disks, networks
      - GKE: Cluster info, node pools, workloads
      - Storage: Buckets, objects, permissions
      - IAM: Roles, policies, service accounts
      - Monitoring: Metrics, logs, traces
      - BigQuery: Datasets, tables, queries
      - Cloud SQL: Instances, databases, users

      Example commands:
      - gcloud compute instances list
      - gcloud container clusters describe CLUSTER --region REGION
      - gcloud logging read "resource.type=k8s_pod"
      - gcloud monitoring metrics-descriptors list
      - gcloud iam service-accounts list
```

## Available GCP Read-Only Roles

The setup script grants comprehensive read-only access across all GCP services:

### Core Infrastructure
- Project viewer, Compute viewer, GKE viewer
- IAM security reviewer, Organization viewer

### Storage & Databases
- Cloud Storage, BigQuery, Cloud SQL, Spanner
- Firestore, Bigtable, Memorystore

### Monitoring & Logging
- Cloud Monitoring, Logging, Trace
- Error Reporting, Profiler, Debugger

### Security
- Security Command Center, Secret Manager
- Cloud KMS, Binary Authorization

### And many more...

See `gcp-readonly-roles.txt` for the complete list of 70+ roles.

## Testing the Setup

### Verify Deployment

```bash
# Check if pod is running
kubectl get pods -l app=gcloud-mcp-server

# Check logs
kubectl logs -l app=gcloud-mcp-server

# Check service
kubectl get svc gcloud-mcp-server
```

### Test Workload Identity

```bash
# Run a test pod
kubectl run gcp-test \
  --image=google/cloud-sdk:slim \
  --rm -it --restart=Never \
  --overrides='{"spec":{"serviceAccountName":"gcloud-mcp-sa"}}' \
  -- gcloud auth list

# Should show: holmes-gcloud-mcp@PROJECT.iam.gserviceaccount.com
```

### Test from Holmes

```bash
holmes ask "What GCE instances are running in my GCP project?"
holmes ask "Show me recent errors in GKE cluster logs"
holmes ask "What Cloud SQL databases exist in the project?"
```

## Troubleshooting

### MCP Server Not Responding

1. Check pod status:
   ```bash
   kubectl describe pod -l app=gcloud-mcp-server
   ```

2. Check logs:
   ```bash
   kubectl logs -l app=gcloud-mcp-server -f
   ```

### Authentication Issues

1. Verify Workload Identity binding:
   ```bash
   kubectl get sa gcloud-mcp-sa -o yaml
   # Should have annotation: iam.gke.io/gcp-service-account
   ```

2. Check IAM bindings:
   ```bash
   gcloud iam service-accounts get-iam-policy \
     holmes-gcloud-mcp@PROJECT.iam.gserviceaccount.com
   ```

3. Test authentication in pod:
   ```bash
   kubectl exec -it deploy/gcloud-mcp-server -- gcloud auth list
   ```

### Permission Denied Errors

1. Check if specific role is missing:
   ```bash
   gcloud projects get-iam-policy PROJECT \
     --flatten="bindings[].members" \
     --filter="bindings.members:holmes-gcloud-mcp@PROJECT.iam.gserviceaccount.com"
   ```

2. Add missing role:
   ```bash
   gcloud projects add-iam-policy-binding PROJECT \
     --member="serviceAccount:holmes-gcloud-mcp@PROJECT.iam.gserviceaccount.com" \
     --role="roles/MISSING_ROLE"
   ```

## Security Considerations

- The MCP server has **read-only** access to GCP services
- Workload Identity ensures pods use temporary credentials
- No GCP credentials are stored in the cluster
- Access is scoped to specific service account
- Network policy restricts pod communication

## Alternative: Service Account Key (Not Recommended)

For non-GKE clusters or testing, you can use a service account key:

1. Create a key:
   ```bash
   gcloud iam service-accounts keys create key.json \
     --iam-account=holmes-gcloud-mcp@PROJECT.iam.gserviceaccount.com
   ```

2. Create a secret:
   ```bash
   kubectl create secret generic gcp-sa-key --from-file=key.json
   ```

3. Mount in deployment (modify `gcloud-mcp-deployment.yaml`):
   ```yaml
   volumeMounts:
   - name: gcp-key
     mountPath: /var/secrets/gcp
     readOnly: true
   env:
   - name: GOOGLE_APPLICATION_CREDENTIALS
     value: /var/secrets/gcp/key.json
   volumes:
   - name: gcp-key
     secret:
       secretName: gcp-sa-key
   ```

⚠️ **Warning**: Service account keys are long-lived credentials and less secure than Workload Identity.

## Multi-Cluster Setup

To use the same setup across multiple clusters:

```bash
# Cluster 1
./setup-workload-identity.sh --project PROJECT --cluster prod --region us-central1

# Cluster 2 (will reuse the same GCP service account)
./setup-workload-identity.sh --project PROJECT --cluster staging --region us-east1

# Results in:
# - One GCP service account: holmes-gcloud-mcp@PROJECT.iam
# - K8s service account in each cluster
# - Same IAM roles applied
```

## Next Steps

1. **Enhance Holmes investigations** with GCP-specific patterns:
   - GKE node issues
   - Cloud SQL performance problems
   - BigQuery query optimization
   - Cloud Storage access issues

2. **Create evaluation tests** for GCP scenarios:
   - GCE network connectivity issues
   - GKE pod failures
   - Cloud SQL connection problems
   - IAM permission debugging

3. **Monitor usage** and adjust resource limits as needed

## Support

For issues or questions:
- Check Holmes documentation at https://holmesgpt.dev/
- Report issues at https://github.com/robusta/holmesgpt/issues
- Review gcloud MCP documentation at https://github.com/googleapis/gcloud-mcp
