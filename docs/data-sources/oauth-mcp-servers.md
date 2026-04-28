# OAuth MCP Servers

!!! warning "Alpha Feature"
    OAuth MCP server support is a new feature and will experience rapid improvements. Configuration and behavior may change between releases.

Some MCP servers support OAuth-based authentication natively — you only need to set `oauth.enabled: true` and Holmes handles the rest. When Holmes connects to an OAuth-enabled MCP server, it automatically discovers the server's OAuth endpoints, opens a browser for login, and persists the token for future use.

## Setup

To add an OAuth MCP server, set `mode: streamable-http` and `oauth.enabled: true` in the server's config:

=== "Robusta CLI"

    Set the `CUSTOM_TOOLSET_LOCATION` environment variable pointing to a YAML file with your MCP server configuration:

    ```bash
    export CUSTOM_TOOLSET_LOCATION=/Users/.../custom_toolset.yaml
    ```

    In that file, define your OAuth MCP servers:

    ```yaml
    toolsets:
      # ... your toolsets

    mcp_servers:
      my-server:
        description: "Description of the MCP server"
        config:
          mode: streamable-http
          url: https://example.com/mcp
          oauth:
            enabled: true
    ```

=== "Robusta Helm Chart with Platform"

    Add the MCP servers to your `generated_values.yaml`. Make sure `enableHolmesGPT` is set to `true` and SaaS is enabled:

    ```yaml
    holmes:
      mcp_servers:
        my-server:
          description: "Description of the MCP server"
          config:
            mode: streamable-http
            url: https://example.com/mcp
            oauth:
              enabled: true
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Example: Atlassian

=== "Robusta CLI"

    ```yaml
    mcp_servers:
      atlassian:
        description: "Atlassian Jira + Confluence MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          oauth:
            enabled: true
    ```

=== "Robusta Helm Chart with Platform"

    **Before configuring Holmes, set up the Atlassian side:**

    1. Go to [https://admin.atlassian.com/](https://admin.atlassian.com/) and select your organization
    2. Navigate to **Rovo** → **Rovo MCP Server**
    3. Click **Add domain** and enter your Robusta platform URL, matching your region:

        | Region | URL |
        |--------|-----|
        | US (default) | `https://platform.robusta.dev/**` |
        | EU | `https://eu.platform.robusta.dev/**` |
        | AP | `https://ap.platform.robusta.dev/**` |

    **Update your values and helm install or upgrade:**

    ```yaml
    holmes:
      mcp_servers:
        atlassian:
          config:
            mode: streamable-http
            url: https://mcp.atlassian.com/v1/mcp
            oauth:
              enabled: true
    ```

## How It Works

1. Holmes detects that the MCP server has `oauth.enabled: true`
2. Holmes discovers the server's OAuth configuration automatically via the MCP protocol
3. The user is prompted to authenticate via their browser
4. After login, Holmes exchanges the authorization code for an access token
5. The token is persisted and refreshed automatically — users only need to authenticate once
