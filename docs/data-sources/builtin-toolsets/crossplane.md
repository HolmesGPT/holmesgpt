# Crossplane

By enabling this toolset, HolmesGPT will be able to troubleshoot Crossplane-managed infrastructure by inspecting providers, compositions, claims, composite resources, and managed resources across the full resource hierarchy.

## Prerequisites

Crossplane must be installed on your Kubernetes cluster. HolmesGPT uses `kubectl` to query Crossplane custom resources, so no additional CLI tools are required.

HolmesGPT needs read access to Crossplane CRDs. If you use Kubernetes RBAC, ensure the service account has permissions to `get` and `list` the following API groups:

```yaml
# Add to your ClusterRole
- apiGroups: ["pkg.crossplane.io"]
  resources: ["providers", "providerrevisions"]
  verbs: ["get", "list"]
- apiGroups: ["apiextensions.crossplane.io"]
  resources: ["compositeresourcedefinitions", "compositions"]
  verbs: ["get", "list"]
# For managed resources, add the specific API groups used by your providers.
# Example for AWS provider:
- apiGroups: ["s3.aws.upbound.io", "rds.aws.upbound.io", "ec2.aws.upbound.io"]
  resources: ["*"]
  verbs: ["get", "list"]
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
        crossplane/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Which Crossplane managed resources are failing and why?"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        customClusterRoleRules:
            - apiGroups: ["pkg.crossplane.io"]
              resources: ["providers", "providerrevisions"]
              verbs: ["get", "list"]
            - apiGroups: ["apiextensions.crossplane.io"]
              resources: ["compositeresourcedefinitions", "compositions"]
              verbs: ["get", "list"]
        toolsets:
            crossplane/core:
                enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| crossplane_list_providers | List all installed Crossplane providers with health status, version, and package reference |
| crossplane_get_provider | Get detailed status of a specific Crossplane provider including conditions and revision |
| crossplane_list_provider_revisions | List provider revisions to check for version rollout issues or stuck upgrades |
| crossplane_list_provider_configs | List ProviderConfigs of a specific type to check credential configurations |
| crossplane_get_provider_config | Get detailed ProviderConfig including credential source and configuration |
| crossplane_list_xrds | List all CompositeResourceDefinitions (XRDs) |
| crossplane_get_xrd | Get details of a specific XRD including schema, claim names, and offered versions |
| crossplane_list_compositions | List all Compositions which define how composite resources map to managed resources |
| crossplane_get_composition | Get details of a specific Composition including resource templates and patch sets |
| crossplane_get_claim | Get a Crossplane claim's full status including conditions and connection details |
| crossplane_get_composite_resource | Get a composite resource (XR) including status, conditions, and composed references |
| crossplane_list_managed_resources | List managed resources of a specific kind with sync and ready status |
| crossplane_get_managed_resource | Get full details of a specific managed resource including conditions and external name |
| crossplane_get_resource_events | Get Kubernetes events for a specific Crossplane resource |
| crossplane_list_managed_by_composite | List all managed resources owned by a specific composite resource |

## Common Use Cases

```bash
holmes ask "Which Crossplane managed resources are failing and why?"
```

```bash
holmes ask "Are all Crossplane providers healthy?"
```

```bash
holmes ask "Trace the claim my-database in namespace production and find which managed resource is broken"
```

```bash
holmes ask "Why is my S3 bucket not becoming ready?"
```
