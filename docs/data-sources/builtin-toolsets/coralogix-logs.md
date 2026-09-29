# Coralogix

HolmesGPT can use Coralogix for logs/traces (DataPrime) and, separately, PromQL-style metrics. This page shows both setups.

## Prerequisites

1. A [Coralogix API key](https://coralogix.com/docs/developer-portal/apis/data-query/direct-archive-query-http-api/#api-key) with `DataQuerying` permissions
2. A [Coralogix domain](https://coralogix.com/docs/user-guides/account-management/account-settings/coralogix-domain/) (e.g., `eu2.coralogix.com`)
3. (Optional) Your team's [slug](https://coralogix.com/docs/user-guides/account-management/organization-management/create-an-organization/#teams-in-coralogix) - only needed for generating clickable UI permalink URLs in tool output

You can find your `domain` and `team_slug` from the URL you use to access Coralogix. For example, if you access Coralogix at `https://my-team.app.eu2.coralogix.com/` then `team_slug` is `my-team` and `domain` is `eu2.coralogix.com`.

## Configuration

Configure both the Coralogix DataPrime toolset (for logs/traces) and the Prometheus metrics toolset (for metrics) using the same API key. The `team_slug` field is optional — it's only used to generate clickable permalink URLs that open query results in the Coralogix UI.

Holmes automatically derives the UI hostname for permalinks from your `domain` — the Coralogix UI uses a different hostname than the API in most regions. For example, with the US2 domain (`us2.coralogix.com` or `cx498.coralogix.com`) permalinks point to `https://<team_slug>.app.cx498.coralogix.com`. If your team's UI lives at a non-standard address, set the optional `ui_url` field to its full base URL (e.g. `ui_url: "https://my-team.app.cx498.coralogix.com"`) to override the derived hostname.

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      coralogix:
        enabled: true
        config:
          api_key: "<your Coralogix API key>"
          domain: "eu2.coralogix.com"
          # Optional: enables clickable UI permalink URLs in tool output
          team_slug: "your-company-name"

      prometheus/metrics:
        enabled: true
        subtype: coralogix
        config:
          additional_headers:
            Authorization: "Bearer <your Coralogix API key>"
          prometheus_url: "https://ng-api-http.eu2.coralogix.com/metrics"  # replace domain
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Coralogix API key:

    ```bash
    kubectl create secret generic coralogix-api-key \
      --from-literal=api-key=your-coralogix-api-key \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: CORALOGIX_API_KEY
        valueFrom:
          secretKeyRef:
            name: coralogix-api-key
            key: api-key

    toolsets:
      coralogix:
        enabled: true
        config:
          api_key: "{{ env.CORALOGIX_API_KEY }}"
          domain: "eu2.coralogix.com"
          # Optional: enables clickable UI permalink URLs in tool output
          team_slug: "your-company-name"

      prometheus/metrics:
        enabled: true
        subtype: coralogix
        config:
          additional_headers:
            Authorization: "Bearer {{ env.CORALOGIX_API_KEY }}"
          prometheus_url: "https://ng-api-http.eu2.coralogix.com/metrics"  # replace domain
    ```


**Note**: Both toolsets use the same API key. Helm-tab users only need to create one Kubernetes secret — the env var feeds both the `coralogix` toolset's `api_key` field and the Prometheus toolset's `Authorization` header.

## Multiple Instances

```multi-instance
toolset: coralogix
name: Coralogix
config: |
  api_key: "<your Coralogix API key>"
  domain: "eu2.coralogix.com"
```

