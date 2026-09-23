# OAuth MCP Servers

!!! note
    OAuth MCP server support is available starting in Holmes 0.25.0.

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


## Example: Atlassian

!!! tip "Running Holmes headlessly?"
    OAuth requires a browser consent screen. To connect the same Atlassian Rovo MCP server with a static credential instead, see [Atlassian Rovo (MCP)](builtin-toolsets/atlassian-rovo-mcp.md).

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


## How It Works

1. Holmes detects that the MCP server has `oauth.enabled: true`
2. Holmes discovers the server's OAuth configuration automatically via the MCP protocol
3. The user is prompted to authenticate via their browser
4. After login, Holmes exchanges the authorization code for an access token
5. The token is persisted and refreshed automatically — users only need to authenticate once
