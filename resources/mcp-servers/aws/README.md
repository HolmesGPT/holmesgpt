# AWS MCP Server Integration for Holmes

This directory contains resources for deploying the AWS API MCP (Model Context Protocol) server for Holmes, enabling comprehensive AWS service queries including CloudWatch Container Insights for investigating Kubernetes issues.

## Overview

The AWS MCP server provides Holmes with direct access to AWS APIs through a secure, read-only interface. The server is packaged as a Docker container using Supergateway to expose the stdio-based AWS MCP as an SSE (Server-Sent Events) API, making it accessible as a remote MCP server within Kubernetes.

## Architecture

```
Holmes → Remote MCP (SSE API) → Supergateway Wrapper → AWS MCP Server → AWS APIs
                                        ↓
                          Running in Kubernetes with IRSA
                          (IAM Roles for Service Accounts)
```

## Quick Start

```bash
# 1. Set up IRSA (one command creates everything)
./setup-irsa.sh --cluster-name my-cluster --region us-east-1

# 2. Deploy with Helm (enable in values.yaml)
helm upgrade --install holmes ./helm/holmes --set mcpAddons.aws.enabled=true

# 3. Verify it's working
kubectl get pods -l app=holmes-aws-mcp
```

## Resource Files in This Directory

### Core Files

- **`Dockerfile`** - Wraps the stdio-based AWS MCP server with Supergateway to expose it as an SSE API service
  - Base image: `supercorp/supergateway:latest` (provides SSE API wrapper)
  - Installs Python and the `awslabs.aws-api-mcp-server` package
  - Exposes port 8000 for remote MCP connections
  - Converts stdio interface to HTTP SSE API for remote access

- **`aws-mcp-iam-policy.json`** - Comprehensive IAM policy with read-only permissions for AWS services
  - Covers: CloudWatch, EC2, EKS, ECS, RDS, S3, IAM, Cost Management, and more
  - All permissions are read-only (Get*, List*, Describe*)
  - Can be shared across multiple EKS clusters
  - No destructive operations allowed

- **`setup-irsa.sh`** - Automated script to set up IRSA (IAM Roles for Service Accounts)
  - Creates all necessary AWS and Kubernetes resources
  - Handles the complete IRSA setup process
  - Safe: won't overwrite existing resources, uses auto-suffix for conflicts
  - Usage: `./setup-irsa.sh --cluster-name <name> --region <region> [--namespace <namespace>]`

- **`enable-oidc-provider.sh`** - Enables OIDC provider for EKS cluster (prerequisite for IRSA)

## Understanding IRSA Requirements

For the AWS MCP server to access AWS APIs from within Kubernetes, it needs IRSA (IAM Roles for Service Accounts). This involves four key components:

1. **OIDC Provider**: Establishes trust between your EKS cluster and AWS IAM
2. **IAM Policy** (`holmes-aws-mcp-policy`): Defines what AWS services can be accessed (read-only)
3. **IAM Role** (`holmes-aws-mcp-role-{cluster-name}`): AWS identity that pods can assume
4. **Service Account** (`aws-api-mcp-sa`): Kubernetes resource that links pods to the IAM role

### How IRSA Works

1. Pod starts with the configured service account
2. AWS SDK in the pod reads the service account's IAM role annotation
3. Pod exchanges its Kubernetes token for temporary AWS credentials
4. Pod can now make AWS API calls with the permissions from the IAM policy

## Setup Instructions

### 1. Prerequisites

- EKS cluster running
- AWS CLI configured with permissions to create IAM roles/policies
- kubectl configured to access your cluster
- eksctl installed (for OIDC provider setup)

### 2. Automated Setup with setup-irsa.sh

The `setup-irsa.sh` script automates the entire IRSA setup process:

```bash
# Basic usage
./setup-irsa.sh --cluster-name my-cluster --region us-east-1

# With custom namespace
./setup-irsa.sh --cluster-name my-cluster --region us-east-1 --namespace holmes

# See all options
./setup-irsa.sh --help
```

