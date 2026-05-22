# Inspektor Gadget

By enabling this toolset, HolmesGPT will be able to use [Inspektor Gadget](https://inspektor-gadget.io/) eBPF-based observability tools for deep Kubernetes node-level troubleshooting.

## Prerequisites

1. Kubernetes cluster with `kubectl` configured
2. Node access permissions for `kubectl debug --profile=sysadmin`
3. Set the `ENABLE_INSPEKTOR_GADGET` environment variable
4. For tcpdump toolset: `tcpdump` CLI installed locally

## Configuration

=== "Holmes CLI"

    First, verify your environment is configured:

    ```bash
    # Verify kubectl is accessible
    kubectl version --client

    # Set the environment variable to enable Inspektor Gadget
    export ENABLE_INSPEKTOR_GADGET=true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    customClusterRoleRules:
      - apiGroups: [""]
        resources: ["pods", "pods/attach"]
        verbs: ["create"]
    additionalEnvVars:
      - name: ENABLE_INSPEKTOR_GADGET
        value: "true"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| ig_node_snapshot_process | Snapshot of running processes on a node |
| ig_node_snapshot_socket | Snapshot of open sockets on a node |
| ig_node_trace_exec | Trace process execution events on a node |
| ig_node_traceloop | Capture system calls in real-time, acting as a flight recorder for applications |
| ig_node_trace_open | Trace file open events on a node |
| ig_node_trace_tcp | Trace TCP connection events on a node |
| ig_node_trace_dns | Trace DNS query events on a node |
| ig_node_tcpdump | Capture network packets on a node using tcpdump expressions |
