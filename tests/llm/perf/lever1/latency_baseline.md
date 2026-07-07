# MCP data-source wizard — latency baseline

- Endpoint: `https://stg.api.robusta.dev/integrations/stream/actions`
- Model: `Robusta/Opus 4.7`
- Iterations: 5 (ok: 5, failed: 0)

## End-to-end wall time
- mean **113.1s**, median 105.4s, min 91.7s, max 141.9s

- time to first event: mean 12.96s
- LLM turns (tool batches): mean 5.4, max 8
- tool calls per run: mean 7.8, max 9

## Per-tool duration (across all runs)

| tool | calls | mean | median | max |
|---|---|---|---|---|
| `DataSourceSetupGuide` | 5 | 0.01s | 0.01s | 0.02s |
| `IdentifyDataSourceApp` | 4 | 0.00s | 0.00s | 0.00s |
| `ReportDataSourceProgress` | 14 | 0.00s | 0.00s | 0.01s |
| `fetch_webpage` | 16 | 0.41s | 0.24s | 1.24s |

## fetch_webpage invocations observed

- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/#connecting-to-the-remote-mcp-server
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/getting-started/
- Internet: Fetch Webpage https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/
- Internet: Fetch Webpage https://mcp.sentry.dev
- Internet: Fetch Webpage https://mcp.sentry.dev/
- Internet: Fetch Webpage https://registry.modelcontextprotocol.io/v0/servers?search=sentry

## Per-run detail

- run 1: 133.5s, 5 turns, 9 tools, guide✓
- run 2: 141.9s, 4 turns, 7 tools, guide✓
- run 3: 91.7s, 5 turns, 7 tools, guide✓
- run 4: 93.1s, 5 turns, 8 tools, guide✓
- run 5: 105.4s, 8 turns, 8 tools, guide✓
