# MCP data-source wizard — latency baseline

- Endpoint: `https://stg.api.robusta.dev/integrations/stream/actions`
- Model: `Robusta/Opus 4.7`
- Iterations: 3 (ok: 3, failed: 0)

## End-to-end wall time
- mean **86.7s**, median 73.2s, min 56.3s, max 130.7s

- time to first event: mean 13.92s
- LLM turns (tool batches): mean 5.3, max 6
- tool calls per run: mean 6.7, max 8

## Per-tool duration (across all runs)

| tool | calls | mean | median | max |
|---|---|---|---|---|
| `DataSourceSetupGuide` | 3 | 0.01s | 0.01s | 0.01s |
| `IdentifyDataSourceApp` | 1 | 0.02s | 0.02s | 0.02s |
| `ReportDataSourceProgress` | 7 | 0.00s | 0.00s | 0.01s |
| `fetch_webpage` | 9 | 0.30s | 0.17s | 1.37s |

## fetch_webpage invocations observed

- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/#connecting-to-the-mcp-server
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/getting-started/
- Internet: Fetch Webpage https://github.com/getsentry/sentry-mcp
- Internet: Fetch Webpage https://mcp.sentry.dev/

## Per-run detail

- run 1: 73.2s, 5 turns, 8 tools, guide✓
- run 2: 56.3s, 5 turns, 5 tools, guide✓
- run 3: 130.7s, 6 turns, 7 tools, guide✓
