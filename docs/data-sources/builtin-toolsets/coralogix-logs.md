# Coralogix

HolmesGPT can use Coralogix for logs/traces (DataPrime) and, separately, PromQL-style metrics. This page shows both setups.

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


```

**Note**: Both toolsets use the same API key. The DataPrime toolset supports fields (`CoralogixConfig`): `api_key`, `domain`, `team_hostname`, optional `labels`.

## Recommended: Customize Coralogix Instructions

By specifying details about your Coralogix metrics, logs, and traces, you can significantly speed up and improve investigations. This allows Holmes to work with your environment directly, rather than spending time discovering labels, mappings, and metric names on its own.

To configure this:

1. Go to [platform.robusta.dev](https://platform.robusta.dev/)
2. Navigate to **Settings → AI Assistant → AI Customization**
3. Add your labels and metric details
4. Save your changes

### Example Custom Instructions

Below is an example of how your custom instructions might look, based on the labels and metrics used in your environment:

```text
# Coralogix details

For Coralogix, use the following label mappings for logs:
- pod: k8s.pod_name
- namespace: k8s.namespace_name
- service: k8s.service_name
- deployment: k8s.deployment_name

Custom Coralogix metrics:
- payments_failures: tracks payment processing failures
- api_latency_p95: 95th percentile API latency
```
