## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        TOOLSET_PATH:
            enabled: true
            config:
                # Add your configuration here
                CUSTOM_CONFIG
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        toolsets:
            TOOLSET_PATH:
                enabled: true
                config:
                    # Add your configuration here
                    CUSTOM_CONFIG
    ```
