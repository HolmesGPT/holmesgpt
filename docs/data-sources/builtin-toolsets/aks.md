# Azure Kubernetes Service (AKS)

!!! tip "Consider Azure MCP instead"
    Most users should start with the built-in [Kubernetes](kubernetes.md) integration and the [Azure MCP](azure-mcp.md) integration. Together, these provide broad access to Kubernetes resources and all Azure APIs including AKS. This standalone toolset is only needed if you require specific AKS CLI commands that aren't available through the MCP server.

By enabling this toolset, HolmesGPT will be able to interact with Azure Kubernetes Service clusters, providing Azure-specific troubleshooting capabilities and cluster management.

## Prerequisites

1. Azure CLI installed and configured
2. Appropriate Azure RBAC permissions for AKS clusters
3. Access to the target AKS cluster

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
      aks/core:
        enabled: true
        config:
          subscription_id: "<your Azure subscription ID>" # Optional
          resource_group: "<your AKS resource group>" # Optional
          cluster_name: "<your AKS cluster name>" # Optional
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        aks/core:
          enabled: true
          config:
            subscription_id: "<your Azure subscription ID>"
            resource_group: "<your AKS resource group>"
            cluster_name: "<your AKS cluster name>"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Advanced Configuration

You can configure additional Azure settings:

```yaml
toolsets:
  aks/core:
    enabled: true
    config:
      subscription_id: "<your Azure subscription ID>"
      resource_group: "<your AKS resource group>"
      cluster_name: "<your AKS cluster name>"
      location: "eastus"  # Azure region
      timeout: 60  # Request timeout in seconds
```

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| cloud_provider | Fetch the cloud provider of the Kubernetes cluster from node providerIDs |
| aks_get_cluster | Get the configuration details of a specific AKS cluster |
| aks_list_clusters_by_rg | List all AKS clusters under a specific resource group |
| aks_list_node_pools | List node pools in an AKS cluster |
| aks_show_node_pool | Show details of a specific node pool in an AKS cluster |
| aks_list_versions | List supported Kubernetes versions in a region |
| aks_get_credentials | Download kubeconfig file for an AKS cluster |
| aks_list_addons | List all available AKS addons |
| get_default_subscription | Retrieve the current Azure CLI default subscription ID |
| get_cluster_name | Retrieve the active Kubernetes cluster name from kubeconfig |
| get_cluster_resource_group | Retrieve the resource group name for the AKS cluster |
| get_node_resource_group | Retrieve the node resource group name for the AKS cluster |
| get_api_server_public_ip | Get the public IP of kube-apiserver for a public AKS cluster |
| get_all_nsgs | Get all Network Security Group (NSG) instances in a subscription |
| get_nsg_rules | Get all NSG rules associated with a specific NSG |
