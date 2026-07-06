# 283_mcp_data_source_setup — latency baseline for the "Add MCP data source" chat wizard

This eval reproduces the robusta-frontend **"Set up with Holmes"** chat for adding a
**custom MCP data source** (Sentry), so we can measure and then optimize how long that
flow takes. It is the eval-side twin of the frontend prompt builders; the shared
frontend payload lives in `tests/llm/fixtures/shared/data_source_mcp_setup.yaml`
(mirrors `custom-data-source-setup-prompt.ts`, `troubleshoot-prompts.ts`,
`DataSourceTroubleshootStep.vue`, and the four callable tools).

## Why the flow is slow (what we're profiling)

The wizard prompt tells Holmes to read the app's docs page first, then Robusta's
remote-MCP docs, then (if needed) the MCP registry / OpenAPI specs, and to call
`ReportDataSourceProgress` before every step. Each doc read is a `fetch_webpage` call,
and Holmes's agentic loop is sequential per turn — so wall-clock time is driven by the
**number of LLM turns** and **doc fetches**. The latency signals to read:

- `holmes_duration` — end-to-end wall-clock for the run (logged to the Braintrust eval span)
- `num_llm_calls` — number of sequential agentic turns
- `tool_call_count` / `tools_used` — how many `fetch_webpage` (and other) calls happened
- In the Braintrust trace tree: the `gen_ai.chat` span count (turns) and the duration of
  each `holmesgpt.tool.fetch_webpage` span (per-doc-read latency)

## Running it (produces the baseline)

Run in an environment with a real Braintrust key and outbound access to the docs hosts
(`docs.sentry.io`, `holmesgpt.dev`, `registry.modelcontextprotocol.io`). Live doc fetches
are required, so keep `RUN_LIVE=true` (the default).

```bash
export BRAINTRUST_API_KEY=<your key>
export BRAINTRUST_ORG=robustadev          # default; the HolmesGPT project
export MODEL=anthropic/claude-opus-4-6     # Opus 4.6 (adjust the id to your provider
                                           # routing, e.g. openai/anthropic/claude-opus-4.6
                                           # via OpenRouter)
export RUN_LIVE=true
export ITERATIONS=5                        # average over a few runs; latency varies

poetry run pytest -k "283_mcp_data_source_setup" --no-cov -vv
```

Then open the run in Braintrust (project `HolmesGPT`), or read the local
`evals_report.md`, and record the baseline: `holmes_duration`, `num_llm_calls`,
`tool_call_count`, and the per-`fetch_webpage` span durations. That baseline is what the
optimization iterations are compared against.

## Notes

- **Tags:** `manual` — this is a live, network-dependent latency eval, not part of the
  fast/regression gate. It also carries `mcp`, `frontend`, `network`.
- **Single-turn by design:** production is multi-turn (Holmes asks hosting/auth/intended-use
  via `PromptMultipleChoice` and waits). CLI-mode evals are single-turn and that tool is a
  noop, so `user_prompt` inlines those answers to drive the full flow through to
  `DataSourceSetupGuide` in one agentic run. The latency-driving doc reads and per-step
  turns are preserved.
- **Swapping the app:** re-render the shared fixture's `additional_system_prompt` for a
  different app (Linear / Atlassian Jira / PagerDuty) by changing the `presetAppName` /
  `presetDocsUrl` inputs — see the header comment in the shared fixture for the source
  builders and the exact render inputs.
- **Sandbox note:** the Claude Code sandbox network policy blocks `docs.sentry.io` and
  `holmesgpt.dev` (and openrouter), so the live pass cannot be run there; run it in your
  own environment.
