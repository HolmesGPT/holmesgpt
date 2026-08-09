# Atlassian Rovo (MCP)

The [Atlassian Rovo MCP Server](https://support.atlassian.com/atlassian-rovo-mcp-server/) is hosted by Atlassian and gives Holmes access to Jira and Confluence Cloud. It lets Holmes search Jira issues for related incidents, read and comment on tickets, and pull runbooks out of Confluence during an investigation.

Because Atlassian hosts the server, there is nothing to deploy in your cluster — Holmes connects directly to `https://mcp.atlassian.com/v1/mcp`.

!!! note "Two ways to authenticate"
    This page covers **API token** authentication, which is the right choice for Holmes running in Kubernetes or any other non-interactive environment: the credential is static and no browser login is involved.

    If you want each user to authenticate with their own Atlassian account through a browser consent screen, use the OAuth 2.1 flow described in [OAuth MCP Servers](../oauth-mcp-servers.md) instead.

## Prerequisites

### 1. Your admin must enable API token authentication

An Atlassian organization admin has to turn this on before any token will work:

1. Go to [admin.atlassian.com](https://admin.atlassian.com/) and select your organization
2. Navigate to **Rovo** → **Rovo MCP Server** → **Authentication**
3. Enable authentication via API token

### 2. Create a scoped API token

!!! warning "Classic API tokens do not work"
    The Rovo MCP Server requires an **API token with scopes**. A classic (unscoped) token authenticates successfully but exposes almost no tools — Jira and Confluence tools are granted per scope, so a token with no scopes gets none of them. See [Troubleshooting](#troubleshooting) for how this failure looks.

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token with scopes** — *not* **Create API token**
3. Give it a label (e.g. "HolmesGPT") and an expiry
4. Select the apps and scopes you need (see the table below)
5. **Copy the token immediately** — it won't be shown again

Tools are granted per scope, so only pick the ones you actually want Holmes to have:

| Scope | Unlocks |
|-------|---------|
| `read:jira-work` | Reading issues, projects, transitions, and issue metadata |
| `search:jira-work` | JQL search |
| `write:jira-work` | Creating and editing issues, comments, worklogs, transitions |
| `read:page:confluence`, `read:space:confluence`, `read:hierarchical-content:confluence`, `read:comment:confluence` | Reading Confluence pages, spaces, and comments |
| `search:confluence` | CQL search |
| `write:page:confluence` | Creating and updating Confluence pages and comments |

!!! tip "Read-only is a good default"
    For investigations, the read and search scopes are enough. Only add the `write:` scopes if you want Holmes to open tickets or post comments.

### 3. Build the Basic auth header value

The Rovo MCP Server expects HTTP Basic authentication with your Atlassian account email and the API token:

```bash
printf '%s:%s' "<YOUR_ATLASSIAN_EMAIL>" "<YOUR_API_TOKEN>" | base64 -w0
```

Keep the resulting base64 string — it becomes the `Authorization: Basic <value>` header below.

!!! note "Service accounts"
    If your organization uses an Atlassian service account instead of a personal account, skip the base64 step and send the API key directly as `Authorization: Bearer <YOUR_API_KEY>`.

## Configuration

=== "Holmes CLI"

    Export the base64 credential, then point Holmes at Atlassian's server:

    ```bash
    export ATLASSIAN_MCP_BASIC=$(printf '%s:%s' "<YOUR_ATLASSIAN_EMAIL>" "<YOUR_API_TOKEN>" | base64 -w0)
    ```

    Add the MCP server to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      atlassian-rovo:
        description: "Atlassian Jira and Confluence via the Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_BASIC }}"
          icon_url: "https://cdn.simpleicons.org/jira/0052CC"
        llm_instructions: |
          Use the Atlassian Rovo MCP server to search Jira for tickets describing the same
          symptoms before concluding an investigation, and to look up runbooks in Confluence.
          Always pass the cloudId of the target site.
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a secret with the base64 credential:

    ```bash
    kubectl create secret generic atlassian-mcp-credentials \
      --from-literal=basic-auth="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_API_TOKEN>' | base64 -w0)" \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    additionalEnvVars:
      - name: ATLASSIAN_MCP_BASIC
        valueFrom:
          secretKeyRef:
            name: atlassian-mcp-credentials
            key: basic-auth

    mcp_servers:
      atlassian-rovo:
        description: "Atlassian Jira and Confluence via the Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_BASIC }}"
          icon_url: "https://cdn.simpleicons.org/jira/0052CC"
        llm_instructions: |
          Use the Atlassian Rovo MCP server to search Jira for tickets describing the same
          symptoms before concluding an investigation, and to look up runbooks in Confluence.
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a secret with the base64 credential:

    ```bash
    kubectl create secret generic atlassian-mcp-credentials \
      --from-literal=basic-auth="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_API_TOKEN>' | base64 -w0)" \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: ATLASSIAN_MCP_BASIC
          valueFrom:
            secretKeyRef:
              name: atlassian-mcp-credentials
              key: basic-auth

      mcp_servers:
        atlassian-rovo:
          description: "Atlassian Jira and Confluence via the Rovo MCP server"
          config:
            mode: streamable-http
            url: https://mcp.atlassian.com/v1/mcp
            headers:
              Authorization: "Basic {{ env.ATLASSIAN_MCP_BASIC }}"
            icon_url: "https://cdn.simpleicons.org/jira/0052CC"
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

`{{ env.ATLASSIAN_MCP_BASIC }}` is resolved when Holmes loads its configuration, so the token itself never has to appear in your values file or config file.

## Available Tools

The tools Atlassian exposes depend on the scopes attached to your token.

| Tool | Description | Scope |
|------|-------------|-------|
| `getJiraIssue` | Get a Jira issue by key or ID | `read:jira-work` |
| `getVisibleJiraProjects` | List projects the token can see | `read:jira-work` |
| `getTransitionsForJiraIssue` | List available workflow transitions | `read:jira-work` |
| `getJiraIssueRemoteIssueLinks` | List remote links on an issue | `read:jira-work` |
| `getJiraProjectIssueTypesMetadata` | List issue types for a project | `read:jira-work` |
| `lookupJiraAccountId` | Resolve a user to an account ID | `read:jira-work` |
| `searchJiraIssuesUsingJql` | Search issues with JQL | `search:jira-work` |
| `createJiraIssue` | Create an issue | `write:jira-work` |
| `editJiraIssue` | Edit an existing issue | `write:jira-work` |
| `addCommentToJiraIssue` | Comment on an issue | `write:jira-work` |
| `transitionJiraIssue` | Move an issue through its workflow | `write:jira-work` |
| `getConfluencePage` | Get a Confluence page and its body | `read:page:confluence` |
| `getPagesInConfluenceSpace` | List pages in a space | `read:page:confluence` |
| `getConfluenceSpaces` | List spaces | `read:space:confluence` |
| `getConfluencePageFooterComments` | Read page comments | `read:comment:confluence` |
| `searchConfluenceUsingCql` | Search Confluence with CQL | `search:confluence` |
| `createConfluencePage` | Create a page | `write:page:confluence` |
| `updateConfluencePage` | Update a page | `write:page:confluence` |

See Atlassian's [supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/) reference for the complete list.

!!! note "Most tools need a cloudId"
    Rovo tools are site-scoped and take a `cloudId` argument. You can find yours at `https://<your-site>.atlassian.net/_edge/tenant_info`. Holmes will usually discover it via `getVisibleJiraProjects`, but putting it in `llm_instructions` saves a round trip.

## Testing the Connection

```bash
holmes toolset list
```

`atlassian-rovo` should show as `enabled`. Then check that the tools actually work:

```bash
holmes ask "List the Jira projects I have access to"
```

## Common Use Cases

```bash
holmes ask "Search Jira for open issues mentioning the checkout-api pod crashing"
```

```bash
holmes ask "Find the Confluence runbook for database failover and summarize the steps"
```

```bash
holmes ask "Open a Jira ticket in PROJ describing the OOMKills on the payments deployment"
```

## Troubleshooting

**Only three `TeamworkGraph` tools appear, and no Jira or Confluence tools**

Your token is a classic API token without scopes. Holmes will show the toolset as `enabled` — Atlassian accepts the credential and serves its default tool set — but none of the Jira or Confluence tools are granted. Create a new token with **Create API token with scopes** and pick the scopes from the table above.

**`403 Forbidden ... requires a modern API token (API token with scopes). Legacy API tokens without scopes are not supported.`**

Same cause as above. The credential is valid, the token type is not.

**`401 Unauthorized`**

The email/token pair is wrong, the base64 encoding is malformed, or the token has been revoked. Re-run the `printf ... | base64 -w0` command and make sure there is no trailing newline (`-w0` on GNU coreutils; on macOS use `base64` with no flags).

**The toolset fails to load entirely**

Confirm the org-level setting is on (**Atlassian Administration** → **Rovo** → **Rovo MCP Server** → **Authentication**) and that outbound traffic to `mcp.atlassian.com` is allowed from wherever Holmes runs.

**A tool returns "not found"**

That tool's scope isn't on your token. Tools are filtered by scope, so the server reports them as missing rather than as permission errors.

## Additional Resources

- [Atlassian Rovo MCP Server documentation](https://support.atlassian.com/atlassian-rovo-mcp-server/)
- [Configuring authentication via API token](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/)
- [Supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/)
- [Manage API tokens for your Atlassian account](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
- [OAuth MCP Servers in Holmes](../oauth-mcp-servers.md)