The script will:
1. **Check Service Account**: Ensures no existing service account conflicts
2. **Verify/Create OIDC Provider**: Sets up trust between EKS and IAM
3. **Create/Reuse IAM Policy**: Uses existing `holmes-aws-mcp-policy` if found, creates if not
4. **Create IAM Role**: Creates cluster-specific role with auto-suffix if needed
5. **Create Service Account**: Sets up Kubernetes service account with IAM role annotation

### Important Notes on Multi-Cluster Setup

- **IAM Policy**: Can be shared across all clusters (created once, reused many times)
- **IAM Roles**: Must be unique per cluster (each has cluster-specific trust relationship)
- **Service Accounts**: Created in each cluster separately

Example for multiple clusters:
```bash
# Cluster 1
./setup-irsa.sh --cluster-name prod --region us-east-1

# Cluster 2 (will reuse the same IAM policy)
./setup-irsa.sh --cluster-name staging --region us-east-1

# Results in:
# - One shared policy: holmes-aws-mcp-policy
# - Two roles: holmes-aws-mcp-role-prod, holmes-aws-mcp-role-staging
# - Service account in each cluster
```

### 3. Docker Image - SSE API Wrapper

The AWS MCP server is originally a stdio-based tool. To make it accessible as a remote MCP server in Kubernetes, we wrap it with Supergateway, which converts stdio communication to an SSE (Server-Sent Events) API.

**Pre-built image available at:**
```
us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1
```

**How the Docker image works:**
1. Uses `supercorp/supergateway:latest` as base (provides SSE API wrapper)
2. Installs Python and the AWS MCP server package
3. Supergateway exposes the stdio interface as HTTP SSE on port 8000
4. This allows Holmes to connect to it as a remote MCP server

**To build your own:**
```bash
docker build -t your-registry/aws-api-mcp-server:latest .
docker push your-registry/aws-api-mcp-server:latest
```

### 6. Verify the Setup

#### Test IRSA Configuration
```bash
# Verify service account has correct annotation
kubectl get sa aws-api-mcp-sa -n default -o yaml

# Test AWS access with a temporary pod
kubectl run aws-cli-test \
  --image=amazon/aws-cli \
  --rm -it --restart=Never \
  --overrides='{"spec":{"serviceAccountName":"aws-api-mcp-sa"}}' \
  -n default \
  -- sts get-caller-identity

# Should return the IAM role ARN, not the node's role
```

### What Information is Available

Container Insights captures:
- **OOM Events**: Exact timestamp when pod was killed
- **Exit Codes**: 137 indicates SIGKILL (often OOM)
- **Memory Metrics**: Memory usage leading up to OOM
- **Container State**: Last state before termination
- **Restart Count**: Number of times pod has restarted
- **Resource Limits**: Configured memory limits
- **Memory Working Set**: Actual memory usage over time


## Troubleshooting

### MCP Server Not Responding

1. Check pod status:
   ```bash
   kubectl get pods -l app=aws-api-mcp-server
   kubectl logs -l app=aws-api-mcp-server
   ```

2. Verify IRSA is working:
   ```bash
   kubectl exec -it deploy/aws-api-mcp-server -- aws sts get-caller-identity
   ```

### Permission Issues

1. Check IAM role is attached:
   ```bash
   kubectl get sa aws-api-mcp-sa -o yaml
   ```

2. Verify IAM role has correct policies:
   ```bash
   aws iam list-attached-role-policies --role-name aws-api-mcp-role
   ```

## Security Considerations

- The MCP server has **read-only** access to AWS services
- IRSA ensures pods use temporary credentials
- No AWS credentials are stored in the cluster
- Access is scoped to specific service account

## Next Steps

1. Create evaluation tests for AWS scenarios:
   - ELB failure analysis
   - EC2 network issues
   - RDS performance problems
   - IAM permission debugging
   - Cost analysis queries

2. Enhance Holmes toolsets to leverage AWS data

3. Add more AWS service integrations as needed
