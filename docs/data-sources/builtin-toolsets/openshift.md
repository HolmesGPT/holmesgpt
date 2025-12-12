# OpenShift Toolsets

## Core ✓

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

By enabling this toolset, HolmesGPT will be able to describe and find OpenShift resources like projects, routes, deployment configs, build configs, and other OpenShift-specific resources using the `oc` command.

### Prerequisites

```bash
oc version --client
```

### Configuration

```yaml
holmes:
    toolsets:
        openshift/core:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| oc_describe | Run oc describe command on a specific resource |
| oc_get_by_name | Get details of a specific resource with labels |
| oc_get_by_kind_in_namespace | List all resources of a given type in a namespace |
| oc_get_by_kind_in_cluster | List all resources of a given type across the cluster |
| oc_find_resource | Search for resources matching a keyword |
| oc_get_yaml | Get YAML definition of a resource |
| oc_events | Get events for a specific resource |
| oc_projects | List all projects (namespaces) in the OpenShift cluster |
| oc_project_current | Show the current project (namespace) context |
| oc_routes | List all routes in a specific namespace or cluster-wide |
| oc_route_describe | Describe a specific route to see its configuration and status |
| oc_imagestreams | List image streams in a namespace or cluster-wide |
| oc_deploymentconfigs | List deployment configs in a namespace or cluster-wide |
| oc_buildconfigs | List build configs in a namespace or cluster-wide |
| oc_builds | List builds in a namespace or cluster-wide |
| oc_build_logs | Get logs from a specific build |
| oc_adm_openshift_audit_logs | Get OpenShift audit logs from a specified node |
| oc_adm_openshift_audit_logs_with_filter | Get OpenShift audit logs from a specified node with an applied filter |
| openshift_jq_query | Query OpenShift resources using jq filters |

## Logs ✓

!!! info "Enabled by Default"
    This toolset is enabled by default. You do not need to configure it.

By enabling this toolset, HolmesGPT will be able to read OpenShift pod logs using the `oc` command.

--8<-- "snippets/toolsets_that_provide_logging.md"

### Prerequisites

```bash
oc version --client
```

### Configuration

```yaml
holmes:
    toolsets:
        openshift/logs:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| oc_logs | Fetch logs from a specific pod |
| oc_logs_all_containers | Fetch logs from all containers in a pod |
| oc_previous_logs | Fetch previous logs from a pod |
| oc_previous_logs_all_containers | Fetch previous logs from all containers in a pod |
| oc_container_logs | Fetch logs from a specific container in a pod |
| oc_container_previous_logs | Fetch previous logs from a specific container in a pod |
| oc_logs_grep | Search for specific patterns in pod logs |
| oc_logs_all_containers_grep | Search for patterns in logs from all containers |

## Live Metrics

This toolset retrieves real-time CPU and memory usage for pods and nodes using OpenShift commands.

!!! warning "Snapshot Data Only"
    The oc top commands only show current snapshot data and cannot be used for generating graphs or historical analysis.

### Prerequisites

```bash
oc adm top nodes
```

### Configuration

```yaml
holmes:
    toolsets:
        openshift/live-metrics:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| oc_top_pods | Get current CPU and memory usage for pods |
| oc_top_nodes | Get current CPU and memory usage for nodes |

## Security

This toolset provides access to OpenShift security-related resources and configurations including Security Context Constraints (SCCs), role bindings, and service accounts.

### Prerequisites

```bash
oc version --client
```

### Configuration

```yaml
holmes:
    toolsets:
        openshift/security:
            enabled: true
```

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| oc_scc | List Security Context Constraints (SCCs) in the cluster |
| oc_scc_describe | Describe a specific Security Context Constraint |
| oc_policy_who_can | Check who can perform a specific action on a resource |
| oc_policy_can_i | Check if the current user can perform a specific action |
| oc_serviceaccounts | List service accounts in a namespace or cluster-wide |
| oc_rolebindings | List role bindings in a namespace or cluster-wide |
| oc_clusterrolebindings | List cluster role bindings |
