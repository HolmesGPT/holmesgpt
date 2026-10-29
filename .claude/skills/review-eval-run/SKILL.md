---
name: review-eval-run
description: This skill should be used when the user asks to "review an eval run", "check a benchmark run", "investigate eval results", "are these failures real", or shares a GitHub Actions eval/benchmark run URL or Braintrust experiment and wants to know whether failures are model failures or bad evals.
version: 0.1.0
---

# Reviewing an Eval Run

Goal: separate real model failures from broken evals, harness bugs and judge mistakes before anyone trusts the numbers.

## Step 1: Get the inputs and run the checks

The run's pytest report (`eval_results.json`, in the `eval-results-<run_id>` artifact) is the source of truth for pass/fail, answers, the judge's rationale (`judge_rationale`), provider errors and skill-check failures. Braintrust (experiment `ci-benchmark-<run_id>`) only adds trace links and tool spans: it drops rows, and the script warns which runs it is missing.

```bash
# A run id downloads the artifact (cached under $TMPDIR/review-eval-run/<run_id>/);
# a path to eval_results.json works too.
poetry run python .claude/skills/review-eval-run/review_eval_run.py <run_id> \
  --baseline <previous_run_id | eval_results.json | committed report .md> \
  --exclude-tests 101_loki_historical_logs_pod_deleted,... \
  --model <newest-model> [--min-cache 50] [--braintrust <events.json | experiment>] [--no-braintrust]
```

- **Artifact by hand**: `gh api repos/HolmesGPT/holmesgpt/actions/runs/<run_id>/artifacts` for the id, then `gh api repos/HolmesGPT/holmesgpt/actions/artifacts/<id>/zip > results.zip`. Artifacts expire; a committed report then works as the baseline (`docs/development/evaluations/history/results_*.md`, or `git show <commit>:docs/development/evaluations/fast-benchmark-results.md`) but carries only status and cost per run.
- **Full job log**: `gh run view <run_id> --json jobs` for the job id, then `gh api repos/HolmesGPT/holmesgpt/actions/jobs/<job_id>/logs > job.log`. The GitHub MCP `get_job_logs` returns only the last 5,000 lines.
- **Network**: `api.braintrust.dev` (with `BRAINTRUST_API_KEY`; logging eval runs also needs `www.braintrust.dev`, where the SDK logs in), and the blob host GitHub redirects artifact and log downloads to, `productionresultssa<N>.blob.core.windows.net`. `N` differs per run (`sa2`, `sa6`, `sa11` seen), so allow the wildcard; a 403 "Forbidden" from `gh api .../zip` means that shard is blocked.
- If the run is still in progress, check `gh run view <id> --json status,jobs` first.

The script output is a guideline, not a verdict: it only catches known patterns. Always also open the failing traces in Braintrust, read the rendered prompts and tool outputs, and look for anything the script doesn't check.

## Step 2: Act on each section

**Pass rate and cost**: per model, raw (passed / all runs) and clean (passed / executed runs, without the `--exclude-tests` eval bugs), next to the baseline. Cost is the sum of per-test cost, as in the markdown report; `COST SPIKE` means more than 1.5× the baseline. Models new to this run show `no baseline`. Compare clean rates: raw rates move with provider outages.

**Regressions vs baseline**: tests a model passed in every baseline run and now fails at least once. Read each before calling it a model regression; fixture changes between the two runs show up here too.

**Never-executed runs**: ⚠️ provider errors, ⏱️ timeouts/rate limits, setup failures, skips and crashes before the model answered. They say nothing about the model; report them apart and leave them out of success rates.

**Skill-check failures**: runs failed by the deterministic skill-suggestion check (`fail_reason`), with the judge's own verdict and the suggested skill titles. A `correct` judge verdict means the answer was right and only the suggestion was wrong — report these apart from wrong answers, and decide whether the suggested skill was genuinely bad or the eval's `memories_generated: false` is too strict. Reports from before ROB-1715 show `not recorded`: the check overwrote the judge's score.

