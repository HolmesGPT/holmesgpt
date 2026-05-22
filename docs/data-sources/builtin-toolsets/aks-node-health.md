# AKS Node Health

!!! tip "Consider Azure MCP instead"
    Most users should start with the [Azure MCP](azure-mcp.md) integration, which provides broad access to all Azure APIs including AKS node diagnostics. This standalone toolset is only needed if you require specific AKS node health CLI commands that aren't available through the MCP server.

By enabling this toolset, HolmesGPT will be able to perform specialized health checks and troubleshooting for Azure Kubernetes Service (AKS) nodes, including node-specific diagnostics and performance analysis.

## Prerequisites

1. Azure CLI installed and configured
2. Appropriate Azure RBAC permissions for AKS clusters
3. Access to the target AKS cluster
4. Node-level access permissions

## Configuration

=== "Holmes CLI"

    First, ensure you're authenticated with Azure:

    ```bash
    az login
    az account set --subscription "<your subscription id>"
    ```

    Then add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      aks/node-health:
        enabled: true
        config:
          subscription_id: "<your Azure subscription ID>"
          resource_group: "<your AKS resource group>"
          cluster_name: "<your AKS cluster name>"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        aks/node-health:
          enabled: true
          config:
            subscription_id: "<your Azure subscription ID>"
            resource_group: "<your AKS resource group>"
            cluster_name: "<your AKS cluster name>"
    ```

## Advanced Configuration

You can configure additional health check parameters:

```yaml
toolsets:
  aks/node-health:
    enabled: true
    config:
      subscription_id: "<your Azure subscription ID>"
      resource_group: "<your AKS resource group>"
      cluster_name: "<your AKS cluster name>"
      health_check_interval: 300  # Health check interval in seconds
      max_unhealthy_nodes: 3  # Maximum number of unhealthy nodes to report
```

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| check_node_status | Checks the status of all nodes in the AKS cluster. |
| describe_node | Describes a specific node in the AKS cluster to inspect its conditions. |
| get_node_events | Fetches recent events for a specific node to surface warnings and errors. |
| check_node_resource_usage | Shows CPU/memory usage for a specific node (requires metrics-server). |
| review_activity_log | Reviews the Azure Activity Log for recent changes affecting the node. |
| check_top_resource_consuming_pods | Checks for the top resource-consuming pods on a specific node. |
| check_network_outbound | Checks the outbound network connectivity for an AKS cluster. |
| check_network_inbound | Checks the inbound network connectivity for an AKS cluster. |
| list_vmss_names | Lists all VMSS names in the cluster node resource group. |
