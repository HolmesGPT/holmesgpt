# Helm ✓

--8<-- "snippets/enabled_by_default.md"

By enabling this toolset, HolmesGPT will be able to provide read access to a cluster's Helm charts and releases.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        helm/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

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

    --8<-- "snippets/helm_upgrade_command.md"

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
