# Subagent next-steps spec

Working tracker for the redesign discussed 2026-06-05. Captures the eval
package, the four design options, and the recommended bundle.

## Status

- [x] Eval package planned
- [x] Eval package built (5 new evals: 260, 261, 271, 272, 273)
- [x] Re-pre-register criteria with the new candidate eval set (commit updated `SUBAGENT_EVAL_CRITERIA.md`)
- [x] Baseline established (Opus 4.6, subagent_off, N=1, local sandbox)
- [ ] Iteration 0 (existing dispatch_agent, subagent_on) — running
- [ ] Option chosen
- [ ] Implementation
- [ ] Final measurement vs 5×30% bar

## Opus 4.6 baseline (subagent_off, N=1, local sandbox)

| Eval | Score | Cost |
|------|-------|------|
| 260 | PASS | $0.2891 |
| 261 | PASS | $0.6989 |
| 271 | PASS | $0.3681 |
| 272 | PASS | $0.5974 |
| 273 | PASS | $0.5250 |
| **Total** | **5/5** | **$2.4785** |

Confirms Opus passes all 5 multi-stage evals reliably — accuracy is not the
constraint. The 30%-cost-reduction bar is the bar that matters.

## Iteration 0: existing `dispatch_agent` (subagent_on, N=1)

| Eval | Off | On | Δ | Hit 30%? |
|------|-----|----|----|---------|
| 260 | $0.2891 | $0.2448 | −15.3% | no |
| 261 | $0.6989 | $0.6055 | −13.4% | no |
| 271 | $0.3681 | $0.2876 | −21.9% | no |
| 272 | $0.5974 | $0.5677 | −5.0% | no |
| 273 | $0.5250 | $0.6266 | **+19.4%** | regression |
| Total | $2.4785 | $2.3322 | −5.9% | 0/5 |

All 5 still PASS on accuracy. Existing generic `dispatch_agent` averages
−6% across the candidate set with max −22% — well short of the 30% bar.
273 actively regresses (dispatch overhead > savings on small extracts).

Conclusion: the generic dispatch tool isn't enough. Need purpose-built
subagents (Option A/B) and/or toolset surgery (Option C) to hit 30%.

## Theory recap

Prior measurement (`SUBAGENT_EVAL_FINDINGS.md`) showed 0/5 of the original
candidate evals hit the 30% bar on Opus 4.8. Root cause analysis:

1. Opus 4.8 rarely chose to dispatch (13/15 runs had 0 subagent invocations).
2. When it did dispatch, the noisy tool output it would have stripped was
   small because HolmesGPT toolsets already apply server-side jq filtering.
3. Single-shot evals don't compound the savings — most of the win from
   keeping noise out of parent context only materializes when subsequent
   parent turns reuse cached input that *excludes* the noise.
4. A code-level auto-summarize hook (iter22) caused a real accuracy
   regression on 245 by stripping data the parent needed.

The redesigned eval set targets workloads where:

- Raw tool output is genuinely large (logs / traces).
- A jq filter can't extract the answer declaratively (the question
  determines which records matter, requiring reasoning).
- **Multi-stage flow**: parent needs to do additional reasoning or tool
  calls after extracting the slice — so the noisy data would otherwise
  ride along in parent context for subsequent turns.

## Eval package

Each eval below is designed so the noisy data should stay inside the
subagent. The expected_output verifies both the data extracted *and*
the result of the follow-up step that the parent does with that data.

### New evals (this commit)

| # | Name | Pattern | Multi-stage tail |
|---|------|---------|------------------|
| 260 | `260_loki_log_search_then_user_history` | 30k Loki lines, find specific error, then drill into same user's history | logs → user_id → logs(filter=user_id) |
| 261 | `261_loki_followup_same_dataset` | Two questions over same 30k log set; tests persistent-session benefit | logs → answer-1 → same-logs(no-refetch) → answer-2 |
| 271 | `271_elasticsearch_jq_refinement_then_lookup` | Initial ES search returns 250+ docs; agent must refine via jq and then cross-lookup | broad-search → jq-refine → request_id → es-lookup(other-index) |
| 272 | `272_elasticsearch_error_trace_then_user_history` | Trace large_fields-like, but parent must follow up with es-search for the same user | trace → exception+user_id → es-search(filter=user_id) |

### Existing evals — upgrade for multi-stage tail (this commit)

| # | Existing behaviour | Upgrade |
|---|--------------------|---------|
| 245 | Identifies one trace with a large fields | Add follow-up: identify the user_id from the trace error and search ES for other failed traces by that user (within last hour) |
| 259 | Identifies the DB pool exhaustion error | Add follow-up: count how many distinct users were affected in that hour |
| 191 | Identifies high-fanout aggregation in profile | Keep as-is — already exercises a noisy tool result; multi-stage tail is not a natural fit here |

### Pre-registered candidate set for next measurement round

After this eval package lands, the new candidate set for the 30% × 5
criterion becomes:

1. `245_elasticsearch_trace_large_fields` (upgraded)
2. `259_loki_historical_logs_pod_deleted_docker` (upgraded)
3. `260_loki_log_search_then_user_history` (new)
4. `261_loki_followup_same_dataset` (new)
5. `271_elasticsearch_jq_refinement_then_lookup` (new)
6. `272_elasticsearch_error_trace_then_user_history` (new)

That's 6 candidates against the bar of 5. Bar unchanged: ≥5 of these at
≥30% mean cost reduction with non-degraded accuracy, plus the ≤10%
regression-set guardrail.

## Design options

Each option is a self-contained bundle. Components can mix (Option C is
explicitly a component to layer on A or B). Recommendation in
**Option E** below.

### Option A — LogScout: purpose-built log-search subagent

