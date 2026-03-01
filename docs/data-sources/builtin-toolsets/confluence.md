# Confluence

By enabling this toolset, HolmesGPT can fetch and search Confluence pages. This is particularly useful if you store runbooks in Confluence and want Holmes to use them during investigations.

LLMs can parse Confluence storage format (XHTML with macros) directly, so page content is returned as-is for maximum fidelity.

Works with both **Confluence Cloud** and **Confluence Data Center / Server**.

## Configuration

=== "Confluence Cloud"

    **Create an API token:**

    Go to [Atlassian API Tokens](https://id.atlassian.com/manage/api-tokens){:target="_blank"} and create a new token. For service accounts, create a scoped API token in the [Atlassian Admin](https://admin.atlassian.com){:target="_blank"} under **Security** > **API tokens**.

    === "Holmes CLI"

        Add to your config file (`~/.holmes/config.yaml`):

        ```yaml
        toolsets:
          confluence:
            enabled: true
            config:
              api_url: "https://yourcompany.atlassian.net"
              user: "your-email@example.com"
              api_key: "your-api-token"
        ```

        To test, run:

        ```bash
        holmes ask "search Confluence for runbooks about database issues"
        ```

        --8<-- "snippets/toolset_refresh_warning.md"

    === "Robusta Helm Chart"

        ```yaml
        holmes:
          additionalEnvVars:
            - name: CONFLUENCE_API_URL
              value: "https://yourcompany.atlassian.net"
            - name: CONFLUENCE_USER
              value: "your-email@example.com"
            - name: CONFLUENCE_API_KEY
              valueFrom:
                secretKeyRef:
                  name: confluence-credentials
                  key: api-key
          toolsets:
            confluence:
              enabled: true
              config:
                api_url: "{{ env.CONFLUENCE_API_URL }}"
                user: "{{ env.CONFLUENCE_USER }}"
                api_key: "{{ env.CONFLUENCE_API_KEY }}"
        ```

        --8<-- "snippets/helm_upgrade_command.md"

    !!! note "Scoped tokens and service accounts"
        Scoped API tokens and service account tokens on Confluence Cloud require routing through the Atlassian API gateway (`api.atlassian.com`). HolmesGPT auto-detects this and switches to the gateway transparently — no extra configuration needed. If auto-detection doesn't work, you can set `cloud_id` explicitly (find it at `https://yourcompany.atlassian.net/_edge/tenant_info`).

=== "Confluence Data Center / Server"

    Data Center supports two authentication methods: Personal Access Tokens (recommended) and basic auth with username/password.

    **Create a Personal Access Token (PAT):**

    In Confluence Data Center, go to your **Profile** > **Personal Access Tokens** > **Create token**.

    === "Holmes CLI"

        Add to your config file (`~/.holmes/config.yaml`):

        ```yaml
        # Using Personal Access Token (recommended)
        toolsets:
          confluence:
            enabled: true
            config:
              api_url: "https://confluence.yourcompany.com"
              api_key: "your-personal-access-token"
              auth_type: "bearer"
              api_path_prefix: ""
        ```

        ```yaml
        # Using username/password
        toolsets:
          confluence:
            enabled: true
            config:
              api_url: "https://confluence.yourcompany.com"
              user: "your-username"
              api_key: "your-password"
              api_path_prefix: ""
        ```

        --8<-- "snippets/toolset_refresh_warning.md"

    === "Robusta Helm Chart"

        ```yaml
        # Using Personal Access Token (recommended)
        holmes:
          additionalEnvVars:
            - name: CONFLUENCE_API_URL
              value: "https://confluence.yourcompany.com"
            - name: CONFLUENCE_PAT
              valueFrom:
                secretKeyRef:
                  name: confluence-credentials
                  key: pat
          toolsets:
            confluence:
              enabled: true
              config:
                api_url: "{{ env.CONFLUENCE_API_URL }}"
                api_key: "{{ env.CONFLUENCE_PAT }}"
                auth_type: "bearer"
                api_path_prefix: ""
        ```

        --8<-- "snippets/helm_upgrade_command.md"

## Configuration Reference

| Option | Default | Description |
|--------|---------|-------------|
| `api_url` | (required) | Base URL of the Confluence instance |
| `api_key` | (required) | API token (Cloud), Personal Access Token, or password (Data Center) |
| `user` | `null` | User email (Cloud) or username (Data Center). Required for basic auth. |
| `auth_type` | `basic` | `basic` for Cloud or Data Center username/password. `bearer` for Data Center PATs. |
| `api_path_prefix` | `/wiki` | Path prefix before `/rest/api`. Cloud uses `/wiki`. Data Center typically uses `""` (empty). |
| `cloud_id` | `null` | Atlassian Cloud ID for the API gateway. Auto-detected for Cloud URLs when needed (scoped tokens / service accounts). |

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| confluence_request | Make HTTP GET requests to the Confluence REST API. Supports fetching pages, searching with CQL, listing spaces, and retrieving child pages or comments. |

## Common Use Cases

```
Search Confluence for runbooks about database connection pool issues
```

```
Find the on-call runbook in the SRE space and tell me the escalation contacts
```

```
Get the Confluence page at https://mycompany.atlassian.net/wiki/spaces/OPS/pages/12345 and summarize the remediation steps
```

## Troubleshooting

```bash
# Test Cloud authentication
curl -u "your-email@example.com:your-api-token" \
  "https://yourcompany.atlassian.net/wiki/rest/api/space?limit=1"

# Test Data Center PAT authentication
curl -H "Authorization: Bearer your-pat-token" \
  "https://confluence.yourcompany.com/rest/api/space?limit=1"

# Test Data Center basic auth
curl -u "username:password" \
  "https://confluence.yourcompany.com/rest/api/space?limit=1"
```

If you get `401 Unauthorized`, verify your credentials. If you get `404 Not Found`, check the `api_path_prefix` — Cloud uses `/wiki` while Data Center typically uses no prefix.