**Suspect tests**: each pattern almost always means an eval problem, not a model problem. Only executed runs count.

- `fails for N/M models that ran it` (one iteration per test) — at least half the models fail it. Check the fixture data and whether outputs actually contain the expected answer.
- `fails for every model` — check the harness/feature flags the test depends on (e.g. `enable_todo` vs `DISABLED_BY_DEFAULT` in `holmes/core/prompt.py`).
- `passes only on first iteration` — shared setup state decays between iterations (iterations run ~35 min apart on the same cluster). Compare tool outputs of iteration 1 vs 2: if the same query returns data then `None`, the fixture is broken (e.g. Loki flushing idle streams).
- `no model passes every iteration` — read the failing outputs; flaky setup or an over-strict `expected_output`.

**Prompt caching**: every model should be at or above `--min-cache` (default 50%). `LOW` means the prompt prefix is changing between calls (e.g. timestamps or per-call data early in the system prompt); `NOT REPORTED` means the provider/litellm returns no `cached_tokens` — say so, don't call it a caching bug.

**Tool errors**: high rates usually mean a broken toolset config or port-forward in the fixture; the most frequent error is printed (repeated `Refusing to run this tool call` means the model is looping on one call).

**Failures for `--model`**: for every failure, compare OUTPUT against EXPECTED and the tool calls, then classify:

- **Eval/harness bug** — the data the model needed wasn't there, or a required tool/prompt was missing.
- **Judge false negative** — the output contains the expected answer but was scored 0.
- **Real model failure** — the data was available in tool output and the model missed or misread it.

Quote the specific tool output or line of the answer that proves each verdict.

## Step 3: Report

End with one table: `Test | Verdict (eval bug / judge false negative / real failure) | Evidence`, plus success rates recomputed without the eval-bug tests.

## Other things worth checking

- Latency regressions against the previous benchmark: `compare_with_benchmark` in `tests/llm/utils/braintrust_history.py`.
- Runs that hit `max_steps` or timed out (long duration, no final answer).
- Rendered `system_prompt` in the eval metadata missing a component the test expects.

## Running evals locally

CI builds the model list from the `MODEL_LIST_YAML` secret, which can't be read outside Actions. Locally, write your own and point `MODEL_LIST_FILE_LOCATION` at it. Each top-level key is the model name passed in `MODEL` (comma-separated) and its value is litellm parameters; `{{ env.X }}` is filled from the environment:

```yaml
sonnet-5.5-medium:
  model: bedrock/global.anthropic.claude-sonnet-5-5
  aws_access_key_id: '{{ env.EVALS_AWS_ACCESS_KEY_ID }}'
  aws_secret_access_key: '{{ env.EVALS_AWS_SECRET_ACCESS_KEY }}'
  aws_region_name: us-east-1
  reasoning_effort: medium
kimi-k3:
  model: openrouter/moonshotai/kimi-k3
  api_key: '{{ env.EVALS_OPENROUTER_API_KEY }}'
```

By convention the credentials live in `EVALS_`-prefixed variables so they don't collide with the provider SDKs' own: `EVALS_AWS_ACCESS_KEY_ID` / `EVALS_AWS_SECRET_ACCESS_KEY` (Bedrock Claude models), `EVALS_AZURE_SWEDEN_API_KEY` (Azure gpt-6.1-sol, also the judge), `EVALS_OPENAI_API_KEY`, `EVALS_GEMINI_API_KEY` and `EVALS_OPENROUTER_API_KEY` (deepseek, kimi, glm, qwen). Keep only the benchmark models (`DEFAULT_BENCHMARK_MODELS` in `.github/workflows/eval-benchmarks.yaml`) plus the judge (set `CLASSIFIER_MODEL` to its name), and allow each provider's host: `bedrock-runtime.<region>.amazonaws.com`, `*.cognitiveservices.azure.com`, `api.openai.com`, `generativelanguage.googleapis.com`, `openrouter.ai`.
