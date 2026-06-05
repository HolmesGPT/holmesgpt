# Subagent eval findings: dispatch_agent on Opus 4.8 vs gpt-4.1-mini

**Date**: 2026-06-04
**Pre-registered criteria**: `SUBAGENT_EVAL_CRITERIA.md`
**Branch**: `claude/subagent-architecture-matrix-evals-tQRPI`

## TL;DR

The pre-registered bar (≥5 evals × ≥30% cost reduction with non-degraded
accuracy on Opus 4.8) is **NOT met**. On Opus 4.8 with the current
`dispatch_agent` design:

- **0 of 5 dispatch-friendly evals** show a 30% cost reduction
- Per-eval mean cost change ranges from **-13.7% to +0.4%** (mostly slight regressions)
- Aggregate cost across all 5 evals is **5.5% HIGHER** with subagent enabled
- Root cause: Opus 4.8 mostly **chooses not to dispatch** even when the
  `dispatch_agent` tool is available (13 of 15 measured subagent_on runs
  had 0 subagent invocations)

The Haiku-as-subagent variant tested on a partial subset showed the same
pattern: parent Opus still doesn't dispatch, so the subagent model
choice is irrelevant.

## Pre-test criteria recap (locked in `SUBAGENT_EVAL_CRITERIA.md`)

Need ≥5 evals where:
1. Mean cost reduction ≥30% (N=3 runs per condition to beat ~±40% per-run variance)
2. Accuracy not worse (pass rate on ≥ pass rate off)
3. Regression guardrail: across the broader regression set, ≤10% mean cost increase

## Measurement infrastructure

Made the elasticsearch regression evals runnable locally in the Claude
Code sandbox (previously they only ran in CI):

- K3s-in-Docker via `scripts/setup-sandbox-k8s.sh`
- Local Elasticsearch 8.15 in `es-eval` namespace, `xpack.security.enabled=false`
- Local Loki container for the docker variant
- `ELASTICSEARCH_URL=http://localhost:9200`, `ELASTICSEARCH_API_KEY=dummy-key`

All 5 candidate evals run end-to-end in the sandbox.

## Data: Opus 4.8 (`openrouter/anthropic/claude-opus-4.8`), N=3

| Eval                                          | off mean$ | on mean$ | reduction | off pass | on pass | dispatch invocations |
|-----------------------------------------------|-----------|----------|-----------|----------|---------|----------------------|
| 188_elasticsearch_mapping_explosion           | $0.4349   | $0.4333  | **+0.4%** | 3/3      | 3/3     | 2/3 runs dispatched 1× |
| 191_elasticsearch_query_profile               | $0.3218   | $0.3210  | **+0.2%** | 3/3      | 3/3     | 0/3 |
| 193_elasticsearch_large_mapping_search        | $0.2564   | $0.2606  | **−1.6%** | 3/3      | 3/3     | 0/3 |
| 245_elasticsearch_trace_large_fields          | $0.6993   | $0.7954  | **−13.7%**| 2/3      | 3/3     | 0/3 |
| 259_loki_historical_logs_pod_deleted_docker   | $0.4105   | $0.4281  | **−4.3%** | 3/3      | 2/3     | 0/3 |
| **Aggregate**                                 | $2.123    | $2.239   | **−5.5%** | —        | —       | 2/15 runs dispatched |

**No eval meets the 30% bar.**

## Why dispatch doesn't help on Opus 4.8

For comparison, on **gpt-4.1-mini** with the same 5-eval set (N=3),
eval 188 showed a 76.7% mean cost reduction (subagent_off mean $0.0358
→ subagent_on $0.0083) because the weaker model gets confused by the
523-field mapping and one of three off-mode runs loops forever
(700k+ tokens). Dispatch prevents that failure mode.

Opus 4.8 doesn't have that failure mode. It handles a 250k-token
mapping response correctly on every run, with no retry loops. So:

