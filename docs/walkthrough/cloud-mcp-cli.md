# Cloud Provider MCPs (CLI)

Use HolmesGPT CLI with AWS, Azure, and GCP MCP servers running locally on your machine -- no Kubernetes cluster required.

## How It Works

Each cloud provider publishes an official MCP server that runs as a local subprocess. Holmes launches these servers automatically via stdio and communicates with them directly. Authentication uses your existing cloud CLI credentials (`aws`, `az`, `gcloud`).

```
┌──────────────┐     stdio     ┌──────────────────┐      API       ┌───────────┐
│  Holmes CLI  │ ─────────────>│  MCP Server      │ ──────────────>│ Cloud API │
│              │  (subprocess) │  (local process)  │  CLI creds    │           │
└──────────────┘               └──────────────────┘                └───────────┘
```

## Prerequisites

- HolmesGPT CLI installed ([installation guide](../installation/cli-installation.md))
- An AI provider API key configured ([setup guide](../ai-providers/index.md))

## AWS

The [official AWS MCP server](https://github.com/awslabs/mcp) runs via `uvx` (requires [uv](https://docs.astral.sh/uv/getting-started/installation/)).

**Step 1: Authenticate**

```bash
# Option A: Use an existing AWS profile
aws sts get-caller-identity --profile your-profile

# Option B: Set credentials directly
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
```

**Step 2: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  aws_api:
    description: "AWS API - execute AWS CLI commands for investigating infrastructure issues"
    config:
      mode: stdio
      command: "uvx"
      args: ["awslabs.aws-api-mcp-server@latest"]
      env:
        AWS_REGION: "us-east-1"
        READ_OPERATIONS_ONLY: "true"
        # Uncomment to use a specific profile:
        # AWS_API_MCP_PROFILE_NAME: "your-profile"
    llm_instructions: |
      Use this server to investigate AWS infrastructure issues.
      Always gather current state, check CloudTrail for recent changes,
      and collect CloudWatch metrics before providing conclusions.
      Never tell the user to check the AWS console - query it yourself.
```

**Step 3: Test it**

```bash
holmes ask "List my EC2 instances and their current status"
```

## Azure

The [official Azure MCP server](https://github.com/microsoft/mcp) runs via `npx` (requires Node.js 20+).

**Step 1: Authenticate**

```bash
az login
az account show  # verify correct subscription
```

**Step 2: Add to `~/.holmes/config.yaml`**

```yaml
mcp_servers:
  azure_api:
    description: "Azure API - query Azure resources and investigate infrastructure issues"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@azure/mcp@latest", "server", "start"]
    llm_instructions: |
      Use this server to investigate Azure infrastructure issues.
      Always gather current state, check Activity Log for recent changes,
      and collect Azure Monitor data before providing conclusions.
      Never tell the user to check the Azure portal - query it yourself.
```

??? info "Server modes"
    The Azure MCP server supports different modes that control how many tools are exposed:

    - **Default (namespace mode)**: one tool per Azure service namespace
    - **Consolidated mode** (recommended): curated tools grouped by user intent
    - **All mode**: exposes 200+ individual tools

    To use consolidated mode, change the args:
    ```yaml
    args: ["-y", "@azure/mcp@latest", "server", "start", "--mode", "consolidated"]
    ```

    To limit to specific services:
    ```yaml
    args: ["-y", "@azure/mcp@latest", "server", "start", "--namespace", "compute", "--namespace", "network"]
    ```

**Step 3: Test it**

```bash
holmes ask "List all resource groups in my Azure subscription"
```

## GCP

Google publishes [multiple MCP servers](https://github.com/googleapis/gcloud-mcp) via `npx` (requires Node.js). The main one covers general gcloud CLI operations; additional servers cover observability and storage.

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
    llm_instructions: |
      Use for general GCP resource management and investigation.
      Query compute instances, networking, IAM, and audit logs.
  gcp_observability:
    description: "GCP Observability - Cloud Logging, Monitoring, Trace, Error Reporting"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/observability-mcp"]
    llm_instructions: |
      Use for Cloud Logging, Monitoring, Trace, and Error Reporting.
      Can retrieve historical logs for deleted Kubernetes resources.
  gcp_storage:
    description: "Google Cloud Storage operations"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/storage-mcp"]
    llm_instructions: |
      Use for investigating Cloud Storage bucket issues,
      access permissions, and object operations.
```

You can use all three servers together or pick only the ones you need.

**Step 3: Test it**

```bash
holmes ask "List all GKE clusters in my project"
```

## Using Multiple Providers

You can configure all three providers simultaneously. Holmes will choose the right MCP server based on the question:

```yaml
mcp_servers:
  aws_api:
    description: "AWS API - execute AWS CLI commands"
    config:
      mode: stdio
      command: "uvx"
      args: ["awslabs.aws-api-mcp-server@latest"]
      env:
        AWS_REGION: "us-east-1"
        READ_OPERATIONS_ONLY: "true"
    llm_instructions: "Use for investigating AWS infrastructure."
  azure_api:
    description: "Azure API - query Azure resources"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@azure/mcp@latest", "server", "start"]
    llm_instructions: "Use for investigating Azure infrastructure."
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/gcloud-mcp"]
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
# Verify uvx is installed (for AWS)
uvx --version

# Verify npx is installed (for Azure, GCP)
npx --version

# Test AWS MCP server directly
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}' | AWS_REGION=us-east-1 READ_OPERATIONS_ONLY=true uvx awslabs.aws-api-mcp-server@latest

# Test Azure MCP server directly
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}}}' | npx -y @azure/mcp@latest server start

# Verify cloud CLI authentication
aws sts get-caller-identity        # AWS
az account show                    # Azure
gcloud auth list                   # GCP
```

## What's Next?

- **[AWS (MCP)](../data-sources/builtin-toolsets/aws.md)** - Kubernetes deployment and multi-account support
- **[Azure (MCP)](../data-sources/builtin-toolsets/azure-mcp.md)** - Kubernetes deployment and all auth methods
- **[GCP (MCP)](../data-sources/builtin-toolsets/gcp.md)** - Kubernetes deployment and Workload Identity
- **[MCP Servers](../data-sources/remote-mcp-servers.md)** - General MCP server configuration reference
