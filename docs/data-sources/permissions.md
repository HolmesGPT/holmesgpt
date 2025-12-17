# Adding Permissions for Additional Resources (In-Cluster Deployments)

!!! note "In-Cluster Only"
    This page applies only to HolmesGPT running **inside** a Kubernetes cluster via Helm. For local CLI deployments, permissions are managed through your kubeconfig file.

HolmesGPT may require access to additional Kubernetes resources or CRDs for specific analyses. Permissions can be extended by modifying the ClusterRole rules.

## Default CRD Permissions

HolmesGPT includes read-only permissions for common Kubernetes operators and tools by default. These can be individually enabled or disabled:

| CRD Permission | Default | Description |
|----------------|---------|-------------|
| `argo` | `true` | Argo CD, Workflows, Rollouts, Events |
| `flux` | `true` | Flux CD GitOps toolkit |
| `kafka` | `true` | Strimzi Kafka operator |
| `keda` | `true` | KEDA autoscaling |
| `crossplane` | `true` | Crossplane compositions and providers |
| `istio` | `true` | Istio service mesh |
| `gatewayApi` | `true` | Kubernetes Gateway API |
| `velero` | `true` | Velero backup and restore |

### Disabling Unused CRD Permissions

You can disable these permissions using the following configuration example:
=== "Holmes Helm Chart"

    ```yaml
    crdPermissions:
      argo: true
      flux: true
      kafka: false      # Disable if not using Strimzi Kafka
      keda: false       # Disable if not using KEDA
      crossplane: false # Disable if not using Crossplane
      istio: false      # Disable if not using Istio
      gatewayApi: false # Disable if not using Gateway API
      velero: false     # Disable if not using Velero
    ```

=== "Robusta Helm Chart"

    ```yaml
    enableHolmesGPT: true
    holmes:
      crdPermissions:
        argo: true
        flux: true
        kafka: false
        keda: false
        crossplane: false
        istio: false
        gatewayApi: false
        velero: false
    ```

## Adding Custom Permissions

For resources not covered by the default CRD permissions, you can add custom ClusterRole rules.

### Common Scenarios

1. **External Integrations and CRDs** - Access to custom resources from other operators
2. **Additional Kubernetes resources** - Resources not included in the default permissions

## Example: Adding Cert-Manager Permissions

To enable HolmesGPT to analyze cert-manager certificates and issuers (not included in default permissions), add custom ClusterRole rules:

=== "Holmes Helm Chart"

    ```yaml
    customClusterRoleRules:
      - apiGroups: ["cert-manager.io"]
        resources: ["certificates", "certificaterequests", "issuers", "clusterissuers"]
        verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes --values=values.yaml
    ```

=== "Robusta Helm Chart"

    ```yaml
    enableHolmesGPT: true
    holmes:
      customClusterRoleRules:
        - apiGroups: ["cert-manager.io"]
          resources: ["certificates", "certificaterequests", "issuers", "clusterissuers"]
          verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```
