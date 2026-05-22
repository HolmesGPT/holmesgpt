# KubeVela

This toolset provides access to KubeVela CLI commands for managing and troubleshooting applications built on the Open Application Model (OAM).

## Prerequisites

The KubeVela CLI (`vela`) must be installed and configured to access your cluster.

**Installation:**

```bash
# Install vela CLI
curl -fsSl https://kubevela.io/script/install.sh | bash

# Verify installation
vela version
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    <!-- markdownlint-disable-next-line MD046 -->
    ```yaml
    toolsets:
        kubevela/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "What is the status of my KubeVela applications?"
    ```

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| vela_app_list | List all KubeVela applications across namespaces or in a specific namespace |
| vela_app_status | Get detailed status of a KubeVela application including components, traits, and health |
| vela_app_show | Show the application specification and configuration |
| vela_logs | Get logs from a KubeVela application or specific component |
| vela_port_forward | Check port-forward capability for debugging (informational only) |
| vela_component_list | List available component definitions in the cluster |
| vela_trait_list | List available trait definitions (like ingress, autoscaling, etc.) |
| vela_workflow_list | List workflow definitions available in the cluster |
| vela_workflow_status | Get the status of a workflow execution for an application |
| vela_workflow_logs | Get logs from a workflow execution |
| vela_addon_list | List installed KubeVela addons |
| vela_addon_status | Get detailed status of a specific addon |
| vela_definition_show | Show details of a component, trait, or workflow definition |
| vela_top | Show resource usage (CPU/Memory) for application pods |
| vela_dry_run | Preview the Kubernetes resources that would be created by an application spec |
| vela_live_diff | Show difference between local spec and running application |

## Common Use Cases

```bash
holmes ask "What KubeVela applications are unhealthy and why?"
```

```bash
holmes ask "Show me the workflow status for my payment-service application"
```

```bash
holmes ask "What components does my frontend application have and are they running correctly?"
```

```bash
holmes ask "Check if there are any trait configuration issues in the user-api application"
```
