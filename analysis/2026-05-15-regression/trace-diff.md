# Trace-level comparison: baseline `cf6ddb7` vs current `1b61fe3`

**Source**: Braintrust experiments
- Baseline: `ci-benchmark-25268492423` (May 3, 2026 03:18 UTC)
- Current:  `master-25938985537` (May 15, 2026 20:15 UTC)

**Test**: `101_loki_historical_logs_pod_deleted` on `opus-4.6` (k8s variant, the source of regression #101 in the report).

## Per-LLM-call token breakdown

### Baseline (cf6ddb7) — 7 LLM calls

| # | duration | prompt tk | completion tk | cached tk | non-cached tk |
|---|---------:|----------:|--------------:|----------:|--------------:|
| 1 | 11.0s | 16,826 | 918 | 0 | 16,826 |
| 2 | 6.7s  | 19,806 | 515 | 16,816 | 2,990 |
| 3 | 5.8s  | 22,057 | 377 | 18,429 | 3,628 |
| 4 | 4.8s  | 24,216 | 302 | 21,681 | 2,535 |
| 5 | 5.5s  | 26,038 | 249 | 23,932 | 2,106 |
| 6 | 12.7s | 26,768 | 598 | 25,707 | 1,061 |
| 7 | 2.8s  | 974    | 222 | 0 | 974 |
| **Σ** | **49.3s** | **136,685** | **3,181** | **106,565** | **30,120** |

### Current (1b61fe3) — 8 LLM calls (+1)

| # | duration | prompt tk | completion tk | cached tk | non-cached tk |
|---|---------:|----------:|--------------:|----------:|--------------:|
| 1 | 12.0s | 16,784 | 925 | 0 | 16,784 |
| 2 | 9.9s  | 19,740 | 766 | 16,781 | 2,959 |
| 3 | 6.0s  | 21,786 | 401 | 18,360 | 3,426 |
| 4 | 5.3s  | 24,098 | 348 | 21,369 | 2,729 |
| 5 | 12.6s | 25,776 | 854 | 23,718 | 2,058 |
| 6 | 7.8s  | 27,070 | 503 | 25,775 | 1,295 |
| 7 | 11.1s | 28,012 | 679 | 27,069 | 943 |
| 8 | 2.6s  | 931    | 223 | 0 | 931 |
| **Σ** | **67.3s** | **164,197** | **4,699** | **133,072** | **31,125** |

### Key observation

**Per-call sizes are nearly identical** (call 1: 16,826 vs 16,784 — current is even slightly smaller).
The regression is **entirely from one extra LLM call** that re-sends the ~27K-cached-token prefix.
Non-cached input is flat (30,120 → 31,125, +3.3%) — the *new* content the model emits per turn is similar.

## System prompt diff (`+ added`, `- removed`)

Total size: **43,248 → 41,863 chars** (-3.2%, the prompt got smaller despite the regression).

### Big behavioral changes

**1. Removed the "MUST use tools first" directive**

```diff
  You are HolmesGPT version dev-unknown, a tool-calling AI assist...
- Whenever possible you MUST first use tools to investigate then answer the question.
  Ask for multiple tool calls at the same time as it saves time for the user.
```

**2. Added new "Skill Usage" section that injects a skill-fetch step at the top of every investigation**

```diff
+ # Skill Usage:
+ If a skill in the catalog clearly matches the issue being investigated, fetch it using the `fetch_skill` tool before diving into other tools.
+ Only fetch skills that are relevant to the specific issue — do not fetch skills speculatively or "just in case".
+ If no skill matches, skip this step and investigate directly with available tools.
+ 
+ After fetching a skill, read the content returned in the tool's data field and follow its steps.
+ Skill content takes priority over general investigation steps.
```

**3. Phase 1 reordered — skill check is now step 1, TodoWrite demoted to step 2**

```diff
  ## Phase 1: Initial Investigation
- 1. **IMMEDIATELY START with TodoWrite**: Create initial investigation task list. Already start working on tasks. Mark the tasks you're working on as in_progress.
- 2. **Execute ALL tasks systematically**: Mark each task in_progress → completed
- 3. **Complete EVERY task** in the current list before proceeding
+ 1. **Check for matching skills**: If a skill in the catalog clearly matches the issue, fetch it first. Otherwise, skip this step.
+ 2. **Start with TodoWrite**: Create initial investigation task list
+ 3. **Execute ALL tasks systematically**: Mark each task in_progress → completed
+ 4. **Complete EVERY task** in the current list before proceeding
```

**4. Deleted the whole "If investigating Kubernetes problems" section (~14 lines of concrete kubectl guidance)**

```diff
- # If investigating Kubernetes problems
- 
- * run as many kubectl commands as you need to gather more information, then respond.
- * if possible, do so repeatedly on different Kubernetes objects.
- * for example, for deployments first run kubectl on the deployment then a replicaset inside it, then a pod inside that.
- * when investigating a pod that crashed or application errors, always run kubectl_describe and fetch the logs
- * Do check both the status of the kubernetes resources and the application runtime as well, by investigating logs
- * do not give an answer like "The pod is pending" as that doesn't state why the pod is pending and how to fix it.
- * do not give an answer like "Pod's node affinity/selector doesn't match any available nodes" because that doesn't include data on WHICH label doesn't match
- * if investigating an issue on many pods, there is no need to check more than 3 individual pods in the same deployment.
- * if the user says something isn't working, ALWAYS:
- ** use kubectl_describe on the owner workload + individual pods and look for any transient issues they might have been referring to
- ** look for misconfigured ingresses/services etc
- ** check the application logs because there may be runtime issues
```

This content was extracted into the `kubernetes-troubleshooting` skill (PR #2040). The trade-off: the LLM no longer has the guidance inline, so it has to fetch the skill — adding an extra tool call.

**5. Example became generic — concrete tool names removed**

```diff
  ## Examples
  
- User: Why did the webserver-example app crash?
- (Call tool kubectl_find_resource kind=pod keyword=webserver`)
- (Call tool kubectl_previous_logs namespace=demos pod=webserver-example-1299492-d9g9d # this pod name was found from the previous tool call)
+ User: Why did the checkout service crash?
+ (Call a discovery tool to locate the failing resource)
+ (Call a logs tool to read the most recent error output)
```

**6. The `# MANDATORY Task Management` block was moved earlier in the prompt** (no content change — only repositioned). Without "use tools first", this becomes the primary forcing function, and it puts TodoWrite ahead of investigation.

## User prompt diff

Size: **1,008 → 2,176 chars** (+116%). One new section added:

```diff
+ # Skill Selection
+ 
+ You (HolmesGPT) have access to skills with step-by-step troubleshooting instructions.
+ If one of the following skills relates to the user's issue or matches one of the alerts or symptoms listed in the skill entry, fetch it with the fetch_skill tool. Only fetch skills that clearly match — do not fetch speculatively.
+ You (HolmesGPT) must follow skill sources in this priority order:
+ 1) Skill Catalog (priority #1)
+ ## Skill Catalog (priority #1)
+    Here are local skills:
+    * kubernetes-troubleshooting | description: Troubleshoot Kubernetes issues.
+ 
+ 
+ If a skill clearly matches the user's issue:
+ 1. Fetch the skill with the `fetch_skill` tool.
+ 2. Decide based on the skill's contents if it is relevant or not.
+ 3. If it seems relevant, inform the user that you accessed a skill and will use it to troubleshoot the issue.
+ 4. To the maximum extent possible, follow the skill instructions step-by-step.
+ 5. Provide a detailed report of the steps you performed, including any findings or errors encountered.
+ 6. If a skill step requires tools or integrations you don't have access to, tell the user that you cannot perform that step due to missing tools.
```

The user prompt now actively *names* the skill catalog entry (`kubernetes-troubleshooting`) and instructs the model to fetch it. That is a strong nudge — in the docker-loki sweep the LLM's first tool call was always `fetch_skill kubernetes-troubleshooting`, which is one extra turn vs baseline.

## Tool-call sequence comparison (test 101, opus-4.6)

| # | Baseline (cf6ddb7) | Current (1b61fe3) |
|---|--------------------|--------------------|
| 1 | TodoWrite          | TodoWrite          |
| 2 | bash (kubectl)     | bash (kubectl)     |
| 3 | fetch_resource_issues_metadata | fetch_resource_issues_metadata |
| 4 | fetch_configuration_changes_metadata | fetch_configuration_changes_metadata |
| 5 | grafana_loki_query | grafana_loki_query |
| 6 | grafana_loki_query | grafana_loki_query |
| 7 | grafana_loki_query | **bash** (extra kubectl describe) |
| 8 | bash (kubectl get all) | grafana_loki_query |
| 9 | bash (kubectl describe) | bash (kubectl describe — duplicate) |
| 10 | grafana_loki_query | grafana_loki_query |
| 11 | fetch_configuration_changes_metadata | grafana_loki_query |
| 12 | grafana_loki_query | grafana_loki_query |
| 13 | grafana_loki_query | grafana_loki_query |
| 14 | TodoWrite (close)  | grafana_loki_query |
| 15 | —                  | TodoWrite          |
| 16 | —                  | TodoWrite (close)  |

| Tool                                  | Δ  |
|---------------------------------------|---:|
| TodoWrite                             | +1 |
| bash                                  |  0 |
| fetch_configuration_changes_metadata  | -1 |
| fetch_resource_issues_metadata        |  0 |
| grafana_loki_query                    | +1 |

Net +1 tool call, +1 LLM call, +18s wall time. Same investigation pattern but with **extra TodoWrite churn** and **one extra Loki query** before answering.

On the docker-loki sweep (where `kubernetes/core` is disabled), the model goes further off-script: it always fetches the `kubernetes-troubleshooting` skill on turn 1, then discovers it can't use kubectl, and tries more Loki queries to compensate. That extra back-and-forth is why the docker sweep showed +36% turns vs the K8s benchmark's +14%.

## Root cause summary

Two PRs in the 14-day window touched this code path:

1. **PR #1970 (`31fa24c`, 2026-05-14)** — "Simplify jinja2 prompts: inline, deduplicate, fix logic"
   - Removed the `MUST first use tools` directive
   - Added the "Skill Usage" preamble
   - Reordered Phase 1 to check skills first, TodoWrite second
   - Moved task-management block earlier in the prompt

2. **PR #2040 (`47149f0`, 2026-05-14)** — "Extract Kubernetes troubleshooting guidance into dedicated skill"
   - Deleted the `# If investigating Kubernetes problems` section from the prompt
   - Created the `kubernetes-troubleshooting` skill
   - Added the `# Skill Selection` block to the user prompt that names this skill

Together these changes:
- Made the system prompt **smaller** (-3.2%) — so cache savings per call ✓
- But also **softer**: no "MUST use tools first" + skill-first ordering → the model deliberates more
- **Externalized concrete K8s guidance** into a skill that has to be fetched — extra tool call
- The total cost is +14% wall clock, +10% USD, +33% total tokens, +36% turns (docker sweep) — same task, more rounds.

## Fix options (in order of effort)

1. **Restore the "MUST first use tools" directive** in `generic_ask.jinja2` — single-line revert.
2. **Reorder Phase 1** back to TodoWrite first, skill-check second/optional — the skill should be opportunistic, not a gate.
3. **Keep an inline 3-line K8s playbook** in the prompt (the most useful 3 of the 14 deleted lines) so the model has a fast path without fetching the skill for every k8s investigation.
4. **Tune the user-prompt skill catalog block**: maybe only include it when k8s toolsets are enabled, since it adds 1.2K chars to *every* call.
