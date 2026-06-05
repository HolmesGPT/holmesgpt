# Subagent eval success criteria

Pre-registered, written BEFORE running any further measurement. Locks in
what counts as a win so we don't move the goalposts after seeing numbers.

## Primary bar (the user's goal)

Subagents are accepted as a win iff **all** of these hold:

1. **≥5 evals** show a token-cost reduction of **≥30%** comparing
   subagent-on vs subagent-off, on the same model, with the same toolset
   configuration, averaged over **N=3 independent runs** per (eval, mode)
   to beat single-run variance (~±40%).
2. On those same 5 evals, **accuracy does not drop** vs subagent-off
   (both modes pass in ≥2 of 3 runs, i.e. accuracy_on ≥ accuracy_off).
3. **Regression guardrail**: across the full regression set defined
   below, mean token cost increases by **≤10%** and mean accuracy drops
   by **≤10%**.

If any of (1), (2), (3) fails after N=3, the subagent change is not
accepted as a win and we either iterate or redefine.

## Model / config

- Parent model: `openrouter/anthropic/claude-opus-4.8` (Opus 4.8, per
  user request).
- Subagent model: same as parent (configurable via `SUBAGENT_MODEL` env;
  Haiku-as-subagent variant pre-registered as an alternative
  configuration to evaluate independently if the primary
  Opus-everywhere config fails.)
- Classifier model: `openrouter/openai/gpt-4.1`.

## Eval set (candidates expected to benefit)

### Addendum 2026-06-05: replaced candidate set

The original 8 evals (below) were measured against Opus 4.8 and produced
0/5 wins. Per the user's direction on 2026-06-05 to "write new evals" with
multi-stage flows that exercise the dispatch sweet spot, the candidate
set for the next measurement round is **redefined** to the following 6
evals. All are multi-stage: the parent must do additional reasoning or
follow-up tool calls AFTER extracting the answer from a noisy initial
fetch — so the noisy data would otherwise ride along in parent context.

| Eval (new candidate set)                                  | Multi-stage pattern                                          |
|-----------------------------------------------------------|--------------------------------------------------------------|
| 245_elasticsearch_trace_large_fields                      | Large-fields trace → identify error (single-stage; kept)     |
| 260_loki_log_search_then_user_history                     | 30k Loki lines → find error → re-query for affected user     |
| 261_loki_followup_same_dataset                            | 30k Loki lines → identify CRITICAL → count prior WARN retries|
| 271_elasticsearch_jq_refinement_then_lookup               | Broad ES search → jq refine → cross-index user-session lookup|
| 272_elasticsearch_error_trace_then_user_history           | Trace with huge fields → user_id → count prior failed traces |
| 273_loki_pool_exhausted_then_user_count                   | Loki errors → identify cause → count distinct affected users |

Bar unchanged: ≥5 of the 6 at ≥30% mean cost reduction with non-degraded
accuracy, plus the ≤10% regression-set guardrail.

### Original candidate set (now superseded — kept for record)

These 8 evals were the original strongest dispatch candidates. They
remained valid pre-measurement but failed to demonstrate the win on
single-shot dispatch.

| Eval                                                | Why it should benefit                                  |
|-----------------------------------------------------|--------------------------------------------------------|
| 185_elasticsearch_cross_region_search               | `elasticsearch_get_mapping` on multiple indexes        |
| 188_elasticsearch_mapping_explosion                 | `elasticsearch_get_mapping` on a 523-field index       |
| 190_elasticsearch_cross_service_correlation         | `elasticsearch_get_mapping` + cross-index `_search`    |
| 193_elasticsearch_large_mapping_search              | `elasticsearch_get_mapping` (largest mapping)          |
| 245_elasticsearch_trace_large_fields                | `elasticsearch_get_mapping` + trace search             |
| 101_loki_historical_logs_pod_deleted                | Loki log query returning many lines                    |
| 156_kafka_opensearch_latency                        | OpenSearch search across multiple indexes              |
| 259_loki_historical_logs_pod_deleted_docker         | Loki log query (Docker variant)                        |

## Regression set (for the ≤10% guardrail)

The full regression-tagged eval set in `tests/llm/fixtures/test_ask_holmes/`,
restricted to those runnable in the current sandbox. Currently:
all `regression`-tagged evals except `176_network_policy_blocking_traffic_no_skills`
(unrunnable per `scripts/setup-sandbox-k8s.sh` note: kernel restricts ipset).

## Sandbox capability

All evals in the candidate set above are now runnable in this sandbox:
- K8s evals: K3s-in-Docker via `scripts/setup-sandbox-k8s.sh`.
- Elasticsearch evals: local ES deployment in `es-eval` namespace
  (`docker.elastic.co/elasticsearch/elasticsearch:8.15.0`, security
  disabled, port-forwarded to `localhost:9200`). The eval `before_test`
  scripts auth with `Authorization: ApiKey <dummy>` which ES ignores
  when `xpack.security.enabled=false`.
- Run via: `ELASTICSEARCH_URL=http://localhost:9200 ELASTICSEARCH_API_KEY=dummy`.

## Measurement protocol

1. For each eval × mode (on/off), run 3 independent invocations.
2. Record per-run: cost, total tokens, pass/fail, parent turns,
   subagent turns (if applicable).
3. Compute mean cost and pass rate per (eval, mode).
4. Compute reduction: `1 - mean(cost_on) / mean(cost_off)`.
5. An eval "passes the 30% bar" iff reduction ≥ 0.30 AND
   pass_rate_on ≥ pass_rate_off.

## Decision rules

- Need ≥5 of the 8 dispatch-friendly evals to pass the 30% bar.
- If 5+ pass and regression guardrail holds → accept the subagent change.
- If <5 pass after 3 runs of measurement and 3 iterations of tuning
  (where an "iteration" is a code change to subagent prompt / dispatch
  rules) → conclude the subagent pattern doesn't beat the bar on this
  workload and report honestly.