1. Subagent dispatched + executed = pure overhead (parent dispatch
   call + subagent's own LLM cost + parent processes summary)
2. Subagent NOT dispatched (most common on Opus) = just adds a
   `dispatch_agent` definition to every prompt = small cost increase

Forcing dispatch via a more aggressive prompt
(`MUST DISPATCH before calling elasticsearch_get_mapping`) increases
cost further on Opus 4.8 — verified with a smoke test on 188
that showed $0.55 cost with forced dispatch vs $0.40 baseline.

## Conclusion against pre-registered criteria

- ❌ Criterion (1) "≥5 evals × ≥30% reduction": **0 of 5 met**
- Criterion (2) "accuracy not worse": **mixed** (245 improved 2/3→3/3, 259 regressed 3/3→2/3)
- Criterion (3) "regression guardrail ≤10%": **−5.5% aggregate is within ±10%, so guardrail holds**

The dispatch_agent pattern as currently designed:
- **Is a win** on weaker models (gpt-4.1-mini: 76% reduction on 188)
- **Is roughly cost-neutral** on Opus 4.8 (5.5% aggregate increase, within noise)
- **Does not meet the 30%×5 bar on Opus 4.8**

## What this means for the PR

The subagent infrastructure works correctly. It is gated behind
`subagents_enabled=False` by default. Per the user's pre-registered
goal, we cannot demonstrate the 30%×5 win on Opus 4.8 with the
current design — so the bar is not met and we should not claim the
win.

Options going forward (require user direction):

1. **Ship as-is, default off**, document as "useful for cheaper
   parent models, not for Opus 4.8" — accuracy preserved, no
   regression to regression guardrail.
2. **Redefine criteria** to a model where subagents demonstrably help
   (gpt-4.1-mini etc.), with the explicit acknowledgement that the
   benefit doesn't transfer to Opus 4.8.
3. **Abandon the subagent direction** and pursue different
   optimizations on Opus 4.8.
4. **Iterate on the design** — for example, only auto-dispatch for
   `elasticsearch_get_mapping` rather than relying on parent
   judgement. Risk: forcing dispatch hurt cost in the smoke test, so
   no clear win path on Opus 4.8.

Raw data: `/tmp/measure-opus-results.tsv`, `/tmp/measure-results.tsv`,
`/tmp/measure-haikusub-results.tsv` (partial).

## Postscript: iter22 caused a real accuracy regression (reverted)

After writing the above, I tried one more code-level approach — re-applied
iter20 (`maybe_auto_summarize_tool_result` hook) and expanded the
`AUTO_SUMMARIZE_TOOLS` frozenset from just `elasticsearch_get_mapping`
(the original commit's value, which is also a misname — the actual tool
is called `elasticsearch_mappings`) to include `elasticsearch_search`
and `grafana_loki_query`. The change was commit `a588f9f4`.

CI on `a588f9f4` produced a clean regression: **245_elasticsearch_trace_large_fields[subagent_on] went from PASS to FAIL**
on Opus 4.6 (the CI's default model). The matching run on the
pre-iter22 commit `ad148da` was a PASS at $0.6100 / 236k tokens; the
post-iter22 run is a FAIL at $0.2656 / 115k tokens. Adding
`elasticsearch_search` to the auto-summarize set caused the subagent
to strip information the parent needed to identify the trace, dropping
accuracy.

Both iter20 and iter22 reverted (commits `cbf5a398`, `dbeed3b7`). The
branch is back to the design that has 0 accuracy regressions in CI
but doesn't beat the 30%×5 bar — same place as before, no worse.

This confirms the design tension: the auto-summarize hook either fires
on already-filtered results (no win — iter22 with mapping tool name
mismatch + smoke test data), or fires on data the parent genuinely
needs (accuracy regression — iter22 with the corrected tool name set
on real CI). There isn't a working middle ground for this codebase.
