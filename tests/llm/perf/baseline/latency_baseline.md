# MCP data-source wizard — latency baseline

- Endpoint: `https://stg.api.robusta.dev/integrations/stream/actions`
- Model: `Robusta/Opus 4.7`
- Iterations: 5 (ok: 5, failed: 0)

## End-to-end wall time
- mean **234.6s**, median 196.3s, min 157.7s, max 381.4s

- time to first event: mean 32.66s
- LLM turns (tool batches): mean 6.2, max 8
- tool calls per run: mean 10.4, max 12

## Per-tool duration (across all runs)

| tool | calls | mean | median | max |
|---|---|---|---|---|
| `DataSourceSetupGuide` | 5 | 0.01s | 0.01s | 0.02s |
| `IdentifyDataSourceApp` | 5 | 0.00s | 0.00s | 0.01s |
| `ReportDataSourceProgress` | 14 | 0.00s | 0.00s | 0.01s |
| `fetch_webpage` | 26 | 0.70s | 0.58s | 1.77s |
| `get_file_contents` | 2 | 0.35s | 0.35s | 0.64s |

## fetch_webpage invocations observed

- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/#connecting-with-a-remote-mcp-client
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/#connecting-with-an-access-token
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/#remote-mcp
- Internet: Fetch Webpage https://docs.sentry.io/product/sentry-mcp/getting-started/
- Internet: Fetch Webpage https://github.com/getsentry/sentry-mcp
- Internet: Fetch Webpage https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/
- Internet: Fetch Webpage https://mcp.sentry.dev/
- Internet: Fetch Webpage https://registry.modelcontextprotocol.io/v0/servers?search=sentry

## Per-run detail

- run 1: 381.4s, 8 turns, 12 tools, guide✓
- run 2: 252.9s, 7 turns, 8 tools, guide✓
- run 3: 196.3s, 5 turns, 11 tools, guide✓
- run 4: 184.9s, 6 turns, 11 tools, guide✓
- run 5: 157.7s, 5 turns, 10 tools, guide✓
