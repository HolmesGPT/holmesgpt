# MCP data-source wizard — latency baseline

- Endpoint: `https://stg.api.robusta.dev/integrations/stream/actions`
- Model: `Robusta/Opus 4.7`
- Iterations: 3 (ok: 3, failed: 0)

## End-to-end wall time
- mean **152.4s**, median 145.6s, min 94.5s, max 217.1s

- time to first event: mean 14.50s
- LLM turns (tool batches): mean 5.7, max 9
- tool calls per run: mean 8.0, max 12

## Per-tool duration (across all runs)

| tool | calls | mean | median | max |
|---|---|---|---|---|
| `DataSourceSetupGuide` | 3 | 0.03s | 0.02s | 0.04s |
| `IdentifyDataSourceApp` | 3 | 0.00s | 0.00s | 0.00s |
| `ReportDataSourceProgress` | 9 | 0.00s | 0.00s | 0.00s |
| `fetch_webpage` | 9 | 1.35s | 0.89s | 3.09s |

## fetch_webpage invocations observed

- Internet: Fetch Webpage https://github.com/grafana/mcp-grafana
- Internet: Fetch Webpage https://grafana.com/docs/grafana-cloud/introduction/mcp/
- Internet: Fetch Webpage https://grafana.com/docs/grafana-cloud/machine-learning/mcp-server/
- Internet: Fetch Webpage https://grafana.com/docs/grafana-cloud/machine-learning/mcp/
- Internet: Fetch Webpage https://grafana.com/docs/grafana-cloud/monitor-infrastructure/mcp/
- Internet: Fetch Webpage https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/
- Internet: Fetch Webpage https://www.google.com/search?q=Grafana+Cloud+hosted+MCP+endpoint+URL+remote

## Per-run detail

- run 1: 217.1s, 4 turns, 5 tools, guide✓
- run 2: 145.6s, 9 turns, 12 tools, guide✓
- run 3: 94.5s, 4 turns, 7 tools, guide✓
