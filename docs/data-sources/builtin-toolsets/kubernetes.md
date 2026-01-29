# Kubernetes Toolsets

## Core ✓

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

By enabling this toolset, HolmesGPT will be able to describe and find Kubernetes resources like nodes, deployments, pods, etc.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/core:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_describe | Run kubectl describe command on a specific resource |
| kubectl_get_by_name | Get details of a specific resource with labels |
| kubectl_get_by_kind_in_namespace | List all resources of a given type in a namespace |
| kubectl_get_by_kind_in_cluster | List all resources of a given type across the cluster |
| kubectl_find_resources | Search for resources matching a keyword |
| kubectl_get_yaml | Get YAML definition of a resource |
| kubectl_events | Get events for a specific resource |
| kubectl_memory_requests_all_namespaces | Get memory requests for all pods across all namespaces in MiB |
| kubectl_memory_requests_namespace | Get memory requests for all pods in a specific namespace in MiB |
| kubernetes_jq_query | Query Kubernetes resources using jq filters |

## Logs ✓

!!! info "Enabled by Default"
    This toolset is enabled by default. You do not need to configure it.

By enabling this toolset, HolmesGPT will be able to read Kubernetes pod logs.

--8<-- "snippets/toolsets_that_provide_logging.md"

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/logs:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_logs | Fetch logs from a specific pod |
| kubectl_logs_all_containers | Fetch logs from all containers in a pod |
| kubectl_previous_logs | Fetch previous logs from a pod |
| kubectl_previous_logs_all_containers | Fetch previous logs from all containers in a pod |
| kubectl_container_logs | Fetch logs from a specific container in a pod |
| kubectl_logs_grep | Search for specific patterns in pod logs |
| kubectl_logs_all_containers_grep | Search for patterns in logs from all containers |

## Live Metrics

This toolset retrieves real-time CPU and memory usage for pods and nodes.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/live-metrics:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_top_pods | Get current CPU and memory usage for pods |
| kubectl_top_nodes | Get current CPU and memory usage for nodes |

## Prometheus Stack

This toolset fetches Prometheus target definitions. Requires specific cluster role rules.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/prometheus_stack:
            enabled: true
    customClusterRoleRules:
        - apiGroups: ["monitoring.coreos.com"]
          resources: ["servicemonitors", "podmonitors", "prometheusrules"]
          verbs: ["get", "list"]
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_get_prometheus_targets | Get Prometheus monitoring targets |
| kubectl_get_service_monitors | Get ServiceMonitor resources |
| kubectl_get_pod_monitors | Get PodMonitor resources |

## Resource Lineage Extras

Two variations of resource lineage toolsets: one native and one using kubectl krew. Provides tools to fetch children/dependents and parents/dependencies of Kubernetes resources.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/resource_lineage_extras:
            enabled: true
        # OR
        kubernetes/resource_lineage_extras_krew:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_lineage_children | Get child/dependent resources of a Kubernetes resource |
| kubectl_lineage_parents | Get parent/dependency resources of a Kubernetes resource |
