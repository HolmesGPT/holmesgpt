# MCP data-source wizard — latency baseline

- Endpoint: `https://stg.api.robusta.dev/integrations/stream/actions`
- Model: `Robusta/Opus 4.7`
- Iterations: 3 (ok: 3, failed: 0)

## End-to-end wall time
- mean **81.8s**, median 84.6s, min 62.7s, max 98.1s

- time to first event: mean 17.32s
- LLM turns (tool batches): mean 4.7, max 6
- tool calls per run: mean 5.3, max 6

## Per-tool duration (across all runs)

| tool | calls | mean | median | max |
|---|---|---|---|---|
| `DataSourceSetupGuide` | 3 | 0.00s | 0.00s | 0.01s |
| `IdentifyDataSourceApp` | 2 | 0.00s | 0.00s | 0.00s |
| `ReportDataSourceProgress` | 7 | 0.00s | 0.00s | 0.00s |
| `fetch_webpage` | 4 | 1.53s | 1.60s | 1.64s |

## fetch_webpage invocations observed

- Internet: Fetch Webpage https://docs.stripe.com/mcp
- Internet: Fetch Webpage https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/

## Per-run detail

- run 1: 84.6s, 4 turns, 5 tools, guide✓
- run 2: 98.1s, 6 turns, 6 tools, guide✓
- run 3: 62.7s, 4 turns, 5 tools, guide✓
