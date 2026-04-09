# Kubernetes Toolsets

## Core

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
| kubernetes_jq_query | Query Kubernetes resources using jq filters with pagination |
| kubernetes_tabular_query | Extract specific fields from resources in tabular format with optional filtering |
| kubernetes_count | Count Kubernetes resources matching a jq filter |

## Logs

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
| kubectl_previous_logs | Fetch previous logs from a crashed pod |
| kubectl_previous_logs_all_containers | Fetch previous logs from all containers in a crashed pod |
| kubectl_container_previous_logs | Fetch previous logs from a specific container in a crashed pod |
| kubectl_container_logs | Fetch logs from a specific container in a pod |
| kubectl_logs_grep | Search for specific patterns in pod logs |
| kubectl_logs_all_containers_grep | Search for patterns in logs from all containers |

## Live Metrics

!!! note "Not Enabled by Default"
    This toolset is only available when `kubectl top` is supported (requires [Metrics Server](https://github.com/kubernetes-sigs/metrics-server)).

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

## Kube Prometheus Stack

!!! note "Not Enabled by Default"
    This toolset must be explicitly enabled.

This toolset uses `kubectl` to proxy into a Prometheus service running in-cluster and fetch target definitions. This is different from the [Prometheus toolset](prometheus.md), which connects directly to a Prometheus server for metrics querying.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/kube-prometheus-stack:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| get_prometheus_target | Fetch the definition of a Prometheus target via kubectl proxy |

## Resource Lineage

!!! note "Not Enabled by Default"
    This toolset must be explicitly enabled. Requires [kube-lineage](https://github.com/tohjustin/kube-lineage) installed either via `kubectl krew` or built from source.

Provides tools to fetch children/dependents and parents/dependencies of Kubernetes resources. Two variations are available depending on how kube-lineage is installed.

### Configuration

```yaml
holmes:
    toolsets:
        kubernetes/kube-lineage-extras:
            enabled: true
        # OR if installed via krew:
        kubernetes/krew-extras:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| kubectl_lineage_children | Get child/dependent resources of a Kubernetes resource |
| kubectl_lineage_parents | Get parent/dependency resources of a Kubernetes resource |

