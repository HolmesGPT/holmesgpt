# Investigating using AKS MCP Server

You can investigate Azure Kubernetes Service issues using HolmesGPT with the AKS MCP (Model Context Protocol) server.

![AKS MCP Integration](../assets/Holmes-azure-mcp.gif)

## Prerequisites

- HolmesGPT CLI installed ([installation guide](../installation/cli-installation.md))
- An AI provider API key configured ([setup guide](../ai-providers/index.md))
- Azure CLI installed and authenticated
- Access to Azure Kubernetes Service clusters
- [Azure Kubernetes Service](https://marketplace.visualstudio.com/items?itemName=ms-kubernetes-tools.vscode-aks-tools) VS Code extension installed

## Setting Up AKS MCP Server

### Step 1: Setup the MCP Server

- Open VS Code Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`)
- Run: **"AKS: Setup AKS MCP Server"**
- Follow the setup wizard to configure your Azure credentials and cluster access

### Step 2: Update Configuration for SSE
   After installation, update your VS Code MCP configuration (`.vscode/mcp.json`) to use SSE transport and start the server
   ```json
   {
     "servers": {
       "AKS MCP": {
         "command": "/Users/yourname/.vs-kubernetes/tools/aks-mcp/v0.0.3/aks-mcp",
         "args": [
           "--transport",
           "sse"
         ]
       }
     }
   }
   ```
   **Note:** Change `"stdio"` to `"sse"` in the transport argument.

### Step 3: Configure HolmesGPT

Add this configuration to your HolmesGPT config file (`~/.holmes/config.yaml`):

```yaml
mcp_servers:
  aks-mcp:
    description: "Azure Kubernetes Service(AKS) Model Context Protocol(MCP) server"
    url: "http://localhost:8000/sse"
    llm_instructions: "MCP server to get AKS cluster information, retrieve cluster resources and workloads, analyze network policies and VNet configurations, query control plane logs, fetch cluster metrics and health status. Investigate networking issues with NSGs and load balancers, perform kubectl operations, real-time monitoring of DNS, services across Azure Kubernetes environments"

```

## Investigation Examples

Once configured, you can investigate AKS issues using natural language queries:

### Cluster Health Issues
```bash
holmes ask "What issues do I have in my AKS cluster?"
```

### Network Connectivity Problems
```bash
holmes ask "My payment deployment can't reach external services investigate why"
```

## What's Next?

- **[Add more data sources](../data-sources/index.md)** - Combine AKS MCP with other observability tools
- **[Set up additional MCP servers](../data-sources/remote-mcp-servers.md)** - Integrate multiple specialized MCP servers
- **[Configure custom toolsets](../data-sources/custom-toolsets.md)** - Create specialized investigation workflows
