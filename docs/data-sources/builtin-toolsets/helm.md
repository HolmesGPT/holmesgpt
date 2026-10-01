# Helm ✓

--8<-- "snippets/enabled_by_default.md"

By enabling this toolset, HolmesGPT will be able to provide read access to a cluster's Helm charts and releases.

## Configuration

In Kubernetes, the `secrets` rule is needed because Helm stores each release's record as a Secret. Unless `namespaceScopedRBAC` is `true`, the chart puts these rules in a ClusterRole, which lets Holmes read every Secret in the cluster. To limit Holmes to the namespace it runs in, use [Release namespace only](#release-namespace-only) below.

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        helm/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    toolsets:
        helm/core:
            enabled: true
    customClusterRoleRules:
        - apiGroups: [""]
          resources: ["secrets", "pods", "services", "configmaps", "persistentvolumeclaims"]
          verbs: ["get", "list", "watch"]
        - apiGroups: [""]
          resources: ["namespaces"]
          verbs: ["get"]
        - apiGroups: ["apps"]
          resources: ["deployments", "statefulsets", "daemonsets"]
          verbs: ["get", "list", "watch"]
        - apiGroups: ["batch"]
          resources: ["jobs", "cronjobs"]
          verbs: ["get", "list", "watch"]
        - apiGroups: ["networking.k8s.io"]
          resources: ["ingresses"]
          verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      toolsets:
          helm/core:
              enabled: true
      customClusterRoleRules:
          - apiGroups: [""]
            resources: ["secrets", "pods", "services", "configmaps", "persistentvolumeclaims"]
            verbs: ["get", "list", "watch"]
          - apiGroups: [""]
            resources: ["namespaces"]
            verbs: ["get"]
          - apiGroups: ["apps"]
            resources: ["deployments", "statefulsets", "daemonsets"]
            verbs: ["get", "list", "watch"]
          - apiGroups: ["batch"]
            resources: ["jobs", "cronjobs"]
            verbs: ["get", "list", "watch"]
          - apiGroups: ["networking.k8s.io"]
            resources: ["ingresses"]
            verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Release namespace only

With `namespaceScopedRBAC: true`, the chart renders all of Holmes's RBAC rules, not only these, as a Role and RoleBinding in the release namespace. Holmes can then read Secrets, Helm releases and every other resource only in that namespace, and cluster-scoped resources such as nodes not at all.

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    namespaceScopedRBAC: true

    toolsets:
        helm/core:
            enabled: true
    customClusterRoleRules:
        - apiGroups: [""]
          resources: ["secrets", "pods", "services", "configmaps", "persistentvolumeclaims"]
          verbs: ["get", "list", "watch"]
        - apiGroups: [""]
          resources: ["namespaces"]
          verbs: ["get"]
        - apiGroups: ["apps"]
          resources: ["deployments", "statefulsets", "daemonsets"]
          verbs: ["get", "list", "watch"]
        - apiGroups: ["batch"]
          resources: ["jobs", "cronjobs"]
          verbs: ["get", "list", "watch"]
        - apiGroups: ["networking.k8s.io"]
          resources: ["ingresses"]
          verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      namespaceScopedRBAC: true

      toolsets:
          helm/core:
              enabled: true
      customClusterRoleRules:
          - apiGroups: [""]
            resources: ["secrets", "pods", "services", "configmaps", "persistentvolumeclaims"]
            verbs: ["get", "list", "watch"]
          - apiGroups: [""]
            resources: ["namespaces"]
            verbs: ["get"]
          - apiGroups: ["apps"]
            resources: ["deployments", "statefulsets", "daemonsets"]
            verbs: ["get", "list", "watch"]
          - apiGroups: ["batch"]
            resources: ["jobs", "cronjobs"]
            verbs: ["get", "list", "watch"]
          - apiGroups: ["networking.k8s.io"]
            resources: ["ingresses"]
            verbs: ["get", "list", "watch"]
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| helm_list | Use to get all the current helm releases |
| helm_values | Use to gather Helm values or any released helm chart |
| helm_status | Check the status of a Helm release |
| helm_history | Get the revision history of a Helm release |
| helm_manifest | Fetch the generated Kubernetes manifest for a Helm release |
| helm_hooks | Get the hooks for a Helm release |
| helm_chart | Show the chart used to create a Helm release |
| helm_notes | Show the notes provided by the Helm chart |
