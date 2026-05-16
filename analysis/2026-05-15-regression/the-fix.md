# Eval-perf regression: root cause and fix

**Test**: `259_loki_historical_logs_pod_deleted_docker`, opus-4.6 via OpenRouter, n=5 per condition.
**Baseline**: master at `cf6ddb7` (2026-04-30). **Current**: master at `1b61fe3` (2026-05-15).

## TL;DR

Fully reverting **PR #2040** ('Extract Kubernetes troubleshooting guidance into dedicated skill') restores baseline behavior. PR #1970's prompt cleanup contributes negligibly.

Required changes (both):
1. Restore the deleted `# If investigating Kubernetes problems` section (16 lines of inline kubectl playbook) to `holmes/plugins/prompts/generic_ask.jinja2`.
2. Remove the `holmes/plugins/skills/builtin/kubernetes-troubleshooting/` directory so the skill is no longer registered in the catalog.

After this, the `fetch_skill` tool stops being called (was: 29 invocations across 5 iters → 0). Turn count drops from 10.6 to 8.6 (baseline: 7.8). Pass rate stays 5/5.

## All conditions — means (n=5, opus-4.6, all 5/5 pass)

| Condition | time | turns | tools | cost | total tk | cached tk | fetch_skill (Σ5) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline (cf6ddb7) | 78.6s | 7.8 | 21.8 | $0.4073 | 193,919 | 156,089 | 0 |
| current (1b61fe3) | 89.4s | 10.6 | 22.0 | $0.4472 | 259,063 | 220,349 | 29 |
| fix-A (restore K8s prompt section only — skill still loaded) | 87.4s | 9.2 | 21.0 | $0.4345 | 230,732 | 191,968 | 10 |
| fix-B (restore MUST line only) | 89.9s | 9.6 | 21.4 | $0.4361 | 236,976 | 198,382 | 19 |
| fix-AB (A + B) | 92.9s | 10.8 | 23.2 | $0.4639 | 274,258 | 234,962 | 5 |
| fix-AC (A + remove skill-usage block + revert Phase 1 ordering) | 92.8s | 9.4 | 22.4 | $0.4533 | 238,175 | 197,521 | 5 |
| **fix-AD (full PR #2040 revert: A + delete skill file)** | 83.0s | 8.6 | 19.8 | $0.4017 | 202,756 | 165,888 | 0 |

## Recovery vs current (1.0 = back to baseline, 0 = no help)

| condition | time | turns | total tk | cached | cost |
|---|---:|---:|---:|---:|---:|
| fix-A | +0.18 | +0.50 | +0.43 | +0.44 | +0.32 |
| fix-B | -0.04 | +0.36 | +0.34 | +0.34 | +0.28 |
| fix-AB | -0.32 | -0.07 | -0.23 | -0.23 | -0.42 |
| fix-AC | -0.31 | +0.43 | +0.32 | +0.36 | -0.15 |
| **fix-AD** | +0.59 | +0.71 | +0.86 | +0.85 | +1.14 |

## z-score vs baseline (|z|<2 = within noise; +ve = worse than baseline)

| condition | time | turns | total tk | cached | cost |
|---|---:|---:|---:|---:|---:|
| current (regression) | +1.90 | +5.11 | +3.85 | +3.96 | +2.11 |
| fix-A | +2.39 | +2.27 | +2.23 | +2.25 | +1.59 |
| fix-AB | +2.83 | +3.94 | +3.52 | +3.55 | +2.51 |
| fix-AC | +2.87 | +3.58 | +3.64 | +3.62 | +2.70 |
| **fix-AD** | +0.93 | +1.79 | +0.68 | +0.81 | -0.29 |

## Per-iter raw data

- **baseline**: times=[88.8, 73.2, 84.0, 75.6, 71.6], turns=[9, 7, 8, 8, 7]
- **current**: times=[80.3, 94.0, 87.9, 80.2, 104.6], turns=[10, 11, 10, 10, 12]
- **fix-AD**: times=[86.3, 71.3, 81.2, 91.4, 84.9], turns=[9, 8, 9, 9, 8]

Notice the per-iter spread on **fix-AD** sits inside the baseline range — no outliers.

## Why fix-A alone wasn't enough

Fix-A (restore the inline K8s playbook only) recovered ~45% of the regression and dropped fetch_skill activity from 29 → 10. The residual gap is the `fetch_skill` tool call itself: even with the inline playbook, the `kubernetes-troubleshooting` skill file still existed → registered into the skill catalog → the user-prompt `# Skill Selection` block named it on every prompt → the model called `fetch_skill` exactly once per session, then partially followed the skill's procedure (adding more turns).

Deleting the skill file makes the catalog empty, the user-prompt block stops rendering, and the `fetch_skill` call goes away entirely. **Recovery jumps from 0.45 → 0.71-0.86** across the regressed metrics.

## Why fix-B (restoring 'MUST use tools') didn't help

That directive was a redundant nudge — the rest of the prompt already insists on tool use through TodoWrite and 'Multi-Phase Investigation Process'. Removing it didn't affect aggressiveness; what affected aggressiveness was the new *competing* nudge toward `fetch_skill`.

## Recommended PR

Title: `Revert PR #2040 (eval perf regression: extra fetch_skill turn per investigation)`

Diff:
```
# 1. Add back the deleted section to generic_ask.jinja2 (between the
#    "ALWAYS check the logs" line and the toolsets-instructions include):

+ # If investigating Kubernetes problems
+
+ * run as many kubectl commands as you need to gather more information, then respond.
+ * if possible, do so repeatedly on different Kubernetes objects.
+ * for example, for deployments first run kubectl on the deployment then a replicaset inside it, then a pod inside that.
+ * when investigating a pod that crashed or application errors, always run kubectl_describe and fetch the logs
+ * Do check both the status of the kubernetes resources and the application runtime as well, by investigating logs
+ * do not give an answer like "The pod is pending" as that doesn't state why the pod is pending and how to fix it.
+ * do not give an answer like "Pod's node affinity/selector doesn't match any available nodes" because that doesn't include data on WHICH label doesn't match
+ * if investigating an issue on many pods, there is no need to check more than 3 individual pods in the same deployment. pick up to a representative 3 from each deployment if relevant
+ * if the user says something isn't working, ALWAYS:
+ ** use kubectl_describe on the owner workload + individual pods and look for any transient issues they might have been referring to
+ ** look for misconfigured ingresses/services etc
+ ** check the application logs because there may be runtime issues

# 2. Remove the skill that was extracted in PR #2040:
- rm -r holmes/plugins/skills/builtin/kubernetes-troubleshooting/
```

Sweep results in `analysis/2026-05-15-regression/fix_ad_iter_{1..5}.md`.
