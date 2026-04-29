# Confluence

By enabling this toolset, HolmesGPT can fetch and search Confluence pages. This is particularly useful if you store runbooks in Confluence and want Holmes to use them during investigations.

LLMs can parse Confluence storage format (XHTML with macros) directly, so page content is returned as-is for maximum fidelity.

Works with both **Confluence Cloud** and **Confluence Data Center / Server**.

## Configuration

HolmesGPT supports three ways to connect to Confluence. Pick the one that matches your setup:

| Setup | `subtype` value | When to use |
|-------|-----------------|-------------|
| [Confluence Cloud](#confluence-cloud) (recommended) | `cloud` | Atlassian-hosted Confluence at `<your-company>.atlassian.net` |
| [Confluence Data Center - Personal Access Token](#confluence-data-center-personal-access-token) | `dc-pat` | Self-hosted Confluence Data Center / Server using a PAT (recommended for DC) |
| [Confluence Data Center - Basic Auth](#confluence-data-center-basic-auth) | `dc-basic` | Self-hosted Confluence Data Center / Server using username + password |

!!! note "About `subtype`"
    The top-level `subtype:` field in each example tells HolmesGPT which Confluence variant you're connecting to. Setting it is recommended — each variant fixes its own auth mode and API path prefix internally, and tags the resulting toolset card under the correct integration in the UI. If you omit `subtype`, HolmesGPT will fall back to inferring the variant from the URL and `auth_type` field for backwards compatibility.

### Confluence Cloud

HolmesGPT authenticates to Confluence Cloud with an Atlassian API token.

**Create an API token:**

Go to [Atlassian API Tokens](https://id.atlassian.com/manage/api-tokens){:target="_blank"} and create a new token. For service accounts, create a scoped API token in the [Atlassian Admin](https://admin.atlassian.com){:target="_blank"} under **Security** > **API tokens**.

=== "Holmes CLI"

    Add to your config file (`~/.holmes/config.yaml`):

    ```yaml
    toolsets:
      confluence:
        enabled: true
        subtype: cloud
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

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your API token:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=api-key=your-api-token \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
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
        subtype: cloud
        config:
          api_url: "{{ env.CONFLUENCE_API_URL }}"
          user: "{{ env.CONFLUENCE_USER }}"
          api_key: "{{ env.CONFLUENCE_API_KEY }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your API token:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=api-key=your-api-token \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

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
          subtype: cloud
          config:
            api_url: "{{ env.CONFLUENCE_API_URL }}"
            user: "{{ env.CONFLUENCE_USER }}"
            api_key: "{{ env.CONFLUENCE_API_KEY }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

!!! note "Scoped tokens and service accounts"
    Scoped API tokens and service account tokens on Confluence Cloud require routing through the Atlassian API gateway (`api.atlassian.com`). HolmesGPT auto-detects this and switches to the gateway transparently — no extra configuration needed. If auto-detection doesn't work, you can set `cloud_id` explicitly in raw YAML (find it at `https://yourcompany.atlassian.net/_edge/tenant_info`).

### Confluence Data Center - Personal Access Token

HolmesGPT authenticates to a self-hosted Confluence Data Center (or Server) instance with a Personal Access Token. This is the **recommended** auth method for Data Center — PATs can be revoked individually and don't require sharing a password.

**Create a Personal Access Token:**

In Confluence Data Center, go to your **Profile** > **Personal Access Tokens** > **Create token**.

=== "Holmes CLI"

    Add to your config file (`~/.holmes/config.yaml`):

    ```yaml
    toolsets:
      confluence:
        enabled: true
        subtype: dc-pat
        config:
          api_url: "https://confluence.yourcompany.com"
          api_key: "your-personal-access-token"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Personal Access Token:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=pat=your-personal-access-token \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
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
        subtype: dc-pat
        config:
          api_url: "{{ env.CONFLUENCE_API_URL }}"
          api_key: "{{ env.CONFLUENCE_PAT }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Personal Access Token:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=pat=your-personal-access-token \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
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
          subtype: dc-pat
          config:
            api_url: "{{ env.CONFLUENCE_API_URL }}"
            api_key: "{{ env.CONFLUENCE_PAT }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

### Confluence Data Center - Basic Auth

HolmesGPT authenticates to a self-hosted Confluence Data Center (or Server) instance with a username and password. Prefer Personal Access Tokens where possible; use this mode when PATs are not available.

=== "Holmes CLI"

    Add to your config file (`~/.holmes/config.yaml`):

    ```yaml
    toolsets:
      confluence:
        enabled: true
        subtype: dc-basic
        config:
          api_url: "https://confluence.yourcompany.com"
          user: "your-username"
          api_key: "your-password"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your password:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=password=your-password \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: CONFLUENCE_API_URL
        value: "https://confluence.yourcompany.com"
      - name: CONFLUENCE_USER
        value: "your-username"
      - name: CONFLUENCE_PASSWORD
        valueFrom:
          secretKeyRef:
            name: confluence-credentials
            key: password

    toolsets:
      confluence:
        enabled: true
        subtype: dc-basic
        config:
          api_url: "{{ env.CONFLUENCE_API_URL }}"
          user: "{{ env.CONFLUENCE_USER }}"
          api_key: "{{ env.CONFLUENCE_PASSWORD }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your password:

    ```bash
    kubectl create secret generic confluence-credentials \
      --from-literal=password=your-password \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: CONFLUENCE_API_URL
          value: "https://confluence.yourcompany.com"
        - name: CONFLUENCE_USER
          value: "your-username"
        - name: CONFLUENCE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: confluence-credentials
              key: password
      toolsets:
        confluence:
          enabled: true
          subtype: dc-basic
          config:
            api_url: "{{ env.CONFLUENCE_API_URL }}"
            user: "{{ env.CONFLUENCE_USER }}"
            api_key: "{{ env.CONFLUENCE_PASSWORD }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Configuration Reference

`subtype` is set at the toolset level (sibling of `enabled:` and `config:`); the rest of the fields below go inside `config:`.

| Option | Default | Description |
|--------|---------|-------------|
| `subtype` | (inferred) | Top-level field — picks the Confluence variant. One of `cloud`, `dc-pat`, `dc-basic`. Setting it is recommended; if omitted, HolmesGPT infers the variant from the URL pattern and `auth_type` for backwards compatibility. |
| `api_url` | (required) | Base URL of the Confluence instance |
| `api_key` | (required) | API token (Cloud), Personal Access Token, or password (Data Center) |
| `user` | `null` | User email (Cloud) or username (Data Center). Required for `cloud` and `dc-basic`; not used by `dc-pat`. |
| `cloud_id` | `null` | Atlassian Cloud ID for the API gateway. Only relevant for `cloud` with scoped tokens or service accounts that must route through `api.atlassian.com`. Auto-detected when a direct call returns 401/403; set explicitly to skip the auto-detect round-trip or force gateway routing. |

!!! note "Auth mode and path prefix"
    The `auth_type` (basic vs. bearer) and `api_path_prefix` (`/wiki` vs. `""`) are determined entirely by the `subtype` you pick — Cloud uses basic auth at `/wiki`, DC PAT uses bearer with no prefix, DC Basic uses basic auth with no prefix. They aren't user-configurable knobs. If you have a non-standard Data Center deployment that needs a different path, please [open an issue](https://github.com/HolmesGPT/holmesgpt/issues).

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

If you get `401 Unauthorized`, verify your credentials. If you get `404 Not Found`, double-check the `subtype` — Cloud routes to `/wiki/rest/api`, while Data Center routes to `/rest/api`.
