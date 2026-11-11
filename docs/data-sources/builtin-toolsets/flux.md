# Flux CD

By enabling this toolset, HolmesGPT can find out why Flux sources, Kustomizations and HelmReleases are not reconciling, read their events and specs, and trace a Kubernetes object back to the Flux object and Git revision that deployed it.

## Prerequisites

The toolset runs read-only commands of the [Flux CLI](https://fluxcd.io/flux/installation/#install-the-flux-cli) against your current kubeconfig context. The HolmesGPT container image does not include the Flux CLI, so the toolset is available in the Holmes CLI.

```bash
# Install the Flux CLI
brew install fluxcd/tap/flux

# Verify installation
flux version --client
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        flux/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Which Flux objects are not ready and why?"
    ```

## Common Use Cases

```bash
holmes ask "Which Flux Kustomizations and HelmReleases are failing and why?"
```

```bash
holmes ask "Why hasn't my last commit been deployed to the production namespace?"
```

```bash
holmes ask "Which Flux object deploys the checkout deployment in the shop namespace, and which Git revision is it running?"
```
