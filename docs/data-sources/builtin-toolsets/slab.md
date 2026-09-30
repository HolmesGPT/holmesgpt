# Slab

By enabling this toolset, HolmesGPT will be able to consult runbooks from Slab pages.

Retrieve your Slab [API token](https://help.slab.com/en/articles/6545629-developer-tools-api-webhooks) prior to configuring this toolset. Do note that Slab API is only available for Slab premium users. See [here](https://help.slab.com/en/articles/6545629-developer-tools-api-webhooks).

## Configuration

=== "Holmes CLI"

    Set the environment variable:

    ```bash
    export SLAB_API_KEY="<your Slab API key>"
    ```

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      slab:
        enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-slab \
      --from-literal=SLAB_API_KEY="<your Slab API key>" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-slab

    toolsets:
      slab:
        enabled: true
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-slab \
      --from-literal=SLAB_API_KEY="<your Slab API key>" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-slab

      toolsets:
        slab:
          enabled: true
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

To test, run:

```bash
holmes ask "Why is my pod failing, if it's a crashloopbackoff use the runbooks from Slab"
```

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| fetch_slab_document | Fetch a document from Slab. Use this to fetch runbooks if they are present before starting your investigation. |
