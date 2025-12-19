# Flux CD

By enabling this toolset, HolmesGPT will be able to check sync status, troubleshoot reconciliation issues, and inspect Flux-managed resources in your Kubernetes cluster.

## Prerequisites

This toolset requires the [Flux CLI](https://fluxcd.io/flux/installation/#install-the-flux-cli) to be installed and configured with access to your Kubernetes cluster.

### Installing the Flux CLI

**macOS (Homebrew):**

```bash
brew install fluxcd/tap/flux
```

**Linux:**

```bash
curl -s https://fluxcd.io/install.sh | sudo bash
```

**Windows (Chocolatey):**

```bash
choco install flux
```

### Verifying Installation

Verify that Flux CLI is installed and can communicate with your cluster:

```bash
flux check --pre
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
        flux/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "What is the sync status of my Flux resources?"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        toolsets:
            flux/core:
                enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

!!! note

    The Flux CLI uses your kubeconfig to access the cluster. Ensure that HolmesGPT has the same Kubernetes access as your Flux CLI.

!!! info "Flux Version Compatibility"

    Most tools work with Flux v2.0+. The `flux_debug_kustomization` and `flux_debug_helmrelease` tools require Flux CLI v2.4 or later.

## Example Questions

Here are some example questions you can ask HolmesGPT with the Flux toolset enabled:

- "What is the sync status of all my Flux resources?"
- "Why is my application out of sync?"
- "Which GitRepositories are failing to reconcile?"
- "Why is my HelmRelease not deploying?"
- "Show me the Flux events for the past hour"
- "What's blocking my Kustomization from applying?"

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| flux_check | Check Flux installation health and prerequisites |
| flux_version | Show Flux CLI and controller versions |
| flux_stats | Display statistics about Flux resources |
| flux_get_all | Get status of all Flux resources across all namespaces |
| flux_get_sources_all | Get status of all source types (Git, Helm, Bucket, OCI) |
| flux_get_sources_git | Get status of GitRepository sources |
| flux_get_sources_helm | Get status of HelmRepository sources |
| flux_get_sources_oci | Get status of OCIRepository sources |
| flux_get_sources_bucket | Get status of Bucket sources |
| flux_get_kustomizations | Get status of Kustomizations |
| flux_get_helmreleases | Get status of HelmReleases |
| flux_get_alerts | Get status of Alert resources |
| flux_get_alert_providers | Get status of Alert Provider resources |
| flux_get_receivers | Get status of Receiver resources (webhooks) |
| flux_get_images_all | Get status of all image automation resources |
| flux_logs | View logs from Flux controllers |
| flux_events | Display Kubernetes events for Flux resources |
| flux_trace | Trace a Flux resource to show its dependency chain |
| flux_tree | Show the tree view of a Kustomization |
| flux_debug_kustomization | Debug a Kustomization with detailed state information (requires Flux v2.4+) |
| flux_debug_helmrelease | Debug a HelmRelease with detailed state information (requires Flux v2.4+) |
| flux_diff_kustomization | Show diff between cluster state and desired state |
| flux_export_source_git | Export a GitRepository definition in YAML |
| flux_export_kustomization | Export a Kustomization definition in YAML |
| flux_export_helmrelease | Export a HelmRelease definition in YAML |

## Troubleshooting

### Common Issues

**Flux CLI not found:**

Ensure the Flux CLI is installed and available in your PATH. Run `flux version --client` to verify.

**Permission denied:**

The Flux CLI requires Kubernetes RBAC permissions to query Flux resources. Ensure your kubeconfig has read access to Flux CRDs in the `flux-system` namespace (or your custom Flux namespace).

**Flux not installed in cluster:**

If `flux check` shows that Flux controllers are not installed, you'll need to [bootstrap Flux](https://fluxcd.io/flux/installation/#bootstrap) in your cluster first.
