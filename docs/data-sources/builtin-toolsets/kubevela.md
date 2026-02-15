# KubeVela

This toolset provides access to KubeVela CLI commands for managing and troubleshooting applications built on the Open Application Model (OAM).

## Prerequisites

The KubeVela CLI (`vela`) must be installed and configured to access your cluster.

**Installation:**

```bash
# Install vela CLI
curl -fsSl https://kubevela.io/script/install.sh | bash

# Verify installation
vela version
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    <!-- markdownlint-disable-next-line MD046 -->
    ```yaml
    toolsets:
        kubevela/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "What is the status of my KubeVela applications?"
    ```

## Common Use Cases

```bash
holmes ask "What KubeVela applications are unhealthy and why?"
```

```bash
holmes ask "Show me the workflow status for my payment-service application"
```

```bash
holmes ask "What components does my frontend application have and are they running correctly?"
```

```bash
holmes ask "Check if there are any trait configuration issues in the user-api application"
```
