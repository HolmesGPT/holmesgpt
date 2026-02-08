# Grafana Dashboards

Connect HolmesGPT to Grafana for dashboard analysis, query extraction, and understanding your monitoring setup. This integration enables investigation of dashboard configurations and extraction of Prometheus queries for deeper analysis.

## Prerequisites

A [Grafana service account token](https://grafana.com/docs/grafana/latest/administration/service-accounts/) with the following permissions:

- Basic role → Viewer

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      grafana/dashboards:
        enabled: true
        config:
          api_key: <your grafana service account token>
          api_url: <your grafana url>  # e.g. https://acme-corp.grafana.net or http://localhost:3000
          # Optional: Additional headers for all requests
          # additional_headers:
          #   X-Custom-Header: "custom-value"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Show me all dashboards tagged with 'kubernetes'"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        grafana/dashboards:
          enabled: true
          config:
            api_key: <your grafana API key>
            api_url: <your grafana url>  # e.g. https://acme-corp.grafana.net
            # Optional: Additional headers for all requests
            # additional_headers:
            #   X-Custom-Header: "custom-value"
    ```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| grafana_search_dashboards | Search for dashboards and folders by query, tags, UIDs, or folder locations |
| grafana_get_dashboard_by_uid | Retrieve complete dashboard JSON including all panels and queries |
| grafana_get_home_dashboard | Get the home dashboard configuration |
| grafana_get_dashboard_tags | List all tags used across dashboards for categorization |

## Advanced Configuration

### SSL Verification

For self-signed certificates, you can disable SSL verification:

```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:
      api_url: https://grafana.internal
      api_key: <your api key>
      verify_ssl: false  # Disable SSL verification (default: true)
```

### External URL

If HolmesGPT accesses Grafana through an internal URL but you want clickable links in results to use a different URL:

```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:
      api_url: http://grafana.internal:3000  # Internal URL for API calls
      external_url: https://grafana.example.com  # URL for links in results
      api_key: <your api key>
```

## How it Works

### Dashboard Query Extraction

When HolmesGPT retrieves a dashboard, it can extract and analyze Prometheus queries from dashboard panels. This is particularly useful for:

- Understanding what metrics a dashboard monitors
- Extracting queries for further investigation with the Prometheus toolset
- Analyzing dashboard time ranges and variable usage

### Example Usage

**Finding dashboards by tag:**
```bash
holmes ask "Find all dashboards tagged with 'production' or 'kubernetes'"
```

**Analyzing a specific dashboard:**
```bash
holmes ask "Show me what metrics the 'Node Exporter' dashboard monitors"
```

**Extracting queries for investigation:**
```bash
holmes ask "Get the CPU usage queries from the Kubernetes cluster dashboard and check if any nodes are throttling"
```
