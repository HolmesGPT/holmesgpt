# Kueue

By enabling this toolset, HolmesGPT will be able to diagnose why a [Kueue](https://kueue.sigs.k8s.io)-managed Kubernetes workload is stuck Pending — insufficient quota, a stuck admission check, a misconfigured LocalQueue/ClusterQueue reference, a Job Kueue never intercepted, a gang-scheduled pod-group running short, or a workload preempted by a higher-priority one.

## Prerequisites

Kueue must be installed on your Kubernetes cluster. HolmesGPT uses `kubectl` and `jq` to query Kueue's custom resources (`Workload`, `LocalQueue`, `ClusterQueue`), so no additional CLI tools are required beyond those.

HolmesGPT needs read access to Kueue's CRDs, plus the standard Job/Pod/Event resources its tools cross-reference (e.g. `kueue_check_pod_labels`, `kueue_check_podgroup_status`, `kueue_get_workload_events`). If you use Kubernetes RBAC, ensure the service account has:

```yaml
# Add to your ClusterRole
- apiGroups: ["kueue.x-k8s.io"]
  resources: ["workloads", "localqueues", "clusterqueues"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods", "events"]
  verbs: ["get", "list"]
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["get", "list"]
- apiGroups: ["apiextensions.k8s.io"]
  resources: ["customresourcedefinitions"]
  verbs: ["get"]
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
        kueue/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Why is the Job my-training-job in namespace ml-team stuck Pending?"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        customClusterRoleRules:
            - apiGroups: ["kueue.x-k8s.io"]
              resources: ["workloads", "localqueues", "clusterqueues"]
              verbs: ["get", "list"]
            - apiGroups: [""]
              resources: ["pods", "events"]
              verbs: ["get", "list"]
            - apiGroups: ["batch"]
              resources: ["jobs"]
              verbs: ["get", "list"]
            - apiGroups: ["apiextensions.k8s.io"]
              resources: ["customresourcedefinitions"]
              verbs: ["get"]
        toolsets:
            kueue/core:
                enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Common Use Cases

```bash
holmes ask "Why is the Job gpu-train in namespace ml-team stuck Pending?"
```

```bash
holmes ask "Does the LocalQueue team-a-queue actually point at a valid ClusterQueue?"
```

```bash
holmes ask "Was the workload for my-training-job preempted, and by what?"
```

```bash
holmes ask "Why does my gang-scheduled pod-group have fewer running pods than expected?"
```
