# Cilium

By enabling this toolset, HolmesGPT will be able to interact with Cilium CNI and Hubble network observability, providing comprehensive network troubleshooting and monitoring capabilities for Kubernetes clusters.

## Prerequisites

1. Cilium CNI installed in your Kubernetes cluster
2. Hubble network observability enabled (for Hubble tools)
3. `cilium` CLI tool installed and configured
4. `hubble` CLI tool installed and configured (for Hubble tools)
5. Appropriate RBAC permissions for Cilium resources

## Configuration

=== "Holmes CLI"

    First, ensure your tools are properly configured:

    ```bash
    # Verify Cilium is accessible
    cilium version
    cilium status

    # Verify Hubble is accessible (if using Hubble tools)
    hubble version
    hubble status
    ```

    Then add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      cilium/core:
        enabled: true
      hubble/observability:
        enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        cilium/core:
          enabled: true
        hubble/observability:
          enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Advanced Configuration

You can configure additional settings for enhanced functionality:

```yaml
toolsets:
  cilium/core:
    enabled: true
    config:
      timeout: 60  # Command timeout in seconds
  hubble/observability:
    enabled: true
    config:
      max_flows: 10000  # Maximum flows to observe
      timeout: 120      # Extended timeout for flow monitoring
```

## Capabilities

### Cilium/Core Toolset

| Tool Name | Description |
|-----------|-------------|
| cilium_status | Display overall Cilium agent status and health |
| cilium_status_verbose | Display detailed Cilium agent status with verbose output |
| cilium_version | Show Cilium version information |
| cilium_config | Display current Cilium configuration |
| cilium_endpoint_list | List all Cilium endpoints (pods with networking) |
| cilium_endpoint_get | Get detailed information about a specific endpoint by ID |
| cilium_endpoint_health | Check health status of all endpoints |
| cilium_endpoint_logs | Show logs for a specific endpoint |
| cilium_service_list | List all Cilium services and load balancer mappings |
| cilium_service_get | Get detailed information about a specific service by ID |
| cilium_loadbalancer_list | List load balancer backend mappings |
| cilium_policy_get | Get network policies applied to an endpoint |
| cilium_policy_trace | Trace policy decisions for traffic between source and destination |
| cilium_policy_trace_verbose | Detailed policy trace with verbose output |
| cilium_monitor | Monitor Cilium datapath events in real-time |
| cilium_monitor_verbose | Monitor datapath events with verbose output |
| cilium_node_list | List all nodes in the Cilium cluster |
| cilium_clustermesh_status | Display cluster mesh status for multi-cluster networking |
| cilium_bpf_map_list | List all BPF maps used by Cilium |
| cilium_bpf_endpoint_list | List endpoints from BPF maps |
| cilium_debuginfo | Generate debug information bundle for troubleshooting |

### Hubble/Observability Toolset

| Tool Name | Description |
|-----------|-------------|
| hubble_observe | Observe network flows in real-time (last 1000 flows) |
| hubble_observe_follow | Follow network flows in real-time as they happen |
| hubble_observe_namespace | Observe flows for a specific namespace |
| hubble_observe_pod | Observe flows to/from a specific pod |
| hubble_observe_since | Observe flows since a specific time |
| hubble_observe_http | Observe HTTP traffic flows |
| hubble_observe_dns | Observe DNS queries and responses |
| hubble_observe_kafka | Observe Kafka protocol traffic |
| hubble_observe_grpc | Observe gRPC traffic flows |
| hubble_observe_drops | Show only dropped network flows (policy denials, etc.) |
| hubble_observe_denied | Show flows denied by network policies |
| hubble_observe_service | Observe flows to/from a specific service |
| hubble_observe_port | Observe flows on a specific port |
| hubble_observe_from_pod | Observe flows originating from a specific pod |
| hubble_observe_to_pod | Observe flows destined to a specific pod |
| hubble_observe_between_namespaces | Observe flows between two specific namespaces |
| hubble_observe_json | Output flow observations in JSON format for detailed analysis |
| hubble_status | Display Hubble server status and configuration |
| hubble_list_nodes | List nodes available for flow observation |
| hubble_observe_flows_summary | Get a summary of recent network flows with basic statistics |
| hubble_observe_security_events | Observe security-related network events and policy violations |
| hubble_observe_l7_denied | Show L7 (application-layer) traffic that was denied |

## Use Cases

### Network Connectivity Troubleshooting
- **Pod-to-Pod Communication**: Use `cilium_endpoint_list` and `hubble_observe_between_namespaces` to diagnose connectivity issues
- **Service Resolution**: Check `cilium_service_list` and observe DNS flows with `hubble_observe_dns`
- **Load Balancing Issues**: Analyze `cilium_loadbalancer_list` and service traffic patterns

### Network Policy Debugging
- **Policy Violations**: Use `hubble_observe_denied` to see blocked traffic
- **Policy Tracing**: Use `cilium_policy_trace` to understand policy decisions
- **Security Events**: Monitor with `hubble_observe_security_events`

### Performance Analysis
- **Network Monitoring**: Real-time observation with `cilium_monitor` and `hubble_observe_follow`
- **Flow Analysis**: Detailed traffic patterns with `hubble_observe_json`
- **Component Health**: Status checking with `cilium_status_verbose`

### Multi-Cluster Networking
- **Cluster Mesh**: Status and connectivity with `cilium_clustermesh_status`
- **Cross-Cluster Flows**: Monitor inter-cluster traffic patterns

## Common Troubleshooting Scenarios

### "My pods can't communicate"

1. Check Cilium agent health: `cilium_status`
2. Verify endpoints are healthy: `cilium_endpoint_health`
3. Observe blocked flows: `hubble_observe_drops`
4. Trace policy decisions: `cilium_policy_trace`

### "DNS resolution is failing"

1. Monitor DNS queries: `hubble_observe_dns`
2. Check endpoint configuration: `cilium_endpoint_get`
3. Verify service mappings: `cilium_service_list`

### "Network policies aren't working"

1. View denied flows: `hubble_observe_denied`
2. Trace policy evaluation: `cilium_policy_trace_verbose`
3. Check applied policies: `cilium_policy_get`

### "Load balancing issues"

1. Examine service backends: `cilium_service_get`
2. Check BPF load balancer state: `cilium_loadbalancer_list`
3. Monitor service traffic: `hubble_observe_service`
