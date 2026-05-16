# Candidate-fix sweep: which prompt change fixes the regression?

**Test**: `259_loki_historical_logs_pod_deleted_docker`, opus-4.6 via OpenRouter, n=5 per condition.

**Conditions**:
- **baseline**: master at `cf6ddb7` (2026-04-30)
- **current**:  master at `1b61fe3` (2026-05-15) — has the regression
- **fix-B**: current + restore the one-liner `Whenever possible you MUST first use tools to investigate then answer the question.`
- **fix-A**: current + restore the `# If investigating Kubernetes problems` section (~14 lines) that PR #2040 moved into the `kubernetes-troubleshooting` skill

All 20 runs passed correctness (5/5 × 4 conditions).

## Means (n=5)

| metric | baseline | current | fix-B | fix-A |
|--------|--------:|--------:|------:|------:|
| time | 78.6s | 89.4s | 89.9s | 87.4s |
| turns | 7.8 | 10.6 | 9.6 | 9.2 |
| tools | 21.8 | 22.0 | 21.4 | 21.0 |
| cost | $0.4073 | $0.4472 | $0.4361 | $0.4345 |
| total_tokens | 193,919 | 259,063 | 236,976 | 230,732 |
| cached | 156,089 | 220,349 | 198,382 | 191,968 |

## Δ vs current (% — negative = fix moves toward baseline)

| metric | baseline | fix-B | fix-A |
|--------|---------:|------:|------:|
| time | -12.0% | +0.5% | -2.2% |
| turns | -26.4% | -9.4% | -13.2% |
| tools | -0.9% | -2.7% | -4.5% |
| cost | -8.9% | -2.5% | -2.8% |
| total_tokens | -25.1% | -8.5% | -10.9% |
| cached | -29.2% | -10.0% | -12.9% |

## Recovery toward baseline (1.0 = fully restored, 0 = unchanged)

| metric | fix-B | fix-A |
|--------|------:|------:|
| time | -0.04 | 0.18 |
| turns | 0.36 | 0.50 |
| total_tokens | 0.34 | 0.43 |
| cached | 0.34 | 0.44 |
| cost | 0.28 | 0.32 |

## `fetch_skill` invocations across 5 iters

| condition | log lines mentioning `fetch_skill` or `kubernetes-troubleshooting` |
|-----------|---:|
| baseline | 0 |
| current  | 29 |
| fix-B    | 19 |
| fix-A    | 10 |

## Verdict

**Neither fix on its own fully restores baseline.** Both help, in roughly the same direction:

- Fix-A (restore K8s section) recovers ~10–15% of each regressed metric (turns, tokens, cost) and cuts skill-fetch chatter by ~65%.
- Fix-B (restore the "MUST use tools first" one-liner) recovers ~10% on turns/tokens with no measurable wall-clock improvement, and only cuts skill-fetch chatter by ~35%.
- The regression is roughly **2.5× bigger** than either fix alone can close. The remaining gap likely comes from the user-prompt `# Skill Selection` block (added by PR #2040) that *names* the `kubernetes-troubleshooting` skill — both fixes leave that block untouched, so the model still considers fetching the skill once per session.

## Suggested follow-up

1. Apply **fix-A + fix-B together** and re-measure — most likely closes ~70-80% of the gap.
2. To fully close it, either:
   - gate the user-prompt skill catalog block on `skill_count > 0` AND `task is K8s-shaped`, or
   - drop the skill nudge entirely if the inline K8s playbook is restored (it duplicates the same guidance).
3. Land both fixes as a single PR titled "Revert regression in eval perf from #1970 + #2040".
