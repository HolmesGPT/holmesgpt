# FluxCD

By enabling this toolset, HolmesGPT will be able to inspect the reconciliation status of FluxCD GitOps resources (GitRepository, HelmRepository, OCIRepository, Kustomization, HelmRelease, and more) and their dependency chains, to help diagnose why a GitOps-managed deployment is failing or stuck.

## Prerequisites

This toolset shells out to the `flux` CLI, so it must be present on the machine running HolmesGPT and able to reach your cluster:

- The `flux` binary must be on `PATH` (see the [Flux installation docs](https://fluxcd.io/flux/installation/))
- A working kubeconfig (or, when deployed in-cluster, a ServiceAccount) with read access to the Flux CRDs, in the `flux-system` namespace or wherever your Flux controllers are installed

No additional auth token or server configuration is required - unlike ArgoCD, Flux resources are read directly through the Kubernetes API using the same credentials as the `kubernetes/core` toolset.

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
    holmes ask "Why is my flux kustomization not becoming Ready?"
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

    A Kustomization or HelmRelease failing to reconcile is very often caused by its source (GitRepository/HelmRepository/OCIRepository) failing first. HolmesGPT is instructed to check the status of all Flux resources before narrowing in on a specific one, so it can find the actual root cause rather than just the symptom.

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| flux_get | Get the reconciliation status of Flux resources (sources, kustomizations, helmreleases, images, alerts, receivers, or all) |
| flux_tree | Show the tree of Kubernetes resources reconciled by a Flux Kustomization, to see what it manages or find dependency chains |
