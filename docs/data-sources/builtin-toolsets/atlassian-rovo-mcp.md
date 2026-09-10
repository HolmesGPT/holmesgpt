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

!!! warning "A classic API token authenticates but grants no tools"
    The Rovo MCP Server requires an **API token with scopes**. This failure is easy to misread as success: a classic (unscoped) token authenticates fine, the MCP session initializes, and `holmes toolset list` reports the toolset as `enabled`. But Jira and Confluence tools are granted per scope, so a token with no scopes gets none of them — you are left with three `TeamworkGraph` tools and every Jira or Confluence tool returning `not found`.

    A classic token also still works against the Jira and Confluence REST APIs, so testing it with `curl` outside of Rovo will succeed and tell you nothing about whether Rovo will serve the tools. Check the tool list, not the connection status. See [Troubleshooting](#troubleshooting).

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token with scopes** — *not* **Create API token**
3. Give it a label (e.g. "HolmesGPT") and an expiry (1–365 days; scoped tokens cannot be non-expiring)
4. Select the app — **Jira** or **Confluence**
5. Select the scopes you need (see the table below)
6. **Copy the token immediately** — it won't be shown again

!!! warning "One token covers one app"
    A scoped token targets a single app, so a Jira token grants no Confluence tools and vice versa. To give Holmes both, create two tokens and register two `mcp_servers` entries pointing at the same URL — see [Configuration](#configuration). Scopes also cannot be edited after creation; changing them means issuing a new token.

Tools are granted per scope, so only pick the ones you actually want Holmes to have:

| App | Scope | Unlocks |
|-----|-------|---------|
| Jira | `read:jira-work` | Reading issues, projects, transitions, and issue metadata |
| Jira | `search:jira-work` | JQL search |
| Jira | `write:jira-work` | Creating and editing issues, comments, worklogs, transitions |
| Confluence | `read:page:confluence` | Reading page bodies and listing pages in a space |
| Confluence | `read:space:confluence` | Listing spaces |
| Confluence | `read:comment:confluence` | Reading page comments |
| Confluence | `read:hierarchical-content:confluence` | Walking page descendants |
| Confluence | `search:confluence` | CQL search |
| Confluence | `write:page:confluence` | Creating and updating pages and comments |

!!! danger "Confluence: pick the granular scopes, not the classic ones"
    The token creation screen offers Confluence scopes in two styles, and **Rovo only accepts the granular ones** — the `<action>:<resource>:confluence` names in the table above.

    Selecting the classic `read:confluence-content.all`-style scopes produces a token that looks fine but grants no Confluence tools in Rovo. It still authenticates, and it still works against the legacy `/wiki/rest/api/content` REST endpoint, which makes it easy to conclude the token is good. Jira is not affected — its scopes (`read:jira-work` and friends) are the classic-style names and are what Rovo expects.

!!! tip "Read-only is a good default"
    For investigations, the read and search scopes are enough. Only add the `write:` scopes if you want Holmes to open tickets or post comments.

### 3. Build the Basic auth header value

The Rovo MCP Server expects HTTP Basic authentication with your Atlassian account email and the API token:

```bash
printf '%s:%s' "<YOUR_ATLASSIAN_EMAIL>" "<YOUR_API_TOKEN>" | base64 | tr -d '\n'
```

Keep the resulting base64 string — it becomes the `Authorization: Basic <value>` header below. Run this once per token if you created both a Jira and a Confluence token.

!!! note "Service accounts"
    If your organization uses an Atlassian service account instead of a personal account, skip the base64 step and send the API key directly as `Authorization: Bearer <YOUR_API_KEY>`.

## Configuration

Because a scoped token covers one app, register one `mcp_servers` entry per token. Both point at the same URL and differ only in the credential. If you only need Jira, drop the Confluence entry.

=== "Holmes CLI"

    Export one base64 credential per token:

    ```bash
    export ATLASSIAN_MCP_JIRA=$(printf '%s:%s' "<YOUR_ATLASSIAN_EMAIL>" "<YOUR_JIRA_TOKEN>" | base64 | tr -d '\n')
    export ATLASSIAN_MCP_CONFLUENCE=$(printf '%s:%s' "<YOUR_ATLASSIAN_EMAIL>" "<YOUR_CONFLUENCE_TOKEN>" | base64 | tr -d '\n')
    ```

    Add the MCP servers to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      atlassian-jira:
        description: "Jira issues via the Atlassian Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_JIRA }}"
          icon_url: "https://cdn.simpleicons.org/jira/0052CC"
        llm_instructions: |
          Use this to search Jira for tickets describing the same symptoms before
          concluding an investigation. Always pass the cloudId of the target site.

      atlassian-confluence:
        description: "Confluence pages via the Atlassian Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_CONFLUENCE }}"
          icon_url: "https://cdn.simpleicons.org/confluence/172B4D"
        llm_instructions: |
          Use this to look up runbooks and architecture docs in Confluence.
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a secret holding one base64 credential per token:

    ```bash
    kubectl create secret generic atlassian-mcp-credentials \
      --from-literal=jira="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_JIRA_TOKEN>' | base64 | tr -d '\n')" \
      --from-literal=confluence="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_CONFLUENCE_TOKEN>' | base64 | tr -d '\n')" \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    additionalEnvVars:
      - name: ATLASSIAN_MCP_JIRA
        valueFrom:
          secretKeyRef:
            name: atlassian-mcp-credentials
            key: jira
      - name: ATLASSIAN_MCP_CONFLUENCE
        valueFrom:
          secretKeyRef:
            name: atlassian-mcp-credentials
            key: confluence

    mcp_servers:
      atlassian-jira:
        description: "Jira issues via the Atlassian Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_JIRA }}"
          icon_url: "https://cdn.simpleicons.org/jira/0052CC"
        llm_instructions: |
          Use this to search Jira for tickets describing the same symptoms before
          concluding an investigation.

      atlassian-confluence:
        description: "Confluence pages via the Atlassian Rovo MCP server"
        config:
          mode: streamable-http
          url: https://mcp.atlassian.com/v1/mcp
          headers:
            Authorization: "Basic {{ env.ATLASSIAN_MCP_CONFLUENCE }}"
          icon_url: "https://cdn.simpleicons.org/confluence/172B4D"
        llm_instructions: |
          Use this to look up runbooks and architecture docs in Confluence.
    ```

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a secret holding one base64 credential per token:

    ```bash
    kubectl create secret generic atlassian-mcp-credentials \
      --from-literal=jira="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_JIRA_TOKEN>' | base64 | tr -d '\n')" \
      --from-literal=confluence="$(printf '%s:%s' '<YOUR_ATLASSIAN_EMAIL>' '<YOUR_CONFLUENCE_TOKEN>' | base64 | tr -d '\n')" \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: ATLASSIAN_MCP_JIRA
          valueFrom:
            secretKeyRef:
              name: atlassian-mcp-credentials
              key: jira
        - name: ATLASSIAN_MCP_CONFLUENCE
          valueFrom:
            secretKeyRef:
              name: atlassian-mcp-credentials
              key: confluence

      mcp_servers:
        atlassian-jira:
          description: "Jira issues via the Atlassian Rovo MCP server"
          config:
            mode: streamable-http
            url: https://mcp.atlassian.com/v1/mcp
            headers:
              Authorization: "Basic {{ env.ATLASSIAN_MCP_JIRA }}"
            icon_url: "https://cdn.simpleicons.org/jira/0052CC"

        atlassian-confluence:
          description: "Confluence pages via the Atlassian Rovo MCP server"
          config:
            mode: streamable-http
            url: https://mcp.atlassian.com/v1/mcp
            headers:
              Authorization: "Basic {{ env.ATLASSIAN_MCP_CONFLUENCE }}"
            icon_url: "https://cdn.simpleicons.org/confluence/172B4D"
    ```

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

The `{{ env.* }}` placeholders are resolved when Holmes loads its configuration, so the tokens themselves never have to appear in your values file or config file.

## Available Tools

The tools Atlassian exposes depend on the scopes attached to your token. Every scoped token also gets these two platform tools regardless of which app it targets, which is useful for confirming a token is live:

| Tool | Description |
|------|-------------|
| `atlassianUserInfo` | Returns the authenticated account ID |
| `getAccessibleAtlassianResources` | Lists the sites the token can reach, with their `cloudId` |

Everything else is scope-dependent:

| Tool | Description | Scope |
|------|-------------|-------|
| `getJiraIssue` | Get a Jira issue by key or ID | `read:jira-work` |
| `getVisibleJiraProjects` | List projects the token can see | `read:jira-work` |
| `getTransitionsForJiraIssue` | List available workflow transitions | `read:jira-work` |
| `getJiraIssueRemoteIssueLinks` | List remote links on an issue | `read:jira-work` |
| `getJiraProjectIssueTypesMetadata` | List issue types for a project | `read:jira-work` |
| `getJiraIssueTypeMetaWithFields` | Get create/edit field metadata for an issue type | `read:jira-work` |
| `getIssueLinkTypes` | List the available issue link types | `read:jira-work` |
| `lookupJiraAccountId` | Resolve a user to an account ID | `read:jira-work` |
| `searchJiraIssuesUsingJql` | Search issues with JQL | `search:jira-work` |
| `createJiraIssue` | Create an issue | `write:jira-work` |
| `editJiraIssue` | Edit an existing issue | `write:jira-work` |
| `addCommentToJiraIssue` | Comment on an issue | `write:jira-work` |
| `addWorklogToJiraIssue` | Log work against an issue | `write:jira-work` |
| `transitionJiraIssue` | Move an issue through its workflow | `write:jira-work` |
| `getConfluencePage` | Get a Confluence page and its body | `read:page:confluence` |
| `getPagesInConfluenceSpace` | List pages in a space | `read:page:confluence` |
| `getConfluencePageDescendants` | Walk a page's child pages | `read:hierarchical-content:confluence` |
| `getConfluenceSpaces` | List spaces | `read:space:confluence` |
| `getConfluencePageFooterComments` | Read footer comments on a page | `read:comment:confluence` |
| `getConfluencePageInlineComments` | Read inline comments on a page | `read:comment:confluence` |
| `getConfluenceCommentChildren` | Read replies to a comment | `read:comment:confluence` |
| `searchConfluenceUsingCql` | Search Confluence with CQL | `search:confluence` |
| `createConfluencePage` | Create a page | `write:page:confluence` |
| `updateConfluencePage` | Update a page | `write:page:confluence` |
| `createConfluenceFooterComment` | Add a footer comment to a page | `write:page:confluence` |
| `createConfluenceInlineComment` | Add an inline comment to a page | `write:page:confluence` |

Atlassian adds tools over time, so treat this as a snapshot rather than a contract — `holmes toolset list` and the server's own `tools/list` are the source of truth for what your token actually gets. See Atlassian's [supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/) reference for products beyond Jira and Confluence.

Three `TeamworkGraph` tools (`getTeamworkGraphContext`, `getTeamworkGraphObject`, `addTeamworkGraphContext`) are also advertised to every client. They traverse relationships across Atlassian products and require their own scopes, so they will appear in your tool list even when they are not usable.

!!! note "Most tools need a cloudId"
    Rovo tools are site-scoped and take a `cloudId` argument. Holmes can discover it by calling `getAccessibleAtlassianResources`, but putting it in `llm_instructions` saves a round trip. To look it up yourself, visit `https://<your-site>.atlassian.net/_edge/tenant_info`.

## Testing the Connection

```bash
holmes toolset list
```

The entries should show as `enabled` — but note that `enabled` only means Holmes reached the server and authenticated. It does **not** mean the Jira or Confluence tools were granted. Confirm that separately:

```bash
holmes ask "List the Jira projects I have access to"
```

If Holmes reports that it has no tool for this, or you see `Tool getVisibleJiraProjects not found`, your token is missing the scope — or is a classic token. Recheck the [prerequisites](#prerequisites).

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

Which tools come back tells you what went wrong, so check the tool list before debugging anything else. The counts below were observed against Rovo at the time of writing and will drift as Atlassian ships tools — the pattern is what matters, not the exact number:

| Tools returned | Meaning |
|----------------|---------|
| `TeamworkGraph` only (3) | Classic API token — no scopes at all |
| `TeamworkGraph` plus `atlassianUserInfo` and `getAccessibleAtlassianResources` (5) | Scoped token, but none of its scopes map to Rovo tools |
| The above plus a block of `*Jira*` or `*Confluence*` tools (14 for Jira read + search) | Working scoped token |

**Only three `TeamworkGraph` tools appear**

Your token is a classic API token without scopes. Holmes will show the toolset as `enabled` — Atlassian accepts the credential and serves its default tool set — but none of the Jira or Confluence tools are granted. Create a new token with **Create API token with scopes**.

**`403 Forbidden ... requires a modern API token (API token with scopes). Legacy API tokens without scopes are not supported.`**

Same cause as above. The credential is valid, the token type is not.

**Five tools appear — the platform tools work, but no Jira or Confluence tools**

The token is genuinely scoped (that is why `atlassianUserInfo` and `getAccessibleAtlassianResources` are there) but its scopes don't map to any Rovo tool. For Confluence this almost always means classic scopes were selected instead of the granular `read:page:confluence`-style ones. Scopes can't be edited after creation, so reissue the token.

You can confirm which product a token is actually scoped for by calling the product gateway directly — a scope mismatch returns `401 Unauthorized; scope does not match`:

```bash
CLOUD_ID=<YOUR_CLOUD_ID>
# Jira scopes
curl -s -o /dev/null -w '%{http_code}\n' -u "<EMAIL>:<TOKEN>" \
  "https://api.atlassian.com/ex/jira/$CLOUD_ID/rest/api/3/myself"
# Confluence granular scopes
curl -s -o /dev/null -w '%{http_code}\n' -u "<EMAIL>:<TOKEN>" \
  "https://api.atlassian.com/ex/confluence/$CLOUD_ID/wiki/api/v2/spaces?limit=1"
```

Note that the Confluence probe hits `/wiki/api/v2/spaces`, which specifically requires `read:space:confluence`. If you scoped the token more narrowly — say `read:page:confluence` and `search:confluence` only — this probe returns `401` even though the token is valid and Rovo will serve the page and search tools. Probe `/wiki/api/v2/pages?limit=1` instead in that case. These probes must go through `api.atlassian.com`; scoped tokens are rejected against `<your-site>.atlassian.net`.

**`401 Unauthorized`**

The email/token pair is wrong, the base64 encoding is malformed, or the token has been revoked. Re-run the `printf ... | base64 | tr -d '\n'` command — the `tr` is what strips the line wrapping that `base64` adds by default, and a header containing a newline will be rejected.

**The toolset fails to load entirely**

Confirm the org-level setting is on (**Atlassian Administration** → **Rovo** → **Rovo MCP Server** → **Authentication**) and that outbound traffic to `mcp.atlassian.com` is allowed from wherever Holmes runs.

**A tool returns "not found"**

That tool's scope isn't on your token. Tools are filtered by scope, so the server reports them as missing rather than as permission errors. If the missing tools are all from one product, you are probably hitting the one-app-per-token limit — check that you registered a second `mcp_servers` entry with that product's token.

## Additional Resources

- [Atlassian Rovo MCP Server documentation](https://support.atlassian.com/atlassian-rovo-mcp-server/)
- [Configuring authentication via API token](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/)
- [Supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/)
- [Manage API tokens for your Atlassian account](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
- [OAuth MCP Servers in Holmes](../oauth-mcp-servers.md)
