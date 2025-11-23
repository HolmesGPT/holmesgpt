# Kubernetes Permissions

This document explains how HolmesGPT handles Kubernetes permissions and what permissions it needs to work effectively and provide the best results.

!!! important "Read-Only Permissions"
    **All permissions granted to HolmesGPT are read-only** (`get`, `list`, `watch`). HolmesGPT **does not modify, create, delete, or update** any Kubernetes resources. It only reads cluster information for troubleshooting and analysis purposes.

## How HolmesGPT Inherits Permissions

HolmesGPT inherits permissions for accessing Kubernetes from its environment:

- **When running locally**: HolmesGPT uses your current `kubectl` context and the permissions configured in your kubeconfig file.
- **When running in-cluster**: HolmesGPT uses the ServiceAccount defined in the Helm chart. The Helm chart automatically creates a ServiceAccount, ClusterRole, and ClusterRoleBinding when `createServiceAccount: true` (default). See the [Service Account Configuration](helm-configuration.md#service-account-configuration) section for details.

The complete ServiceAccount, ClusterRole, and ClusterRoleBinding definitions can be found in the Helm chart template:

[**View Service Account Template**](https://raw.githubusercontent.com/robusta-dev/holmesgpt/refs/heads/master/helm/holmes/templates/holmesgpt-service-account.yaml)

## Adaptive Behavior

HolmesGPT automatically adjusts its behavior based on available permissions:

- **You can modify these permissions** and HolmesGPT will automatically adapt to work with whatever permissions are available.
- **If HolmesGPT tries to run `kubectl` commands** that it doesn't have permissions for, **it will discover the lack of permissions** and adjust its behavior accordingly. It will work with the resources it can access and inform you about any limitations.

## Recommended Permissions

For most users, we recommend giving **read-access to all non-sensitive resources** in the cluster. This allows HolmesGPT to:

- Investigate issues across all namespaces
- Access logs and events
- Analyze resource configurations
- Provide comprehensive troubleshooting insights

The default permissions created by the Helm chart follow this recommendation and include read-only access (`get`, `list`, `watch`) to core Kubernetes resources, custom resources, and monitoring resources across all namespaces.

## Adjusting Permissions

If you want to adjust the permissions, you can do so by:

### Using Custom RBAC Rules

You can extend the default permissions by adding custom rules to your Helm `values.yaml`:

```yaml
customClusterRoleRules:
  - apiGroups: ["argoproj.io"]
    resources: ["applications", "appprojects"]
    verbs: ["get", "list", "watch"]
```

### Using an Existing ServiceAccount

If you prefer to use an existing ServiceAccount with custom permissions:

```yaml
createServiceAccount: false
customServiceAccountName: "your-existing-service-account"
```

For more information, see [Adding Permissions for Additional Resources](../data-sources/permissions.md).

## Related Documentation

- [Kubernetes Installation Guide](../installation/kubernetes-installation.md) - Step-by-step Helm installation
- [Helm Configuration](helm-configuration.md) - Complete Helm chart configuration reference
- [Adding Permissions for Additional Resources](../data-sources/permissions.md) - How to extend permissions for custom resources
