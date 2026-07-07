# Lever #1 (research budget) — before/after

Same setup both sides: Sentry MCP wizard, staging relay → `alon-elish-cluster`,
model `Robusta/Opus 4.7`, 5 iterations each. Baseline = `../baseline/`, capped =
this dir (`../lever1/`). Change = the "read AT MOST 2 pages, stop once you have
URL/transport/auth, no GitHub/registry/sub-page crawling" budget added to the setup
prompt (frontend `custom-data-source-setup-prompt.ts` + eval fixture
`data_source_mcp_setup.yaml`).

| metric | baseline | lever #1 | change |
|---|---|---|---|
| mean wall time | 234.6s | **113.1s** | **-52%** |
| median wall time | 196.3s | 105.4s | -46% |
| min / max | 158 / 381s | 92 / 142s | tighter spread |
| time to first event | 32.7s | 13.0s | -60% |
| LLM turns (mean) | 6.2 | 5.4 | -13% |
| tool calls / run (mean) | 10.4 | 7.8 | -25% |
| fetch_webpage / run | 5.2 | 3.2 | -38% |
| GitHub repo reads | seen in baseline runs | **0 / 5** | eliminated |
| MCP registry lookups | seen in baseline runs | 3 / 5 | reduced, not gone |
| correct config (mcp.sentry.dev + streamable-http + bearer/env + mcp_servers) | 5 / 5 | **5 / 5** | no regression |

## Read of the result

- ~2x faster end-to-end (235s → 113s) with **no correctness regression** on Sentry —
  every capped run still produced the right endpoint, transport, and auth.
- The biggest behavioural change: the "do NOT open GitHub repositories" clause fully
  stopped the GitHub-repo detours the baseline took (which had pulled in the
  `get_file_contents` GitHub-MCP tool). That alone removed turns.
- The MCP registry is still consulted in 3/5 runs — the budget permits ONE targeted
  lookup for a genuinely missing field, and the model still reaches for it. Could be
  tightened further, but that risks over-constraining unknown/less-documented apps.

## Downside assessment

- **None observed here**, but this tests only Sentry, which is well documented. For an
  obscure or poorly-documented app, a hard 2-page cap could in theory cut a read the
  model actually needed. The budget mitigates this by allowing one targeted extra
  lookup for a missing required field. Recommend spot-checking one lightly-documented
  MCP (e.g. an in-house / niche server) before shipping broadly.
- n=5 with real LLM variance, but the effect (~2x) is far larger than the run-to-run
  spread, so the direction is solid.

## Reproduce

```bash
ROBUSTA_STAGING_SESSION_TOKEN=<fresh token> \
poetry run python tests/llm/perf/replay_wizard_latency.py \
  --request tests/llm/perf/fixtures/mcp_wizard_request.json \
  --iterations 5 --out tests/llm/perf/lever1
```
(Rebuild the fixture first with `build_staging_request.py` if the prompt changed.)
