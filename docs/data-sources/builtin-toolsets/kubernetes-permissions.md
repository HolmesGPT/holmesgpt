# Kubernetes Permissions (In-Cluster)

!!! note "In-Cluster Only"
    This page applies only to HolmesGPT running **inside** a Kubernetes cluster via Helm. For local CLI deployments, permissions are managed through your kubeconfig file.

HolmesGPT may require access to additional Kubernetes resources or CRDs for specific analyses. Permissions can be extended by modifying the ClusterRole rules.

## Default CRD Permissions

HolmesGPT includes read-only permissions for common Kubernetes operators and tools by default. These can be individually enabled or disabled:

=== "Holmes Helm Chart"

    ```yaml
    crdPermissions:
      argo: true
      flux: true
      kafka: true
      keda: true
      crossplane: true
      istio: true
      gatewayApi: true
      velero: true
      externalSecrets: true
    ```

=== "Robusta Helm Chart"

    ```yaml
    enableHolmesGPT: true
    holmes:
      crdPermissions:
        argo: true
        flux: true
        kafka: true
        keda: true
        crossplane: true
        istio: true
        gatewayApi: true
        velero: true
        externalSecrets: true
    ```

## Adding Custom Permissions

For resources not covered by the default CRD permissions, you can add custom ClusterRole rules.

**Common scenarios:**

- **External Integrations and CRDs** - Access to custom resources from other operators
- **Additional Kubernetes resources** - Resources not included in the default permissions

**Example: Adding Cert-Manager Permissions**

To enable HolmesGPT to analyze cert-manager certificates and issuers (not included in default permissions), add custom ClusterRole rules:

=== "Holmes Helm Chart"

    **Update your `values.yaml`:**

    ```yaml
    customClusterRoleRules:
      - apiGroups: ["cert-manager.io"]
        resources: ["certificates", "certificaterequests", "issuers", "clusterissuers"]
        verbs: ["get", "list", "watch"]
    ```

    **Apply the configuration:**

    ```bash
    helm upgrade holmes holmes/holmes --values=values.yaml
    ```

=== "Robusta Helm Chart"

    **Update your `generated_values.yaml`** (note: add the `holmes:` prefix):

    ```yaml
    enableHolmesGPT: true
    holmes:
      customClusterRoleRules:
        - apiGroups: ["cert-manager.io"]
          resources: ["certificates", "certificaterequests", "issuers", "clusterissuers"]
          verbs: ["get", "list", "watch"]
    ```

    **Apply the configuration:**

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```
