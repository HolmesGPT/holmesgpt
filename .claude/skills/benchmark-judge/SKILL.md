---
name: benchmark-judge
description: This skill should be used when the user asks to "benchmark the judge", "compare LLM judges", "pick a new classifier model", "replace the eval judge", or wants to know how accurate, fast and cheap a candidate CLASSIFIER_MODEL is on real eval outputs.
version: 0.1.0
---

# Benchmarking LLM Judges

Goal: pick the eval judge (`CLASSIFIER_MODEL`) that is most correct, then fastest and cheapest. Every judge, including the current `gpt-4.1`, can be wrong, so ground truth comes from consensus plus your own adjudication, never from one judge.

## Step 1: Replay a benchmark's judge prompts

Pick the latest benchmark with the most iterations from `docs/development/evaluations/history/` (its Braintrust links name the `ci-benchmark-<run_id>` experiment).

```bash
# Judges are model_list names, e.g. the CI secret MODEL_LIST_YAML
export MODEL_LIST_FILE_LOCATION=/path/to/model_list.yaml
poetry run python .claude/skills/benchmark-judge/benchmark_judge.py <run_id> \
  --judges haiku-5.5-medium,gpt-6.1-sol-medium,deepseek-v4-pro,opus-5.5-medium
```

The script sends each candidate the exact prompt the CI judge received (stored on the `scorer` span in Braintrust), and records the CI verdict as `gpt-4.1-ci`. Results go to `/tmp/benchmark-judge/<run_id>/<judge>.jsonl`; reruns only replay missing or failed prompts. Requires `BRAINTRUST_API_KEY`.

## Step 2: Build the ground truth

A case where every judge agrees is taken as truth; the rest are written to `disputed.json`.

- **Disputed cases**: read each `prompt` (EXPECTED ELEMENTS and OUTPUT) and decide A (all elements present) or B yourself, following the prompt's rules. Do not side with the majority or with `gpt-4.1-ci`; the judges' `reasons` are hints, not evidence.
- **Unanimous cases**: also review every unanimous B and a random sample of 20 unanimous A, since all judges can share a mistake.

Write every verdict to a labels file, `{"<case id>": "A" | "B"}`, with the case ids from `disputed.json` / `cases.json`.

## Step 3: Report

```bash
poetry run python .claude/skills/benchmark-judge/benchmark_judge.py <run_id> --labels labels.json
```

Present the accuracy, cost and latency tables (same shape as the benchmark reports) and recommend a judge: lowest FP + FN first (a false pass hides a real regression, so weigh FP above FN), then p95 latency and total cost. Quote one disputed case each judge got wrong to show the failure mode.

To switch judges, set `CLASSIFIER_MODEL` (`vars.CLASSIFIER_MODEL` in `.github/workflows/eval-regression.yaml`). The classifier calls the OpenAI SDK directly, so the new judge needs an OpenAI-compatible endpoint (see `tests/llm/utils/classifiers.py`).
