# Notion

Notion Integration for HolmesGPT

Enabling this toolset allows HolmesGPT to fetch pages from Notion, making it useful when providing Notion-based runbooks.

## Setup Instructions

1. **Create a Webhook Integration**

    - Go to the Notion Developer Portal.
    - Create a new integration with **read content** capabilities.

2. **Grant Access to Pages**

    - Open the desired Notion page.
    - Click the three dots in the top right.
    - Select **Connections** and add your integration.

3. **Configure Authentication**

    - Retrieve the **Internal Integration Secret** from Notion.
    - Create a Kubernetes secret in your cluster with this key.
    - Configure the `NOTION_AUTH` environment variable.

## Configuration

=== "Holmes CLI"

    Set the environment variable:

    ```bash
    export NOTION_AUTH="<your Notion integration secret>"
    ```

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      notion:
        enabled: true
        config:
          additional_headers:
            Authorization: Bearer {{ env.NOTION_AUTH }}
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-notion \
      --from-literal=NOTION_AUTH="<your Notion integration secret>" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-notion

    toolsets:
      notion:
        enabled: true
        config:
          additional_headers:
            Authorization: Bearer {{ env.NOTION_AUTH }}
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-notion \
      --from-literal=NOTION_AUTH="<your Notion integration secret>" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-notion

      toolsets:
        notion:
          enabled: true
          config:
            additional_headers:
              Authorization: Bearer {{ env.NOTION_AUTH }}
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Timeout Configuration

By default, the Notion toolset uses a 5-second timeout for webpage requests. If you need to increase the timeout for slower Notion API responses, you can set the `INTERNET_TOOLSET_TIMEOUT_SECONDS` environment variable:

=== "Holmes CLI"

    ```bash
    export INTERNET_TOOLSET_TIMEOUT_SECONDS=30
    ```

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    additionalEnvVars:
      - name: INTERNET_TOOLSET_TIMEOUT_SECONDS
        value: "30"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: INTERNET_TOOLSET_TIMEOUT_SECONDS
          value: "30"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| fetch_notion_webpage | Fetch a Notion webpage. Use this to fetch Notion runbooks if they are present before starting your investigation |
