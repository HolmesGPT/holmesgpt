To disable the default logging toolset, add the following to your holmes configuration:

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
        kubernetes/logs:
            enabled: false
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        toolsets:
            kubernetes/logs:
                enabled: false # HolmesGPT's default logging mechanism MUST be disabled
    ```
