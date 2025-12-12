# Coralogix

HolmesGPT can use Coralogix for logs/traces (DataPrime) and, separately, PromQL-style metrics. This page shows both setups.

--8<-- "snippets/toolsets_that_provide_logging.md"

## Prerequisites
1. A [Coralogix API key](https://coralogix.com/docs/developer-portal/apis/data-query/direct-archive-query-http-api/#api-key) which is assigned the `DataQuerying` permission preset
2. A [Coralogix domain](https://coralogix.com/docs/user-guides/account-management/account-settings/coralogix-domain/). For example `eu2.coralogix.com`
3. Your team's [name or hostname](https://coralogix.com/docs/user-guides/account-management/organization-management/create-an-organization/#teams-in-coralogix). For example `your-company-name`

You can deduce the `domain` and `team_hostname` configuration fields by looking at the URL you use to access the Coralogix UI.

For example if you access Coralogix at `https://my-team.app.eu2.coralogix.com/` then the `team_hostname` is `my-team` and the Coralogix `domain` is `eu2.coralogix.com`.

## Configuration
Configure both the Coralogix DataPrime toolset (for logs/traces) and the Prometheus metrics toolset (for metrics) using the same API key:

```yaml-toolset-config
toolsets:
  coralogix:
    enabled: true
    config:
      api_key: "<your Coralogix API key>"
      domain: "eu2.coralogix.com"
      team_hostname: "your-company-name"

  prometheus/metrics:
    enabled: true
    config:
      headers:
        Authorization: "Bearer <your Coralogix API key>"
      prometheus_url: "https://ng-api-http.eu2.coralogix.com/metrics"  # replace domain
      healthcheck: "api/v1/query?query=up"

  kubernetes/logs:
    enabled: false  # disable default Kubernetes logging if desired
```

**Note**: Both toolsets use the same API key. The DataPrime toolset supports fields (`CoralogixConfig`): `api_key`, `domain`, `team_hostname`, optional `labels`.

## Non-standard metrics/labels
If Coralogix Prometheus uses non-standard labels or custom metric names, add instructions in Holmes AI customization: go to [platform.robusta.dev](https://platform.robusta.dev/) → Settings → AI Assistant → AI Customization, add label/metric hints, and save.
