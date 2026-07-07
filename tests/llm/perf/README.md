# Chat-wizard latency harness

Measures how long the **"Add data source" chat wizard** (MCP / HTTP API) takes,
by replaying the exact request the Robusta UI sends against a **deployed** Holmes
(e.g. staging in the DigitalOcean cluster) and timing the streamed SSE events.

This is a measurement tool, **not** a pytest eval. The `tests/llm/` framework runs
Holmes in-process; here we hit the real relay so the numbers reflect the deployed
model, toolset config, and cluster egress (the live doc fetches are what make the
wizard slow).

## Why the wizard is slow (context)

The wizard prompt is built on the frontend
(`robusta-frontend/.../toolset-config/custom-data-source-setup-prompt.ts`) and sent
as `additional_system_prompt` on `POST /integrations/stream/actions`
(`action_name=holmes_chat`). It tells Holmes to read the app's docs page, treat
Robusta's `holmesgpt.dev` docs as schema truth, and optionally hit the MCP registry
/ probe OpenAPI — several blocking `fetch_webpage` calls inside a sequential
per-turn agent loop. Cost is driven by the number of turns and doc fetches.

## Step 1 — capture a real wizard request (once)

The request body (including the byte-exact `additional_system_prompt`, `frontend_tools`,
and `model`) is captured from the live UI so we don't hand-port the frontend builders.

1. Log into staging and open **Data Sources → add an MCP data source → pick an app
   (e.g. Sentry) → "Set up with Holmes"**.
2. In browser devtools (Network), find the `POST .../integrations/stream/actions`
   request and copy its **request payload** (JSON) and note the `session_token`.
3. Save the payload to `fixtures/mcp_wizard_request.json`, then **strip the secret**:
   set `session_token` to `""` and redact `account_id` / `user_email` if you like.
   The token is injected at run time from the environment — never commit it.

(This capture can also be automated with the `agent-browser` skill in the
`robusta-frontend` repo using the E2E staging creds.)

## Step 2 — run the replay

```bash
export ROBUSTA_STAGING_SESSION_TOKEN='<fresh staging JWT>'   # short-lived; re-capture when it expires
poetry run python tests/llm/perf/replay_wizard_latency.py \
    --request tests/llm/perf/fixtures/mcp_wizard_request.json \
    --iterations 5 \
    --out tests/llm/perf/baseline
```

Outputs `baseline/latency_baseline.md` and `.json` with, per run and aggregated:
end-to-end wall time, time-to-first-event, LLM turns (tool batches), per-tool
duration (each `fetch_webpage` URL + how long it took), and tokens/cost if reported.

Add `--verbose` to watch the tool timeline live.

## Step 3 — Braintrust span detail (optional)

Only if the deployment has `HOLMES_ALLOW_PER_REQUEST_EXPERIMENT=true` and a
`BRAINTRUST_API_KEY` set. Then:

```bash
BRAINTRUST_EXPERIMENT="mcp-wizard-baseline" poetry run python tests/llm/perf/replay_wizard_latency.py ... 
```

The driver sends an `X-Braintrust-Experiment` header (`server.py:open_experiment_from_request`)
so all spans for the run group under that experiment in the `robustadev` project —
giving internal per-LLM-call / per-`fetch_webpage` span durations on top of the
client-side timeline. Note: per-request experiment routing is process-global, so
run it when staging isn't serving other concurrent chat traffic.

## Notes

- Each replay is a **real** Holmes run: real LLM spend + live doc fetches.
- `fixtures/mcp_wizard_request.json` must have `session_token: ""` before committing.
