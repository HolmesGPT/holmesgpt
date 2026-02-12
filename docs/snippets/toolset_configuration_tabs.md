=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        TOOLSET_NAME:
            enabled: true
            config:
                CONFIGURATION_OPTIONS
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        toolsets:
            TOOLSET_NAME:
                enabled: true
                config:
                    CONFIGURATION_OPTIONS
    ```

    Update your Helm values and run a Helm upgrade:

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```
