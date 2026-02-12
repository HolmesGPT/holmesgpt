# New Relic

By enabling this toolset, HolmesGPT will be able to pull traces and logs from New Relic for investigations.

## Prerequisites

1. A New Relic API Key with necessary permissions to access traces and logs
2. Your New Relic Account ID

You can find these in your New Relic account under Administration > API keys and Account settings.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      newrelic:
        enabled: true
        config:
          api_key: "<your New Relic API key>"
          account_id: "<your New Relic account ID>"
          is_eu_datacenter: false  # Set to true if using New Relic EU region
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        newrelic:
          enabled: true
          config:
            api_key: "<your New Relic API key>"
            account_id: "<your New Relic account ID>"
            is_eu_datacenter: false  # Set to true if using New Relic EU region
    ```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| newrelic_execute_nrql_query | Execute NRQL queries for Traces, APM, Spans, Logs and more |

## How it Works

You don't need to know NRQL to use this toolset. Holmes will automatically construct and execute NRQL queries based on your investigation needs.

For example, when investigating application logs, Holmes might execute a query like:
```sql
SELECT message, timestamp FROM Log WHERE pod_name = 'your-app' SINCE 1 hour ago
```

To learn more about NRQL syntax, see the [New Relic Query Language documentation](https://docs.newrelic.com/docs/nrql/get-started/introduction-nrql-new-relics-query-language/).
