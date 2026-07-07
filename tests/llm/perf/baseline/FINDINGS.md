# MCP data-source wizard latency — baseline findings (2026-07-07)

Measured by replaying the real "Set up with Holmes" wizard request (Sentry MCP) against
**staging Holmes** (`stg.api.robusta.dev` relay → `alon-elish-cluster`), model
`Robusta/Opus 4.7`, 5 iterations. See `latency_baseline.md` / `.json` for the raw numbers
(regenerate with `replay_wizard_latency.py`).

## Headline

| metric | value |
|---|---|
| end-to-end wall time | **mean 234.6s**, median 196s, range 158–381s |
| time to first event | mean 32.7s |
| LLM turns (tool batches) | mean 6.2, max 8 |
| tool calls / run | mean 10.4, max 12 |
| `fetch_webpage` calls | 26 across 5 runs (~5/run), **mean 0.7s each** |

## The doc fetches are NOT the bottleneck

Total doc-fetch time is ~0.7s × ~5 calls ≈ **3–4s per run** — under 2% of the ~235s total.
The internet toolset is fast (well under its 5s timeout) and multiple fetches in one turn run
in parallel. Speeding up or caching the fetches would barely move the number.

## The cost is sequential LLM turns

Wall time is dominated by **5–8 sequential Opus 4.7 completions per run**, each carrying the
large static system prompt (~40k chars / ~10k tokens) plus the growing transcript of fetched
doc content. Wall time tracks turn count almost linearly (5 turns → 158s, 8 turns → 381s), and
time-to-first-event alone is ~33s (the first turn, before any tool runs). Variance across runs
is driven by how many doc pages Holmes chooses to read (run 1 read 6 Sentry sub-pages + the
GitHub repo; run 5 read far fewer) — more pages → more turns and bigger context → slower.

## Optimization levers, ranked by expected impact

1. **Cut the number of turns / doc reads.** The prompt tells Holmes to read the app docs page
   *and* sub-pages *and* Robusta's docs *and* the MCP registry. Constraining it to the 1–2 pages
   actually needed (or short-circuiting for known apps) is the biggest lever — each turn removed
   is ~30–40s.
2. **Re-enable `INCLUDE_KNOWN_HINTS`** (frontend, currently `false` with a `// TEMP` note). Sentry
   is a known app; a curated hint (endpoint + docs URL) could let Holmes skip most discovery.
3. **Shrink / cache the 40k-char system prompt.** It is re-sent on every one of the 6+ turns; the
   `additional_system_prompt` base could be trimmed for the setup flow, and prompt caching would
   cut the per-turn input-token tax.
4. **Drop or batch the pre-every-tool `ReportDataSourceProgress` calls** — they add turns/latency
   for a cosmetic status line.

Fetch-side tuning (`INTERNET_TOOLSET_TIMEOUT_SECONDS`, parallelism) is **not** worth pursuing —
the fetches are already ~0.7s.

## Next step

Test levers 1–4 one at a time, re-running `replay_wizard_latency.py` and comparing wall time /
turn count. For internal per-LLM-call span durations, enable Braintrust tracing on the staging
deployment (`HOLMES_ALLOW_PER_REQUEST_EXPERIMENT=true`) and pass `--braintrust-experiment`.
