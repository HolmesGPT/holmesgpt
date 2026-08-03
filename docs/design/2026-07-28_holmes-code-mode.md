| Authors | Naomi |
| --- | --- |
| Review Date | 28 / Jul / 2026 |
| Participants | AI/Holmes team, Backend (relay), Frontend |

Ticket: [ROB-723](https://linear.app/robusta/issue/ROB-723) — "Using Code mode
instead of scripts may save our token cost for tool calls." Project: *Minimize
Token Cost for us (and users)*.

Related reading:
- `2026-06-10_remote-tool-execution.md` (relay) — the `expose_remotely` /
  `is_core` markers, the **pre-approved-only, no-approval-round-trip** trust
  model, and the 1MB inline result cap. Code mode reuses all three.
- `2025-10-23_holmes-event-driven-architecture.md` (relay) — the SSE event
  contract (`start_tool_calling` / `tool_calling_result`) that carries tool
  calls to the UI, and relay's role as a transparent pipe.

# Introduction

## Overview

Holmes investigates by running an **agentic tool-calling loop**: the LLM emits
one or more tool calls, Holmes executes them, appends each result to the
conversation, and re-sends the whole growing history on the next step. Complex
investigations run 10–55 tool calls across many steps, and because every step
re-sends all prior tool results, input tokens grow super-linearly with
iteration count.

**Code mode** ([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp),
[Cloudflare](https://blog.cloudflare.com/code-mode/)) replaces "many small tool
calls, one per LLM step" with "the LLM writes **one Python script** that composes
many tool calls, runs it in a sandbox, and only the **filtered final result**
returns to the model's context." Tool outputs the model never needs are
filtered *inside the script* and never enter the context window at all.

Solution: a new **`code_execution` toolset** in Holmes whose single tool takes a
Python snippet, runs it in a subprocess pre-loaded with a **generated `holmes`
client module** that exposes the current request's tools as callable Python
functions. Each function dispatches back into the existing `ToolExecutor`, so
tools behave identically whether called directly or from a script. Sub-calls
still emit their own SSE events, so the UI shows the same per-tool cards it does
today; the wrapping script does not become an opaque black box.

Stakeholders: AI/Holmes team (the runtime + prompting), backend/relay (SSE
passthrough — no code changes expected), frontend (one new event field to
surface script + sub-calls).

## Glossary

- **Code mode** — the execution path where the LLM writes Python composing tool
  calls, instead of emitting one function call per LLM step.
- **`code_execution` toolset / `run_python_code` tool** — the new toolset and
  its single tool (this design). Modeled on `BashExecutorToolset`
  (`holmes/plugins/toolsets/bash/bash_toolset.py:399`).
- **client module (`holmes`)** — an auto-generated Python module, injected into
  the script's namespace, whose functions mirror the request's tools
  (`holmes.kubernetes.get_pods(...)`, `holmes.prometheus.query(...)`). Each is a
  thin wrapper that calls `ToolExecutor.get_tool_by_name(name).invoke(...)`.
- **sub-call** — a tool invoked from inside a running script (as opposed to a
  top-level tool call the LLM emits directly).
- **the two token sinks** — (1) *tool-definition overhead*: all tool schemas
  loaded into context up-front; (2) *intermediate-result bloat*: every tool
  result re-sent through context on each subsequent step. Sink (2) is the one
  that matters for Holmes (see Background).
- **spill-to-disk** — the existing size guard
  (`holmes/core/tools_utils/tool_context_window_limiter.py:30`) that saves an
  oversized tool result to a file and hands the model a `cat … | jq` pointer
  instead of the payload.

## Background

- **Where the money is (30-day prod, customer-facing).** ~$23k/mo total Holmes
  spend; **82.6% overall prompt-cache hit** (89–94% on the expensive buckets).
  Cost is ~99% *input* tokens (completion averages 4–11k against prompt averages
  of 142k → 2.9M). Spend rises with iteration count in lockstep with
  `avg_tool_calls` (4-6 iters → 10 calls, 7-10 → 19, 11-20 → 30, 21-50 → 55).
  Requests with **≥6 iterations = $15.3k/mo (66.5% of spend)** — the exact
  multi-tool-chaining regime code mode targets.
- **Sink (1) is already neutralized by prompt caching.** The static prefix
  (system prompt + all tool schemas) is identical every step and is the most
  cacheable content, so at 82.6% cache hit the "don't load all tool defs
  up-front" half of the Anthropic/Cloudflare 98% headline buys us almost nothing
  in dollars. **The win must come from sink (2): fewer results entering context,
  and fewer iterations.** This re-prioritizes the design: result-filtering and
  call-consolidation first; progressive tool-def disclosure is a Future Goal, not
  v1.
- **Holmes already has a proto-code-mode.** The `bash` toolset
  (`bash_toolset.py:94`) runs shell (kubectl/jq/grep) with prefix allow/deny
  validation, and `spill_oversized_tool_result` already keeps oversized payloads
  out of context behind a file pointer. So v1 is capturing the *residual* between
  "compose CLI tools in bash" and "compose **all** toolsets (Prometheus, Grafana,
  Datadog, k8s, …) in one typed script" — not a greenfield 98%. **Estimated
  saving: ~15–25% of total spend (~$3–6k/mo)**, pending the trace study; the
  wide band is because the result-vs-definition token split is not measurable
  from prod telemetry alone.
- **Not everything is addressable.** The single largest uncached-cost bucket is
  short `user_chat` (1–3 iters, cold cache) — code mode does nothing for it.
  `health_check` synthetic traffic is a separate config problem. v1 scopes to
  multi-iteration, tool-heavy real traffic.

## Goals

User stories:

1. Holmes investigates "which of the 200 pods in namespace X restarted in the
   last hour, and why?" by writing one script that lists pods, filters to
   restarted ones in Python, fetches logs only for those, and returns a 5-line
   summary — instead of 20 LLM steps each re-sending the full pod list.
2. A script that pulls a 10,000-series Prometheus range and returns only the top
   3 offenders keeps the other 9,997 series out of the model's context entirely.
3. The user watching the Ask-Holmes UI still sees each sub-call as its own tool
   card (name, status, expandable output) plus the script that drove them — code
   mode is not an opaque box.
4. A simple single-tool ask ("is the cluster healthy?") is **not** made slower or
   costlier — the model keeps using direct tool calls for trivial work.

Technical requirements:

- A new `code_execution` toolset with one tool `run_python_code`, off by default
  behind a feature flag, modeled on `BashExecutorToolset`.
- Tools callable from a script go through the **same** `ToolExecutor` path,
  validation, transformers, and result-size guard as direct calls — no bypass.
- **Read-only / pre-approved trust model**, identical to remote-tool-execution:
  a running synchronous script cannot pause for interactive approval, so
  approval-gated tools are **denied on the spot inside a script**; only
  pre-approved / read-only tools are callable. `is_core` toolsets
  (`robusta_platform_mcp`, `core_investigation`/`TodoWrite`, `skills`) are never
  exposed in the client module.
- Every sub-call emits its own `start_tool_calling` / `tool_calling_result` SSE
  event (with a `parent_tool_call_id`) so relay forwards them unchanged and the
  UI renders them — see Observability.
- Script stdout over the per-tool cap goes through `spill_oversized_tool_result`
  (same 15% / 25k-token guard), so a runaway script can't flood context.
- `LLMResult` already exposes `num_llm_calls`, `total_tokens`,
  `max_prompt_tokens_per_call`, etc. (`llm_usage.py:81`); code mode must move
  these *down* for multi-step tasks and this is asserted in tests.
- Works on any model/provider — it is an ordinary tool call, no special API.

## Out of Scope

- **Interactive approval inside a script.** No approval round-trip (matches
  remote-tool-execution). Approval-gated tools are denied at call time inside a
  script; the model must fall back to a direct tool call to trigger the normal
  approval flow.
- **A real language-level sandbox** (RestrictedPython / gVisor / container
  isolation) in v1. v1 reuses the bash trust model — subprocess + `ulimit`
  memory cap + read-only tools. Hardening is a Future Goal and an explicit
  security decision (see Assumptions).
- **Progressive disclosure of tool definitions** (filesystem tree of stubs,
  `search_tools`) — deprioritized because caching already neutralizes sink (1).
- **Writing/mutating tools from a script** — read-only tools only.
- **Persisted "skills"** (saving successful scripts as reusable functions across
  sessions).

## Future Goals

- Language-level sandboxing to allow a broader tool set and untrusted code.
- Progressive tool-def disclosure if tool count ever grows enough to matter
  despite caching.
- Auto-routing: a cheap classifier that pre-selects code mode vs direct calls
  per request, instead of leaving it to the model + prompt.
- Persisted skills (reusable scripts) and cross-call state files.
- Extending code mode across clusters by composing the remote tools from
  `2026-06-10_remote-tool-execution.md` inside a script.

## Assumptions & Constraints

- **Security posture.** Holmes runs in-cluster with live credentials and RBAC.
  Executing LLM-generated Python is a materially larger attack surface than
  prefix-validated bash. v1's mitigation is **not** interpreter sandboxing; it is
  that (a) only read-only / pre-approved tools are reachable, (b) the same
  allow/deny validation still runs on any `bash` sub-call, (c) a `ulimit` memory
  cap and wall-clock timeout bound the subprocess, and (d) the feature is
  off-by-default behind a flag. This is the identical stance the team already
  accepted for remote tool execution, and it is called out here as a conscious
  decision, not an oversight.
- **Relay is a transparent SSE pipe.** `ClientsManager.send_stream_message`
  yields Holmes's SSE bytes verbatim; relay identifies tool calls solely by
  `tool_call_id` (`SSE/holmes_tools_helper.py:upsert_tool_call`). So sub-calls
  are observable **iff Holmes emits them as normal SSE events** — no relay change
  needed as long as we reuse the existing event types.
- The frontend renders a **flat `HolmesTool[]`** via two independent consumers
  (legacy `onChunk` in `holmes-chat-history.store.ts:1333` and the realtime
  `EventProjector._processEvent` in `event-projector.ts:228`). A new event field
  must be handled in **both**, or it is silently dropped in one path.
- Per-tool result cap = `min(15% ctx, 25k tokens)`
  (`TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_*`); compaction fires at 95% (rarely — 0.5%
  of requests). Code mode does not change these; it feeds through them.

# Design

## Current Design

- **The loop** — `holmes/core/tool_calling_llm.py`. `ToolCallingLLM.call_stream`
  (`:1035`) runs `while i < max_steps` (`:1105`): assemble messages → check/apply
  compaction (`:1115`) → `self.llm.completion(..., tools=tools)` (`:1167`) →
  append assistant turn → if `tool_calls` present, execute them in a
  `ThreadPoolExecutor(max_workers=16)` (`:1320`), append each
  `tool_call_result.to_llm_message()` (`:1415`), loop; else return the answer.
  Tool execution funnels through `_invoke_llm_tool_call` (`:870`) →
  `_directly_invoke_tool_call` (`:747`) → `tool.invoke(params, context)`
  (`:789`).
- **Tools** — `holmes/core/tools.py`. `Tool` (`:284`) with `parameters:
  Dict[str, ToolParameter]` and abstract `_invoke(...) -> StructuredToolResult`
  (`:516`). `Toolset` (`:719`). `StructuredToolResult` (`:96`) with
  `stringify_data(compact=True)` (`:111`). Approval via `requires_approval`
  (`:423`) returning `APPROVAL_REQUIRED`. `expose_remotely` / `is_core` markers
  exist from remote-tool-execution.
- **Tool → LLM schema** — `holmes/core/openai_formatting.py`.
  `format_tool_to_open_ai_standard` (`:153`), `type_to_open_ai_schema` (`:71`).
- **Dispatch registry** — `holmes/core/tools_utils/tool_executor.py`.
  `get_all_tools_openai_format` (`:178`), `get_tool_by_name` (`:100`),
  `clone_with_extra_tools` (`:153`, per-request tool injection).
- **Result size guard** — `tool_context_window_limiter.py:spill_oversized_tool_result`
  (`:30`); the file-pointer fallback is gated on `_has_bash_for_file_access`
  (`:246`).
- **The bash executor** — `bash_toolset.py`: `RunBashCommand` (`:94`),
  validation via `validate_command`, `requires_approval` → `APPROVAL_REQUIRED`
  for unknown prefixes; `BashExecutorToolset` (`:399`). Low level:
  `common/bash.py:execute_bash_command` (`:17`) = `subprocess(shell=True)` +
  `get_ulimit_prefix()` memory cap. **No interpreter sandbox.**
- **Prompting** — `holmes/plugins/prompts/generic_ask.jinja2` (main system
  prompt), `_toolsets_instructions.jinja2` (injects each toolset's
  `llm_instructions`). Built by `holmes/core/prompt.py:build_system_prompt`
  (`:177`).
- **Streaming contract** — Holmes emits SSE `start_tool_calling` (name + id) then
  `tool_calling_result` (result + status). Relay's `SSEEventType`
  (`relay/pkg/holmes/common/data_types.py:7`) and `upsert_tool_call`
  (`SSE/holmes_tools_helper.py:102`) merge the pair into one `HolmesToolCall`
  keyed by `tool_call_id`. Frontend consumes via `enums.ts`, projects into
  `HolmesTool[]` (`holmes-types.d.ts:189`), renders per-tool in
  `HolmesToolsCollapseBlock.vue`.

Pros: the loop, dispatch, size-guard, validation, and streaming are all
battle-tested. Cons: tools compose only one-per-step through the model, so
intermediate results flow through context repeatedly.

## Proposed Design

### 1. The `run_python_code` tool

New toolset `holmes/plugins/toolsets/code_execution/`, registered in
`holmes/plugins/toolsets/__init__.py:load_python_toolsets`. One `Tool`:

- **Parameters**: `code: str` (the Python snippet), optional `timeout: int`
  (default 60s).
- **`_invoke(params, context)`**:
  1. Materialize the **client module** (§2) for `context`'s `ToolExecutor` and
     the request's enabled, non-`is_core`, pre-approved/read-only tools.
  2. Write the snippet + a bootstrap that imports the client module to a temp
     file (per-request `tmp` dir; `xdist_group` in tests).
  3. Run it via `execute_bash_command("python3 <file>", timeout)` — reusing the
     bash executor's `get_ulimit_prefix()` memory cap and timeout handling
     (`common/bash.py`).
  4. Capture stdout/stderr → `StructuredToolResult` (SUCCESS / ERROR / NO_DATA,
     `return_code`, `invocation`, `elapsed_seconds`), same shape as
     `bash_result_to_structured`.
  5. Pass the result through `spill_oversized_tool_result` so oversized stdout
     becomes a file pointer, not a context flood.
- **`BashExecutorConfig`-style config**: `enabled=False` by default,
  `is_core=False`, `expose_remotely=False` (a script fanning out to remote tools
  is a Future Goal), plus a `llm_instructions` jinja2 template teaching the model
  *when* to use it (multi-step / large-result tasks) and *when not to* (trivial
  asks) — surfaced via `_toolsets_instructions.jinja2`.

The agentic loop (`call_stream`) needs **no change**: `run_python_code` is an
ordinary tool call. Parallel execution, the result-feedback path, and
compaction all apply unchanged.

### 2. The generated `holmes` client module

Generated per request from the `ToolExecutor`'s eligible tools:

```text
holmes/
  kubernetes.py     get_pods(namespace: str, ...) -> dict
  prometheus.py     query(promql: str, ...) -> dict
  logs.py           fetch(pod: str, ...) -> dict
  ...
```

- One Python function per `Tool`, grouped into a module per toolset. Signature is
  derived from `parameters: Dict[str, ToolParameter]` by **inverting** the
  `type_to_open_ai_schema` mapping (`openai_formatting.py:71`) into Python type
  hints; docstring = the tool's `description`.
- Each function body is a thin wrapper:
  `return _dispatch("<tool_name>", locals())` → calls
  `ToolExecutor.get_tool_by_name(name).invoke(params, context)` and returns the
  parsed `StructuredToolResult.data` (raising a Python exception on ERROR so the
  script can `try/except`).
- **Eligibility filter** (applied when building the module):
  - exclude `is_core` toolsets (`robusta_platform_mcp`, `core_investigation`,
    `skills`);
  - exclude tools whose `requires_approval(...)` is not statically
    pre-approved/read-only — a call to one inside a script returns an ERROR
    "approval-gated tool; call it directly instead," never a silent pause.
- Dispatch runs in the **same process** as the loop (the script subprocess calls
  back via a thin RPC/stdin-stdout bridge to the parent, or — simpler v1 — the
  parent process runs the tools and the subprocess only runs the user's Python
  with the client stubbed to IPC). The exact bridge is an implementation
  decision captured in Open Questions; both keep validation server-side.

### 3. Observability — sub-calls are first-class

To keep the UI honest (user story 3), each sub-call emits the **existing** SSE
events with one new field:

- `start_tool_calling` / `tool_calling_result` gain an optional
  `parent_tool_call_id` pointing at the `run_python_code` call's id.
- Relay needs **no change** — it forwards bytes and keys tool calls by id
  (`upsert_tool_call`), so the sub-call events flow through as ordinary tool
  cards.
- Frontend: add `parent_tool_call_id` handling in **both** consumers
  (`holmes-chat-history.store.ts:1333` and `event-projector.ts:228`) and a
  `subTools?: HolmesTool[]` field on `HolmesTool` (`holmes-types.d.ts:189`);
  render sub-calls nested inside the parent row in `HolmesToolsCollapseBlock.vue`
  (which already supports nested `HolmesTools`). The script itself renders in the
  parent row's "Request" tab. Unknown/unhandled → the realtime projector's
  `default` warns (`event-projector.ts:431`); we handle it explicitly so nothing
  is dropped.

### Data flow

```text
LLM step k                Holmes                          UI (via relay pipe)
  │                         │                               │
  ├─ tool_call ───────────▶ run_python_code(code)           │
  │                         │  build holmes client module   ├─ card: "run_python_code"
  │                         │  spawn python subprocess ──┐   │
  │                         │                            │   │
  │                         │   script calls:            │   │
  │                         │     holmes.k8s.get_pods() ─┼──▶ emit start/result   ├─ nested card (sub-call)
  │                         │       → ToolExecutor.invoke │   │   (parent_tool_call_id)
  │                         │     filter in Python        │   │
  │                         │     holmes.logs.fetch(x3) ──┼──▶ emit start/result   ├─ nested cards
  │                         │     print(summary)          │   │
  │                         │                            ◀┘   │
  │                         │  stdout → spill guard          │
  ◀─ tool result (summary) ─┤  (only the 5-line summary      │
  │   enters context           enters context, not the       │
  │                            9,997 discarded series)        │
  ▼
LLM step k+1  (history grew by ~summary, not by all raw tool output)
```

The token win is structural: the raw `get_pods` list and the two unused log
bodies **never enter the model's context** — only `print(summary)` does, once.

### Feature flag & rollout

- Off by default. Enable per-account/per-request via the existing config path
  (`toolsets.code_execution.enabled: true`) and, for the hosted product, an
  `AccountSettings` flag read the same way remote-tool-execution reads its flag.
- Rollout: (1) internal accounts + evals; (2) opt-in beta on a few high-iteration
  accounts (the ROB-723 project's target segment); (3) default-on for
  tool-heavy request types if evals show correctness parity and a token win.

# Security model

The script is arbitrary, LLM-generated Python. The model is therefore
**capability-limiting, not code-sandboxing**: assume the script can run any
Python, and constrain what it can *reach*.

**The trust boundary is the parent-side allow-list, not the generated client.**
The subprocess is handed the bridge socket path (`HOLMES_CODE_SOCKET`), so a
script can bypass the generated `holmes.*` stubs and send raw
`{"tool": ..., "params": ...}` requests over the socket by hand. Every request
is therefore re-checked in the parent's `dispatch` before any tool is looked up
or invoked:

- **Allow-list enforced server-side.** A requested tool name not in
  `eligible_tool_names(...)` is rejected outright — `is_core` toolsets,
  `bash`/`kubectl_run`, and any `approval_required_tools` are excluded, so a
  script cannot reach a mutation/approval surface even by forging the request.
- **Approval cannot be scripted around.** An `APPROVAL_REQUIRED` result at
  dispatch is converted to an error; there is no auto-approve path.
- **Credentials never enter the subprocess.** Tools execute in the parent; the
  subprocess env is a minimal allow-list (`PATH`/`LANG`/`HOLMES_CODE_*`), never
  the parent's `os.environ` (LLM/provider keys, DB creds, etc.).
- **Per-tool validation is unchanged.** Dispatched calls go through the real
  `tool.invoke()`, so each tool's own guards still apply.
- **Resource bounds.** `ulimit` memory cap + wall-clock timeout kill runaway
  scripts; oversized stdout flows through `spill_oversized_tool_result`.

**Residual risk (accepted for v1, off by default).** There is **no
language-level or OS sandbox** — subprocess + `ulimit` only. So a script can
still (a) make **arbitrary outbound network calls** and (b) **read on-disk files
the process can access** (env secrets are stripped, but files such as
`/var/run/secrets/kubernetes.io/serviceaccount/token` are not). Combined with a
successful **prompt injection** (Holmes ingests untrusted logs/alerts/objects),
the blast radius is "anything the pod's process can reach and exfiltrate". This
is the primary reason the feature is off by default; real isolation
(RestrictedPython / gVisor / container sandbox / egress deny) is a Future Goal
(see Out of Scope). These boundaries are covered by
`tests/plugins/toolsets/code_execution/test_code_execution_security.py`,
including a script that forges raw socket requests to excluded tools and the
documented (currently-permitted) local-file read.

# Token-cost impact

- Addressable segment (≥6 iterations, tool-heavy real traffic): ~$15.3k/mo,
  already ~82% cache-discounted.
- Expected reduction on that segment: **20–40%**, from (i) fewer LLM iterations
  (compose N calls in one script) and (ii) large results filtered before they
  ever enter context.
- **Net estimate: ~15–25% of total Holmes spend (~$3–6k/mo, ~$40–70k/yr)** — not
  the 98% headline, because caching already banked sink (1) and bash+spill
  already bank part of sink (2).
- Instrumentation: `LLMResult`/`RequestStats` already record every needed field;
  the eval harness records per-variant `total_tokens` / `num_llm_calls` /
  `tool_call_count`, and `HolmesUsageEvents` gives the prod before/after.

# Testing plan (summary)

Full plan tracked separately; the layers:

1. **Unit** (`tests/plugins/toolsets/code_execution/`) — real trivial
   subprocesses (à la `test_bash_command_execution.py`), the
   result→`StructuredToolResult` converter, and spill reuse (both
   `_has_bash_for_file_access` branches).
2. **Integration** (`tests/test_tool_calling_llm.py`) — script the mocked
   `llm.completion` to emit a `run_python_code` call; assert `num_llm_calls` /
   `total_tokens` are lower than the equivalent N-direct-call script;
   re-entrancy under the 16-worker pool; sub-call error propagation.
3. **LLM evals** — new `test_ask_holmes` fixture with
   `toolsets_matrix: [classic, codemode]`, shared `expected_output` (correctness
   parity), `max_tokens` on the codemode variant, plus a cross-variant assertion
   `codemode.total_tokens < classic.total_tokens`. Runs in `eval-regression.yaml`
   behind an `evals-*` label.
4. **Observability** — unit tests in both FE consumers
   (`event-projector.test.ts` + a new legacy `onChunk` test) asserting
   `parent_tool_call_id` nests sub-calls and keeps intermediate output visible;
   relay `test_holmes_chat_streaming.py` / `test_sse_events.py` for the new field.
5. **Local-stack manual** — `harness/bind.py up` + `verify --ui`; drive a
   code-mode investigation and confirm sub-call cards render.

Edge / failure modes explicitly covered: syntax error → self-correcting ERROR;
runtime exception; timeout / infinite loop; memory blow-up (ulimit); huge stdout
→ spill; approval-gated sub-call denied (not bypassed); bash validation still
enforced inside a script; partial sub-call failure; secret-leakage attempt;
no-bash fallback; xdist temp-file isolation; cross-model codegen; routing
regression on trivial asks.

# Alternatives considered

- **Do nothing / lean on bash.** The bash toolset already composes CLI tools, but
  it can't reach Holmes's Python toolsets (Prometheus, Grafana, Datadog, …) and
  the model must serialize/parse JSON by hand. Code mode gives a typed API over
  *all* toolsets. Rejected as insufficient for the addressable segment.
- **Progressive tool-def disclosure only** (the other half of the Anthropic
  design). Rejected for v1: caching already makes sink (1) nearly free in
  dollars, so it would add complexity for little saving.
- **Real sandbox (RestrictedPython / container) in v1.** Safer, but a large lift
  and it blocks shipping a measurable token win. Deferred to Future Goals; v1
  adopts the already-accepted read-only/pre-approved trust model.
- **New dedicated SSE event type for nested calls.** Rejected in favor of reusing
  `start_tool_calling` / `tool_calling_result` + a `parent_tool_call_id` field,
  so relay stays a pure pipe and the FE change is additive.

# Open Questions

1. **Execution bridge**: run tools in the parent process with the subprocess
   calling back over IPC (keeps validation & credentials out of the subprocess —
   preferred), vs. running tools in the subprocess directly (simpler, but the
   subprocess then holds credentials). Leaning IPC.
2. **Which tools are "statically pre-approved/read-only"** enough to expose in
   the client module — reuse the remote-tool-execution `expose_remotely` set as
   the v1 allowlist, or define a separate code-mode allowlist?
3. **Routing**: prompt-only (model chooses) for v1, or a cheap pre-classifier?
   Prompt-only is simplest; measure regression on trivial asks first.
4. **Sub-call cost attribution** in `HolmesUsageEvents` — do sub-calls count
   toward `tool_call_count`? (Yes, for honest before/after comparison.)