- New tool: `log_search_subagent` (coexists with current `dispatch_agent`;
  the latter stays off by default).
- Scope: parent invokes for any log search (Loki, Coralogix, Elasticsearch
  logs, kubectl logs). Toolset injected into subagent = only log-fetching
  tools. No metrics, no traces, no `kubectl get pods`.
- System prompt: narrow. "You search logs for specific patterns or
  anomalies. Return timestamps + log level + verbatim matching lines + 3
  lines of context. Never paraphrase logs."
- Subagent model: configurable; recommended `haiku-4.5` when parent =
  `opus-4.8`.
- Caching strategy: stateless by default. Option D layers on top.
- Predicted wins: 260, 263 (if added later), 269, 270.
- Predicted no-op: trace evals, metrics evals.

### Option B — TraceScout: trace-extraction subagent

- New tool: `trace_explorer_subagent`. Toolset = only Tempo / Jaeger /
  OTel tools.
- System prompt: "Extract the relevant span(s). Return span_id, service,
  operation, duration_ms, status, key attributes. Drop everything else."
- Subagent model: `haiku-4.5` default — span filtering doesn't need a
  strong reasoner.
- Predicted wins: 245, 264, 265, 272.
- Highest single-shot dispatch case in the codebase (trace data has the
  worst signal-to-noise of any tool).

### Option C — Toolset surgery: hide raw noisy tools from parent

- **Not a standalone — layers on A or B.**
- Idea: when `subagents_enabled=True`, hide certain large-output tools
  from the parent's registry. Parent's only access path = the subagent.
- Specific candidates to hide: `get_full_trace`, large-window
  `grafana_loki_query`, large-window `coralogix_logs`.
- Removes the meta-decision: parent can't bypass the subagent because
  the raw tool isn't in its registry. Opus's reluctance to dispatch
  becomes irrelevant.
- Risk: if subagent returns NO_DATA the parent has no fallback. We need
  either (a) a parent-visible fallback tool that hits the same backend
  with mandatory tight filters, or (b) the subagent retries on its own.

### Option D — Persistent subagent sessions

- **Caching layer; layers on A, B, or current dispatch_agent.**
- On first dispatch, generate a `session_id` UUID. Park the subagent's
  `ToolCallingLLM` instance (with full message history) in an in-memory
  LRU map. TTL ~5 min, max ~10 sessions.
- New parent tool: `continue_subagent_session(session_id, new_question)`.
  Appends a user message to the subagent's existing history. Subagent
  prompt cache (server-side, ~5 min TTL) stays hot — matches the LRU TTL.
- Cost math: follow-up question that would otherwise cost a full re-fetch
  (~$0.20 on Opus subagent / ~$0.02 on Haiku) now costs ~$0.03 / $0.005.
- Predicted wins on follow-up evals: 261, 269, 270.
- Risk: state management; sessions must be request-scoped to avoid
  cross-conversation leakage.

### Option E — Recommended bundle: A + B + C (scoped) + D

The 4 standalones each won't hit 5×30% on their own. Combining them is
the recommended path:

1. **A + B**: purpose-built LogScout and TraceScout, both narrow.
2. **C scoped**: hide only `get_full_trace` and `grafana_loki_query_large_window` from parent. Existing simple cases stay fast (no extra hop).
3. **D**: persistent sessions for follow-up evals.

Predicted hit list against the bar:

- 245 (B+C): 40-50% reduction (full trace currently dominates)
- 260 (A+D): 50-70% reduction (30k log lines stripped)
- 261 (A+D): 60-80% on the follow-up turn
- 264 (B+C — new eval if we add it): similar to 245
- 271 (B-style ES subagent, see Option F): jq dance kept inside subagent
- 272 (A+D): trace + cross-lookup pattern

That's 5-6 evals where I expect ≥30% reduction with non-degraded
accuracy.

### Option F — ESScout (potential addition)

Not in the original 4. ESScout would be a purpose-built ES-search
subagent: parent dispatches with the question + a starting index
pattern, subagent iterates jq/refine internally, returns just the
identified document(s). Natural fit for eval 271.

Whether to add F depends on whether the ES toolset's existing jq
support is enough to handle 271 with a generic dispatch_agent, or
whether a purpose-built subagent is needed. To be decided after
running 271 against the existing dispatch_agent first.

## Decision pending

User to choose which package to build next:

- **A only** (LogScout)
- **B only** (TraceScout)
- **A + B**
- **A + B + C scoped**
- **A + B + D** (no toolset surgery)
- **E = A + B + C scoped + D** (recommended)
- Also: include F (ESScout)?

## Open questions

- Subagent model for non-Anthropic parent (e.g. gpt-5.4 parent): does
  Haiku-as-subagent still help when parent isn't Anthropic? Cross-provider
  prompt caching is messy. Probably yes if subagent provider matches
  subagent model.

## Sandbox notes

- For local opus runs through OpenRouter, the litellm pricing key is
  `openrouter/anthropic/claude-opus-4.6` (dots, not dashes; `openrouter/`
  prefix, not `openai/`). Using `openai/anthropic/claude-opus-4.6` silently
  loses cost tracking ("Total Cost: —" in the eval report). The classifier
  (gpt-4.1) still needs the `openai/` prefix with explicit `api_base` so the
  raw OpenAI SDK that autoevals uses can call OpenRouter.
- Anthropic prompt cache TTL is 5 min standard / 1 hr extended. For
  Option D, do we explicitly use the 1-hour TTL? Adds cost on cache
  write but extends the session lifetime benefit.
- Concurrency: if two evals run in parallel and both dispatch a
  log_search_subagent for the same session_id (collision), what happens?
  Need session_ids to be UUIDs and request-scoped.
