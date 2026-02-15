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

## Capabilities

Provides eBPF-based node-level observability via Inspektor Gadget, including process snapshots, socket inspection, execution tracing, and network packet capture.
