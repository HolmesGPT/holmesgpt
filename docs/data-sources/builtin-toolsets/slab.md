# Slab

By enabling this toolset, HolmesGPT will be able to consult runbooks from Slab pages.

Retrieve your Slab [API token](https://help.slab.com/en/articles/6545629-developer-tools-api-webhooks) prior to configuring this toolset. Do note that Slab API is only available for Slab premium users. See [here](https://help.slab.com/en/articles/6545629-developer-tools-api-webhooks).

## Configuration

=== "Holmes CLI"

    First, set the environment variable:
    ```bash
    export SLAB_API_KEY="<your Slab API key>"
    ```

    Then add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:
    ```yaml
    toolsets:
        slab:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        additionalEnvVars:
            - name: SLAB_API_KEY
              value: "<your Slab API key>"
        toolsets:
            slab:
                enabled: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

To test, run:

```bash
holmes ask "Why is my pod failing, if it's a crashloopbackoff use the runbooks from Slab"
```

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| fetch_slab_document | Fetch a document from Slab. Use this to fetch runbooks if they are present before starting your investigation. |
