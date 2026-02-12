# AKS Node Health

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

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| aks_check_node_health | Perform comprehensive health checks on AKS nodes |
| aks_get_node_metrics | Get detailed metrics for AKS nodes |
| aks_diagnose_node_issues | Diagnose common node-level issues |
| aks_check_node_readiness | Check if nodes are ready and schedulable |
| aks_get_node_events | Get events related to specific nodes |
| aks_check_node_resources | Check resource utilization on nodes |
