| Authors | Naomi |
| --- | --- |
| Review Date | 28 / Jul / 2026 |
| Participants | AI/Holmes team, Backend (relay), Frontend |

Ticket: [ROB-723](https://linear.app/robusta/issue/ROB-723) — "Using Code mode
instead of scripts may save our token cost for tool calls." Project: *Minimize
Token Cost for us (and users)*.

Related reading:

- `2026-06-10_remote-tool-execution.md` (relay) — the `expose_remotely` /
  `is_core` markers and the **pre-approved-only, no-approval-round-trip** trust
  model. Code mode reuses the same posture.
- `2025-10-23_holmes-event-driven-architecture.md` (relay) — the SSE event
  contract (`start_tool_calling` / `tool_calling_result`) that carries tool
  calls to the UI, and relay's role as a transparent pipe.

# Introduction

## Overview

Holmes investigates by running an **agentic tool-calling loop**: the LLM emits a
tool call, Holmes runs it, appends the result to the conversation, and re-sends
the whole growing history on the next step. Complex investigations run tens of
tool calls across many steps, and because every step re-sends all prior tool
results, input tokens grow super-linearly with the number of steps.

**Code mode** ([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp),
[Cloudflare](https://blog.cloudflare.com/code-mode/)) replaces "many small tool
calls, one per LLM step" with "the LLM writes **one Python script** that composes
many tool calls, runs it, and returns only the **filtered result** to the
model." Tool output the model never needs is filtered *inside the script* and
never enters the context window.

This design adds a `code_execution` toolset whose single tool, `run_python_code`,
runs an LLM-written Python script in a subprocess. The script is given a
generated `holmes` client — one Python function per tool the request already has
— and each call is relayed back to the parent Holmes process, which runs the
real tool through the existing `ToolExecutor`. So tools behave identically
whether called directly or from a script, credentials stay in the parent, and
the agentic loop is unchanged: `run_python_code` is just another tool call. The
feature is off by default.

Stakeholders: AI/Holmes team (runtime + prompting), backend/relay (SSE
passthrough — no relay change), frontend (nested sub-call rendering, a later
increment).

## Glossary

- **Code mode** — the execution path where the LLM writes Python composing tool
  calls, instead of emitting one function call per LLM step.
- **`code_execution` toolset / `run_python_code` tool** — the new toolset and its
  single tool (this design), modeled on the existing `bash` toolset.
- **client (`holmes`)** — an auto-generated object injected into the script's
  namespace, exposing one function per eligible tool (`holmes.kubectl_get(...)`,
  `holmes.list_events(...)`). Each function relays the call to the parent.
- **bridge** — the parent-side server that receives a script's tool-call requests
  over a unix-domain socket, checks them against the allow-list, dispatches into
  `ToolExecutor`, and streams the result back.
- **sub-call** — a tool invoked from inside a running script (versus a top-level
  tool call the LLM emits directly).
- **the two token sinks** — (1) *tool-definition overhead*: all tool schemas
  loaded into context up-front; (2) *intermediate-result bloat*: every tool
  result re-sent through context on each subsequent step. Sink (2) is the one
  that matters for Holmes (see Background).
- **spill-to-disk** — the existing size guard that saves an oversized tool
  result to a file and hands the model a `cat … | jq` pointer instead of the
  payload.
- **`is_core`** — a toolset marker (from remote-tool-execution) for internal
  agent-loop machinery (`robusta_platform_mcp`, `core_investigation`/`TodoWrite`,
  `skills`); such toolsets are never exposed to remote callers or to code mode.

## Background

- **Where the money is (30-day prod).** ~$23k/mo total Holmes spend; **82.6%
  overall prompt-cache hit** (89–94% on the expensive buckets). Cost is ~99%
  *input* tokens. Spend rises with step count in lockstep with tool-call count
  (4–6 iters → ~10 calls, 7–10 → ~19, 11–20 → ~30, 21–50 → ~55). Requests with
  **≥6 iterations are ~$15.3k/mo (66.5% of spend)** — the multi-tool-chaining
  regime code mode targets.
- **Sink (1) is already neutralized by prompt caching.** The static prefix
  (system prompt + all tool schemas) is identical every step and is the most
  cacheable content, so at 82.6% cache hit the "don't load all tool defs
  up-front" half of the headline 98% figure buys almost nothing in dollars. The
  win has to come from **sink (2): fewer results entering context, and fewer
  steps.** So this design prioritizes result-filtering and call-consolidation;
  progressive tool-def disclosure is out of scope.
- **Holmes already has a proto-code-mode: the `bash` toolset.** It runs shell
  (`kubectl`/`jq`/`grep`) with prefix allow/deny validation, and `spill-to-disk`
  already keeps oversized payloads out of context. Code mode captures the
  *residual* between "compose CLI tools in bash" and "compose **all** toolsets
  (Prometheus, Grafana, Datadog, k8s, …) in one typed Python script." Because
  bash is the closest existing analog, this doc compares the two directly under
  [Code mode vs. bash mode](#code-mode-vs-bash-mode).
- **Not everything is addressable.** The largest uncached-cost bucket is short
  `user_chat` (1–3 iters, cold cache) — code mode does nothing for it. v1 scopes
  to multi-iteration, tool-heavy traffic.

## Goals

User stories:

1. Holmes answers "which of the 200 pods in namespace X restarted in the last
   hour, and why?" by writing one script that lists pods, filters to the
   restarted ones in Python, fetches logs only for those, and returns a short
   summary — instead of many LLM steps each re-sending the full pod list.
2. A script that pulls a 10,000-series Prometheus range and returns only the top
   3 offenders keeps the other 9,997 series out of the model's context.
3. The user watching the Ask-Holmes UI still sees each sub-call as its own tool
   card, plus the script that drove them — code mode is not an opaque box.
4. A trivial single-tool ask ("is the cluster healthy?") is **not** made slower
   or costlier — the model keeps using direct tool calls for simple work.

Technical requirements:

- One new toolset `code_execution` with one tool `run_python_code`, off by
  default behind a feature flag.
- A script's tool calls go through the **same** `ToolExecutor`, validation,
  transformers, and result-size guard as direct calls — no bypass.
- **Read-only / pre-approved surface only.** A synchronous script cannot pause
  for interactive approval, so approval-gated tools are denied inside a script;
  only read-only / pre-approved tools are callable. `is_core` toolsets are never
  exposed.
- **Credentials never enter the untrusted subprocess.**
- Script stdout over the per-tool cap flows through `spill-to-disk`, so a runaway
  script can't flood context.
- Works on any model/provider — it is an ordinary tool call, no special API.

## Out of Scope

- **Interactive approval inside a script.** No approval round-trip; approval-gated
  tools are denied at call time, and the model falls back to a direct tool call
  to trigger the normal approval flow.
- **A language-level or OS sandbox** in v1 (RestrictedPython / gVisor / container
  isolation). v1's isolation is subprocess + `ulimit` + the tool allow-list; real
  sandboxing is a Future Goal and the gating decision for broad rollout (see
  [Security model](#security-model)).
- **Progressive disclosure of tool definitions** — deprioritized because caching
  already neutralizes sink (1).
- **Writing/mutating tools from a script** — read-only tools only.
- **Persisted "skills"** (saving successful scripts as reusable functions).

## Future Goals

- Language-level / OS sandboxing, to allow a broader tool set and to make the
  feature safe under untrusted input.
- Auto-routing: a cheap classifier that pre-selects code mode vs. direct calls
  per request, instead of leaving it to the model + prompt.
- Live per-sub-call streaming to the UI (nested tool cards) — see Observability.
- Extending code mode across clusters by composing the remote tools from
  `2026-06-10_remote-tool-execution.md` inside a script.

## Assumptions & Constraints

- Holmes runs in-cluster with live credentials and RBAC. Executing
  LLM-generated Python is a materially larger surface than prefix-validated bash;
  the design constrains what a script can *reach*, not what Python it can run
  (see [Security model](#security-model)).
- **Relay is a transparent SSE pipe** — it forwards Holmes's SSE bytes verbatim
  and keys tool calls by `tool_call_id`. So sub-calls become observable purely by
  Holmes emitting them as normal SSE events; no relay change is needed.
- The per-tool result cap is `min(15% of context, 25k tokens)`; compaction fires
  at 95% (rare). Code mode does not change these; its output feeds through them.

# Design

## Current Design

Holmes's agentic loop assembles the conversation, calls the LLM with the full
tool-schema set, executes any returned tool calls in a bounded thread pool,
appends each result back into the conversation, and repeats until the model
answers. Every tool result therefore lives in the context and is re-sent on
every later step.

Two existing pieces matter for this design:

- **The `bash` toolset** runs a shell command in a subprocess with a `ulimit`
  memory cap and a wall-clock timeout, validating the command against a prefix
  allow/deny list and returning `APPROVAL_REQUIRED` for anything not
  pre-approved. It is Holmes's existing "compose tools in code" path, limited to
  CLI tools.
- **The result-size guard (`spill-to-disk`)** intercepts any oversized tool
  result and replaces it with a file pointer so it can't flood the context.

Both are reused unchanged by code mode. The gap they leave: tools compose only
one-per-step through the model (or, for bash, only CLI tools), so intermediate
results flow through the context repeatedly.

## Proposed Design

### The `run_python_code` tool

A new toolset `code_execution` with a single tool:

- **Parameters:** `code` (the Python script) and optional `timeout` (default 60s,
  clamped to ≤300s).
- **Behavior:** the tool generates the `holmes` client for the request's eligible
  tools, writes the script plus a small bootstrap to a temp file, runs it as a
  `python3` subprocess (reusing bash's `ulimit` memory cap and timeout), captures
  stdout/stderr into a `StructuredToolResult`, and passes that result through
  `spill-to-disk`. The result also carries a short footer listing each sub-call
  (name / status / timing) for traceability.
- **Config:** `enabled=False` by default; `is_core=False`; not exposed remotely.
  Its `llm_instructions` teach the model *when* to use code mode (multi-step /
  large-result tasks) and *when not to* (trivial asks).

The agentic loop needs **no change** — `run_python_code` is an ordinary tool
call, so parallel execution, the result-feedback path, and compaction all apply
unchanged.

### The generated `holmes` client

For each request, Holmes generates a `holmes` object with one function per
**eligible** tool. A function's signature is derived from the tool's parameter
schema and its docstring from the tool's description; the body relays the call to
the parent and returns the tool's data (raising a catchable `holmes.HolmesToolError`
on a tool error, so a script can `try/except`).

**Eligibility** (the allow-list) excludes:

- `is_core` toolsets (agent-loop machinery);
- the `bash` and `kubectl_run` toolsets (mutation / shell surfaces) and the
  `code_execution` toolset itself (no recursion);
- any tool matching its toolset's `approval_required_tools` patterns.

The remaining read-only / pre-approved tools are the only functions the client
exposes.

### Execution and isolation

The untrusted script runs in a **subprocess**; the real tools run in the
**parent** Holmes process. They communicate over a **unix-domain socket**: the
script's `holmes.*` call sends `{tool, params}` to the parent; the parent's
**bridge** validates and dispatches it and streams the result back. Two
properties fall out of this split:

- **Credentials stay in the parent.** The subprocess is started with a minimal
  environment allow-list (`PATH`, locale, and the bridge's socket/script paths) —
  never the parent's `os.environ`, so LLM/provider keys and other secrets are not
  visible to the script.
- **The allow-list is enforced parent-side.** The subprocess is given the socket
  path, so a script can bypass the generated client and hand-craft raw socket
  requests. The parent therefore re-checks every request's tool name against the
  eligibility allow-list *before* looking up or invoking anything, and converts
  an `APPROVAL_REQUIRED` outcome into an error. The generated client is a
  convenience; the socket boundary is the security boundary.

### Observability — sub-calls stay visible

Code mode must not turn N tool calls into one opaque box (user story 3). The
design keeps sub-calls first-class by reusing the **existing** SSE events with
one added optional field, `parent_tool_call_id`, pointing at the enclosing
`run_python_code` call. Because relay only forwards bytes and keys tool calls by
id, sub-call events flow through unchanged, and the frontend nests them under the
parent card (the script renders in the parent's "Request" tab). This needs no
relay change and an additive frontend change in both stream consumers.

*v1 status:* the tool records each sub-call and returns them as a summary footer
in the result; the live nested-card streaming (the `parent_tool_call_id` field +
frontend nesting) is the remaining increment and ships as a separate frontend
PR.

### Data flow

```text
LLM step k                Holmes parent                     UI (via relay pipe)
  │                         │                                 │
  ├─ tool_call ───────────▶ run_python_code(code)             ├─ card: "run_python_code"
  │                         │  generate holmes client         │
  │                         │  spawn python subprocess ──┐     │
  │                         │                            │     │
  │                         │  script (subprocess):      │     │
  │                         │    holmes.list_pods() ──────▶ bridge: validate + dispatch
  │                         │       (result → subprocess)│     ├─ nested card (sub-call)
  │                         │    filter in Python        │     │
  │                         │    holmes.logs(x3) ─────────▶ bridge: validate + dispatch
  │                         │    print(summary)          │     ├─ nested cards
  │                         │                            ◀┘     │
  │                         │  stdout → spill guard            │
  ◀─ tool result (summary) ─┤  (only the summary enters        │
  │                            context, not the raw data)      │
  ▼
LLM step k+1  (history grew by ~summary, not by all raw tool output)
```

The token win is structural: the raw tool outputs the script discards **never
enter the model's context** — only what it `print`s does, once.

## Code mode vs. bash mode

Bash is the existing "compose tools in code" path, so it is the right baseline.
The two share their runtime primitives (subprocess, `ulimit`, timeout,
spill-to-disk) but differ in reach and in where the safety boundary sits.

| Dimension | Bash mode (`bash` toolset) | Code mode (`code_execution`) |
|---|---|---|
| **What executes** | One shell command line (`shell=True`) | An arbitrary Python script |
| **Reach / composition** | CLI tools only (`kubectl`, `jq`, `grep`, …); model hand-writes/parses text | All Holmes toolsets (Prometheus, Grafana, k8s, …) as typed `holmes.*` functions in one script |
| **Whitelisting** | Prefix allow/deny list on the **command** itself | No allow-list on the **code** (arbitrary Python by design); the allow-list is on **which Holmes tools** the script may call, enforced parent-side at the bridge |
| **Approval** | Per-command: an unknown/mutating prefix returns `APPROVAL_REQUIRED` → interactive approval | Approval-gated tools are **denied** at dispatch inside a script (no round-trip); the model must call them directly |
| **Memory** | `ulimit -v` cap on the shell subprocess | Same `ulimit -v` cap on the `python3` subprocess |
| **CPU / time** | Wall-clock timeout on the command | Wall-clock timeout on the script (default 60s, ≤300s) |
| **Credentials / env** | Command inherits the Holmes process environment (kubeconfig, keys) | Subprocess gets a **minimal env allow-list** — no `os.environ`; tools (and their creds) run in the parent, reached only via the socket |
| **Security boundary** | Command-prefix validation; single process | Parent-side tool allow-list + per-tool validation at the socket bridge; untrusted code isolated to the subprocess |
| **Injection patterns** | Shell injection bounded by prefix validation (metachar/quoting risk within allowed commands; secrets-read commands denied) | No shell. Tool-name/param **injection over the bridge** is bounded by the parent allow-list + each tool's own validation. **Residual (v1):** the Python itself can make arbitrary outbound network calls and read on-disk files — no OS sandbox (see Security model) |
| **Result-size guard** | `spill-to-disk` on stdout | Same guard on stdout — *and* the script can pre-filter so less reaches stdout at all |
| **Sandbox** | None (subprocess + `ulimit`) | None in v1 (subprocess + `ulimit`); OS/language sandbox is a Future Goal |
| **Token behavior** | Intermediate data can stay in the shell pipe, but only across CLI tools | Intermediate data from **any** toolset stays in the subprocess; only `print()` output re-enters context |

Net: code mode is a strict superset of bash's composition ability across all
toolsets, with the same resource bounds, and it moves the safety boundary from
"validate the command string" to "validate which tools the code may call, in the
parent." Its new risk relative to bash is that the executed language is
unconstrained (network + filesystem), which is what the Security model addresses.

## Error states / failure scenarios

- **Syntax error / runtime exception** → `ERROR` result with the traceback and a
  non-zero return code; the model reads it and self-corrects.
- **Timeout / infinite loop** → the subprocess is killed at the wall-clock
  deadline; `ERROR` result.
- **Memory blow-up** → the `ulimit` cap kills the subprocess; `ERROR` result.
- **Huge stdout** → `spill-to-disk` replaces it with a file pointer.
- **Sub-call to a non-eligible / approval-gated / unknown tool** → the bridge
  returns an error to the script (raised as `holmes.HolmesToolError`); the tool
  never runs.
- **Sub-tool returns an error** → surfaced as a catchable `holmes.HolmesToolError`.
- **Executor not wired** → the tool returns a clear configuration error rather
  than crashing the loop.

## Scale / limitations

- **Concurrency.** The agentic loop already runs top-level tool calls in a
  bounded thread pool; each `run_python_code` call is one such call and spawns one
  subprocess. Memory must be sized so `pool_size × ulimit` fits the pod's limit.
- **Serial sub-calls.** The bridge is single-connection / single-threaded, so
  sub-calls *within* one script run serially. Fine for v1; parallel fan-out
  inside a script would need a multi-connection bridge (Open Questions).
- **Prompt overhead when unused.** Enabling the toolset adds its API reference to
  the system prompt; on small tasks that overhead is not repaid (Open Questions).

## Security model

The script is arbitrary, LLM-generated Python, and Holmes ingests untrusted data
(logs, alerts, k8s object contents), so the realistic threat is **prompt
injection → code execution**. The model is therefore **capability-limiting, not
code-sandboxing**: assume the script can run any Python, and constrain what it
can reach.

**Enforced boundary (v1).**

- **Parent-side tool allow-list.** Every bridge request is checked against the
  eligibility set *before* dispatch, so a script cannot reach `is_core`, `bash` /
  `kubectl_run`, or approval-gated tools — even by forging raw socket requests
  that bypass the generated client.
- **Approval is not scriptable.** An `APPROVAL_REQUIRED` result at dispatch
  becomes an error; there is no auto-approve path.
- **Credentials isolated.** Tools run in the parent; the subprocess env is a
  minimal allow-list, never the parent's secrets.
- **Per-tool validation unchanged**, and **resource bounds** (`ulimit` + timeout)
  apply to the subprocess.

**Residual risk (accepted for v1, off by default).** There is **no language/OS
sandbox** — subprocess + `ulimit` only. A script can therefore still (a) make
**arbitrary outbound network calls** and (b) **read on-disk files** the process
can access (env secrets are stripped, but files such as the mounted
ServiceAccount token are not). Under prompt injection the blast radius is
"anything the pod's process can reach and exfiltrate", and note that reading the
SA token from disk would let a script bypass the read-only tool allow-list by
calling the API server directly. **The allow-list bounds the tools, not the
runtime.**

**Path to production.** The IPC split is deliberately sandbox-friendly: the
untrusted subprocess needs *only* the socket, so it can be jailed hard without
losing function. The recommended posture:

- *Self-hosted, single-tenant (operator owns the data):* ship enabled-by-config
  with least-privilege read-only RBAC (no secret reads), an egress `NetworkPolicy`
  that denies all but the LLM/relay endpoint, and no SA-token mount in the exec
  path.
- *Multi-tenant / untrusted input:* real isolation is a **prerequisite** — run the
  subprocess in an egress-denied, credential-free sandbox (gVisor or a microVM;
  read-only rootfs, non-root, dropped caps, seccomp). This is the gating decision
  for default-on (Open Questions).

These boundaries are covered by
`tests/plugins/toolsets/code_execution/test_code_execution_security.py`,
including a script that forges raw socket requests to excluded tools (denied) and
the documented, currently-permitted local-file read.

## Observability

- **Sub-call visibility** as described above — recorded + summarized in v1, live
  nested cards as the follow-up.
- **Operational:** every executed script and its sub-call list should be logged
  for audit/forensics; `LLMResult`/`RequestStats` already record `num_llm_calls`,
  `total_tokens`, and tool-call counts, and `HolmesUsageEvents` gives the prod
  before/after for a rollout A/B.

## Infrastructure

- No new infra. `run_python_code` needs `python3` in the Holmes image (already
  present) and is verified by the toolset's prerequisite check.
- Off by default; enabled per-account/per-request via the existing config path
  and (for the hosted product) an `AccountSettings` flag read the same way
  remote-tool-execution reads its flag.
- Rollout: (1) internal accounts + evals; (2) opt-in beta on high-iteration
  accounts under the self-hosted hardening above; (3) default-on for tool-heavy
  request types once evals show correctness parity, a prod A/B shows the win, and
  — for any untrusted-input exposure — the runtime sandbox is in place.

## Token-cost impact

- Addressable segment (≥6 iterations, tool-heavy): ~$15.3k/mo, already ~82%
  cache-discounted.
- **Net estimate: ~15–25% of total Holmes spend (~$3–6k/mo)** — deliberately not
  the headline 98%, because caching already banked sink (1) and bash+spill
  already bank part of sink (2). The band is wide because the
  result-vs-definition token split isn't measurable from prod telemetry alone;
  the rollout A/B closes it.
- The *mechanism* is proven deterministically (not model-dependent): measuring
  the context-token cost of a tool result with the product's own tokenizer, a
  filtered script returns **250,029 → 136 tokens** on a large payload and
  collapses **8 sub-call results into 1** (30,952 → 275 tokens). See
  Implementation & Verification.

# Open Questions

1. **Sandbox before default-on.** Ship config-enabled for trusted operators with
   the RBAC/egress hardening, or block default-on on a real runtime sandbox
   (gVisor / microVM)? This is the main go-to-prod decision.
2. **Routing.** v1 is prompt-only. Because Holmes's server-side filters
   (`kubernetes_jq_query`, log `filter`) already handle many tasks, the model
   often won't reach for code mode; a cheap pre-classifier or stronger prompting
   may be needed to realize the savings. Measure regression on trivial asks
   first.
3. **Eligibility source of truth.** Should the code-mode allow-list be pinned to
   the exact remote-tool-execution `expose_remotely` set (they are defined
   independently today and could drift)?
4. **Sub-call cost attribution.** Should sub-calls count toward `tool_call_count`
   in `HolmesUsageEvents` (proposed: yes, for an honest before/after)?
5. **Parallel sub-calls.** Is intra-script parallel fan-out worth a
   multi-connection bridge, or is serial fine indefinitely?
6. **Prompt-overhead crossover.** Where is the task size at which the toolset's
   API-reference overhead is repaid, and should the reference be trimmed or
   lazily disclosed for small requests?
7. **Live A/B demonstration.** Do we want a live eval backed by a data source
   with no server-side filter (to show the win end-to-end), or is the
   deterministic proof + a prod A/B sufficient?

# Implementation & Verification

Implemented in HolmesGPT: the `code_execution` toolset (tool, generated client,
socket bridge, subprocess runner, config), a generic `set_tool_executor` wiring
hook on the agentic loop, and a request-scoped executor handle on the tool
context. Off by default. Verified by:

- **Deterministic token-reduction proof** (`test_code_mode_token_reduction.py`) —
  measures the context-token cost of a tool result classic vs. code mode with the
  product's own formatter + `litellm` tokenizer. Result filtering 250,029 → 136
  tokens (99.95%); call consolidation (8 results → 1) 30,952 → 275 (99.11%);
  correctness asserted. No LLM, no cluster.
- **Unit / integration** (`test_code_execution.py`, `_wiring.py`, `_security.py`)
  — 24 tests over the real subprocess: eligibility exclusions, the token-saving
  property, executor wiring, and failure modes (syntax/runtime error, timeout,
  unavailable tool, erroring sub-tool, approval denied-not-paused, unwired
  executor, timeout clamping, env-secret isolation), plus the security-boundary
  tests (raw-socket bypass of the client denied for excluded/approval-gated/
  unknown names; documented local-file-read gap). No regressions in the existing
  loop/executor tests.
- **LLM evals** — two ask_holmes A/B fixtures (`toolsets_matrix`: classic vs.
  codemode) asserting **correctness parity**; 4/4 passing in CI on opus-4.6.
  These do not claim the token win (Holmes filters server-side, so the model
  didn't switch to code mode on small fixtures) — the deterministic test above is
  the token-win proof.

# Detailed Implementation & Context

Files as of this doc's writing; re-verify line numbers before editing.

## The loop and existing primitives (reused)

- **Loop:** `holmes/core/tool_calling_llm.py` — `ToolCallingLLM.call_stream` runs
  `while i < max_steps`: assemble → compaction check → `llm.completion(tools=…)` →
  execute tool calls in `ThreadPoolExecutor(max_workers=16)` → append each
  `to_llm_message()` → repeat. Tool execution funnels through
  `_directly_invoke_tool_call` → `tool.invoke(params, context)`.
- **Tools:** `holmes/core/tools.py` — `Tool` (abstract `_invoke → StructuredToolResult`),
  `Toolset`, `StructuredToolResult.stringify_data`, `requires_approval →
  APPROVAL_REQUIRED`, and the `is_core` marker.
- **Dispatch registry:** `holmes/core/tools_utils/tool_executor.py` —
  `get_tool_by_name`, `ensure_toolset_initialized`.
- **Result guard:** `holmes/core/tools_utils/tool_context_window_limiter.py:spill_oversized_tool_result`.
- **Bash primitives:** `holmes/plugins/toolsets/bash/common/bash.py` —
  `execute_bash_command`, `get_ulimit_prefix()`.
- **Token measurement:** `holmes/core/tools_utils/token_counting.py:count_tool_response_tokens`
  → `holmes/core/models.py:format_tool_result_data` + `LLM.count_tokens`.

## New: `holmes/plugins/toolsets/code_execution/`

- **`code_execution_toolset.py`** — `RunPythonCode(Tool)` + `CodeExecutionToolset(Toolset)`.
  `_invoke` resolves the request's `ToolExecutor` from a request-scoped handle on
  `ToolInvokeContext` (falling back to the wired toolset handle), builds the
  eligible-tool spec, launches the runner subprocess (stdout → temp file to avoid
  a pipe deadlock), and serves the bridge until the process exits or the deadline
  passes. `_make_dispatch(context, executor, allowed)` is the **security boundary**:
  it rejects any `tool_name not in allowed`, converts `APPROVAL_REQUIRED` to an
  error, and otherwise invokes the real tool with a request-scoped sub-context.
  `_build_subprocess_env(...)` builds the minimal env allow-list (`_ENV_PASSTHROUGH`
  = PATH/LANG/LC_*/TZ + `HOLMES_CODE_*`), never `os.environ`. `_resolve_timeout`
  clamps to `[1, max_timeout_seconds]`, default `default_timeout_seconds`.
  `_is_core=True`, `enabled=False`.
- **`client_generator.py`** — `EXCLUDED_TOOLSET_NAMES = {"bash", "kubectl_run",
  "code_execution"}`; `_is_eligible(tool, toolset)` (excludes `is_core`, the
  excluded names, and `approval_required_tools` matches); `eligible_tools`,
  `eligible_tool_names`, `build_tools_spec`, `build_api_reference`, `_sanitize_attr`.
- **`bridge.py`** — `ToolCallBridge` (context manager binding an AF_UNIX socket;
  `serve_until_exit(process, deadline)` is single-connection with recv/accept
  timeouts) and `SubToolCall` records. `_handle_line` parses `{tool, params}` and
  calls the injected `dispatch`.
- **`runner.py`** — stdlib-only subprocess bootstrap. `_Bridge` (newline-delimited
  JSON over the socket), `_build_holmes_namespace` (turns the tools spec into
  `holmes.<attr>()` functions + `holmes.HolmesToolError`), and `main()` returning
  exit codes (0 ok, 1 exception, 2 SyntaxError, 3 HolmesToolError). Reads
  `HOLMES_CODE_SOCKET` / `HOLMES_CODE_USER_FILE` / `HOLMES_CODE_TOOLS` from env.
- **`code_execution_config.py`** — `CodeExecutionConfig(ToolsetConfig)` with
  `default_timeout_seconds=60`, `max_timeout_seconds=300`.

## Wiring (minimal, generic)

- `holmes/core/tool_calling_llm.py` — a `_wire_toolset_executors()` hook called
  from `__init__` invokes `set_tool_executor(self.tool_executor)` on any toolset
  that exposes it (guarded for non-list `toolsets`); and the `ToolInvokeContext`
  built in `_directly_invoke_tool_call` now carries `tool_executor` so dispatch is
  request-scoped.
- `holmes/core/tools.py` — `ToolInvokeContext.tool_executor: Optional[Any] =
  Field(default=None, exclude=True)` (excluded from serialization).
- `holmes/plugins/toolsets/__init__.py` — `CodeExecutionToolset()` appended in
  `load_python_toolsets` after `BashExecutorToolset()`.

## Tests

`tests/plugins/toolsets/code_execution/` — `test_code_execution.py`,
`test_code_execution_wiring.py`, `test_code_execution_security.py`,
`test_code_mode_token_reduction.py`; all run the real subprocess bridge, grouped
under `xdist_group("code_execution")`. Eval fixtures:
`tests/llm/fixtures/test_ask_holmes/285_code_mode_count_configmaps/` and
`286_code_mode_large_configmap_filter/` (each a classic-vs-codemode
`toolsets_matrix`).
