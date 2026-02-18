# Cloud Provider MCPs (CLI)

Use HolmesGPT CLI with AWS, Azure, and GCP MCP servers to investigate cloud infrastructure issues from your terminal.

## How It Works

Each cloud provider has an MCP server that runs as a pod in a Kubernetes cluster. Holmes CLI connects to these servers over HTTP to query cloud APIs. Even when running Holmes locally, the MCP servers must be deployed to a cluster - they handle authentication via cloud-native mechanisms (IRSA, Workload Identity, etc.).

```
┌──────────────┐     HTTP      ┌─────────────────────┐      API       ┌───────────┐
│  Holmes CLI  │ ─────────────>│  MCP Server (K8s)   │ ──────────────>│ Cloud API │
│  (local)     │  port-forward │  (handles auth)     │  IRSA/WI/SA   │           │
└──────────────┘               └─────────────────────┘                └───────────┘
```

## Prerequisites

- HolmesGPT CLI installed ([installation guide](../installation/cli-installation.md))
- An AI provider API key configured ([setup guide](../ai-providers/index.md))
- `kubectl` access to a Kubernetes cluster
- Cloud provider CLI (`aws`, `az`, or `gcloud`) for IAM setup

## AWS

**Step 1: Set up IAM and deploy the MCP server**

Follow the [AWS (MCP) setup guide](../data-sources/builtin-toolsets/aws.md#single-account-setup) - complete Step 1 (IAM) and the "Holmes CLI" tab of Step 2 (deploy).

**Step 2: Port-forward the MCP server**

```bash
kubectl port-forward -n holmes-mcp svc/aws-mcp-server 8000:8000
```

**Step 3: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  aws_api:
    description: "AWS API MCP Server"
    url: "http://localhost:8000"
    llm_instructions: |
      Use this server to investigate AWS infrastructure issues.
      Always gather current state, check CloudTrail for recent changes,
      and collect CloudWatch metrics before providing conclusions.
      Never tell the user to check the AWS console - query it yourself.
```

**Step 4: Test it**

```bash
holmes ask "List my EC2 instances and their current status"
```

## Azure

**Step 1: Deploy the MCP server and configure authentication**

Follow the [Azure (MCP) setup guide](../data-sources/builtin-toolsets/azure-mcp.md) - complete the "Holmes CLI" tab under Configuration.

**Step 2: Port-forward the MCP server**

```bash
kubectl port-forward -n holmes-mcp svc/azure-mcp-server 8000:8000
```

**Step 3: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  azure_api:
    description: "Azure API MCP Server"
    url: "http://localhost:8000"
    llm_instructions: |
      Use this server to investigate Azure infrastructure issues.
      Always gather current state via Azure CLI commands, check Activity Log
      for recent changes, and collect Azure Monitor data before providing conclusions.
      Never tell the user to check the Azure portal - query it yourself.
```

**Step 4: Test it**

```bash
holmes ask "List all resource groups in my Azure subscription"
```

## GCP

GCP uses three specialized MCP servers: gcloud (general CLI), observability (logs, metrics, traces), and storage.

**Step 1: Create a GCP service account and deploy the MCP servers**

Follow the [GCP (MCP) setup guide](../data-sources/builtin-toolsets/gcp.md#service-account-key) - complete the "Holmes CLI" tab.

**Step 2: Port-forward all three servers**

```bash
kubectl port-forward -n holmes-mcp svc/gcp-mcp-server 8000:8000 8001:8001 8002:8002
```

**Step 3: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      url: "http://localhost:8000/sse"
      mode: "sse"
    llm_instructions: |
      Use for general GCP resource management and investigation.
      Query compute instances, networking, IAM, and audit logs.
  gcp_observability:
    description: "GCP Observability - logs, metrics, traces"
    config:
      url: "http://localhost:8001/sse"
      mode: "sse"
    llm_instructions: |
      Use for Cloud Logging, Monitoring, Trace, and Error Reporting.
      Can retrieve historical logs for deleted Kubernetes resources.
  gcp_storage:
    description: "Google Cloud Storage operations"
    config:
      url: "http://localhost:8002/sse"
      mode: "sse"
    llm_instructions: |
      Use for investigating Cloud Storage bucket issues,
      access permissions, and object operations.
```

**Step 4: Test it**

```bash
holmes ask "List all GKE clusters in my project"
```

## Using Multiple Providers

You can configure all three providers simultaneously. Holmes will choose the right MCP server based on the question:

```yaml
mcp_servers:
  aws_api:
    description: "AWS API MCP Server"
    url: "http://localhost:8000"
    llm_instructions: "Use for investigating AWS infrastructure."
  azure_api:
    description: "Azure API MCP Server"
    url: "http://localhost:8001"
    llm_instructions: "Use for investigating Azure infrastructure."
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      url: "http://localhost:8002/sse"
      mode: "sse"
    llm_instructions: "Use for investigating GCP infrastructure."
```

```bash
# Holmes selects the AWS MCP server
holmes ask "Why can't my app connect to RDS?"

# Holmes selects the Azure MCP server
holmes ask "What changed in our Azure infrastructure today?"

# Holmes selects the GCP MCP server
holmes ask "Show me logs from the payment-service pod that was OOMKilled"
```

## Troubleshooting

```bash
# Verify MCP server pods are running
kubectl get pods -n holmes-mcp

# Check MCP server logs for errors
kubectl logs -n holmes-mcp -l app=aws-mcp-server
kubectl logs -n holmes-mcp -l app=azure-mcp-server
kubectl logs -n holmes-mcp deployment/gcp-mcp-server --all-containers

# Test connectivity from your machine (with port-forward active)
curl http://localhost:8000/health
```

## What's Next?

- **[AWS (MCP)](../data-sources/builtin-toolsets/aws.md)** - Full setup reference including multi-account support
- **[Azure (MCP)](../data-sources/builtin-toolsets/azure-mcp.md)** - Full setup reference including all auth methods
- **[GCP (MCP)](../data-sources/builtin-toolsets/gcp.md)** - Full setup reference including Workload Identity
- **[MCP Servers](../data-sources/remote-mcp-servers.md)** - General MCP server configuration reference
