---
name: review-eval-run
description: This skill should be used when the user asks to "review an eval run", "check a benchmark run", "investigate eval results", "are these failures real", or shares a GitHub Actions eval/benchmark run URL or Braintrust experiment and wants to know whether failures are model failures or bad evals.
version: 0.1.0
---

# Reviewing an Eval Run

Goal: separate real model failures from broken evals, harness bugs and judge mistakes before anyone trusts the numbers.

## Step 1: Run the checks

```bash
# Accepts a GitHub run id (-> ci-benchmark-<id>) or a full experiment name
poetry run python .claude/skills/review-eval-run/review_eval_run.py <run_id> --model <newest-model> [--min-cache 50]
```

Requires `BRAINTRUST_API_KEY`. If the run is still in progress, check `gh run view <id> --json status,jobs` first; Braintrust rows appear as tests finish.

## Step 2: Act on each section

**Prompt caching**: every model should be at or above `--min-cache` (default 50%). `LOW` means the prompt prefix is changing between calls (e.g. timestamps or per-call data early in the system prompt); `NOT REPORTED` means the provider/litellm returns no `cached_tokens` — say so, don't call it a caching bug.

**Suspect tests**: each pattern almost always means an eval problem, not a model problem:

- `fails for every model` — check the harness/feature flags the test depends on (e.g. `enable_todo` vs `DISABLED_BY_DEFAULT` in `holmes/core/prompt.py`) and whether outputs actually contain the expected answer.
- `passes only on first iteration` — shared setup state decays between iterations (iterations run ~35 min apart on the same cluster). Compare tool outputs of iteration 1 vs 2: if the same query returns data then `None`, the fixture is broken (e.g. Loki flushing idle streams).
- `no model passes every iteration` — read the failing outputs; flaky setup or an over-strict `expected_output`.

**Unscored runs / tool errors**: unscored rows are setup failures, timeouts or crashes — count them separately from model failures. High tool-error rates usually mean a broken toolset config or port-forward in the fixture.

**Failures for `--model`**: for every failure, compare OUTPUT against EXPECTED and the tool calls, then classify:

- **Eval/harness bug** — the data the model needed wasn't there, or a required tool/prompt was missing.
- **Judge false negative** — the output contains the expected answer but was scored 0.
- **Real model failure** — the data was available in tool output and the model missed or misread it.

Quote the specific tool output or line of the answer that proves each verdict.

## Step 3: Report

End with one table: `Test | Verdict (eval bug / judge false negative / real failure) | Evidence`, plus success rates recomputed without the eval-bug tests.

## Other things worth checking

- Cost/latency regressions against the previous benchmark: `compare_with_benchmark` in `tests/llm/utils/braintrust_history.py`.
- Runs that hit `max_steps` or timed out (long duration, no final answer).
- Rendered `system_prompt` in the eval metadata missing a component the test expects.
