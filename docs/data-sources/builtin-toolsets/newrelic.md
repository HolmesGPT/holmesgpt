# New Relic

By enabling this toolset, HolmesGPT will be able to pull traces and logs from New Relic for investigations.

## Prerequisites

1. A **New Relic User API Key** (the one whose value starts with `NRAK-`)
2. Your **New Relic Account ID** (numeric, e.g. `1234567`)

### Creating a User API Key

New Relic supports several API key types — HolmesGPT needs a **User API Key** (prefix `NRAK-`). Ingest License Keys (`NRII-`), Browser keys, and Mobile keys will not work.

Go to **Administration → API keys** in your New Relic UI:

- **US region**: [https://one.newrelic.com/admin-portal/api-keys/launcher](https://one.newrelic.com/admin-portal/api-keys/launcher)
- **EU region**: [https://one.eu.newrelic.com/admin-portal/api-keys/launcher](https://one.eu.newrelic.com/admin-portal/api-keys/launcher)

Click **Create a key**, choose key type **User**, give it a name (e.g. "holmesgpt"), and copy the `NRAK-...` value — you'll only see it once.

### Finding your Account ID

In the same UI, click your profile icon (bottom-left) → **Administration** → your account name. The numeric ID appears in the URL and on the account overview page.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      newrelic:
        enabled: true
        config:
          api_key: "<your New Relic User API Key>"
          account_id: "<your New Relic account ID>"
          is_eu_datacenter: false  # Set to true if using New Relic EU region
          enable_multi_account: false  # Optional: set to true to query across multiple accounts
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your User API Key:

    ```bash
    kubectl create secret generic newrelic-credentials \
      --from-literal=api-key=your-new-relic-user-api-key \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: NEW_RELIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: newrelic-credentials
            key: api-key

    toolsets:
      newrelic:
        enabled: true
        config:
          api_key: "{{ env.NEW_RELIC_API_KEY }}"
          account_id: "<your New Relic account ID>"
          is_eu_datacenter: false  # Set to true if using New Relic EU region
          enable_multi_account: false  # Optional: set to true to query across multiple accounts
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your User API Key:

    ```bash
    kubectl create secret generic newrelic-credentials \
      --from-literal=api-key=your-new-relic-user-api-key \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: NEW_RELIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: newrelic-credentials
              key: api-key
      toolsets:
        newrelic:
          enabled: true
          config:
            api_key: "{{ env.NEW_RELIC_API_KEY }}"
            account_id: "<your New Relic account ID>"
            is_eu_datacenter: false  # Set to true if using New Relic EU region
            enable_multi_account: false  # Optional: set to true to query across multiple accounts
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Configuration Reference

| Option | Default | Description |
|--------|---------|-------------|
| `api_key` | (required) | New Relic User API Key (starts with `NRAK-`). |
| `account_id` | (required) | New Relic account ID (numeric, e.g. `1234567`). |
| `is_eu_datacenter` | `false` | Set `true` for the EU region. Controls both the API endpoint (`api.eu.newrelic.com`) and the URL used in clickable links in Holmes's responses. |
| `enable_multi_account` | `false` | Enable cross-account queries. When true, Holmes exposes an additional `newrelic_list_organization_accounts` tool and lets individual NRQL queries override the account ID. |

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| newrelic_execute_nrql_query | Execute NRQL queries for Traces, APM, Spans, Logs and more |
| newrelic_list_organization_accounts | List all account IDs/names available to the API key (only enabled when `enable_multi_account: true`) |

## Multi-Account Mode

If your organization has multiple New Relic accounts, setting `enable_multi_account: true` lets Holmes query across all of them.

Your API key must have access to all the accounts you want Holmes to query. Without the required permissions, Holmes won't be able to retrieve data from those accounts.

The `account_id` in your config is used as the default when no specific account is specified.

## How it Works

You don't need to know NRQL to use this toolset. Holmes will automatically construct and execute NRQL queries based on your investigation needs.

For example, when investigating application logs, Holmes might execute a query like:
```sql
SELECT message, timestamp FROM Log WHERE pod_name = 'your-app' SINCE 1 hour ago
```

To learn more about NRQL syntax, see the [New Relic Query Language documentation](https://docs.newrelic.com/docs/nrql/get-started/introduction-nrql-new-relics-query-language/).
