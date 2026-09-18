# Kyverno

By enabling this toolset, HolmesGPT will be able to troubleshoot Kyverno policy engine issues, including policy violations, admission webhook failures, and background scan results.

## Prerequisites

Kyverno must be installed on your Kubernetes cluster. HolmesGPT uses `kubectl` to query Kyverno custom resources, so no additional CLI tools are required.

HolmesGPT needs read access to Kyverno CRDs. If you use Kubernetes RBAC, ensure the service account has permissions to `get` and `list` the following resources:

```yaml
# Add to your ClusterRole
- apiGroups: ["kyverno.io"]
  resources: ["clusterpolicies", "policies"]
  verbs: ["get", "list"]
- apiGroups: ["reports.kyverno.io", "wgpolicyk8s.io"]
  resources: ["clusterpolicyreports", "policyreports"]
  verbs: ["get", "list"]
- apiGroups: ["kyverno.io"]
  resources: ["updaterequests", "clusterupdaterequests"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list"]
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
        kyverno/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Are there any Kyverno policy violations in my cluster?"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        customClusterRoleRules:
            - apiGroups: ["kyverno.io"]
              resources: ["clusterpolicies", "policies", "updaterequests", "clusterupdaterequests"]
              verbs: ["get", "list"]
            - apiGroups: ["reports.kyverno.io", "wgpolicyk8s.io"]
              resources: ["clusterpolicyreports", "policyreports"]
              verbs: ["get", "list"]
        toolsets:
            kyverno/core:
                enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Common Use Cases

```bash
holmes ask "Are there any Kyverno policy violations in my cluster?"
```

```bash
holmes ask "Which resources are failing the require-labels policy?"
```

```bash
holmes ask "Why was my deployment blocked by Kyverno?"
```

```bash
holmes ask "Are there any failing UpdateRequests in Kyverno?"
```

```bash
holmes ask "Show me the Kyverno controller logs for recent errors"
```
