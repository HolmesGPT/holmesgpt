# AG-UI vs HolmesGPT SSE: Protocol Comparison

## Executive Summary

AG-UI is an open, event-based protocol (born from CopilotKit's partnerships with LangGraph and CrewAI) that standardizes how AI agents connect to frontends. HolmesGPT has its own custom SSE streaming protocol for the `/api/chat` endpoint. There is already an experimental adapter at `experimental/ag-ui/server-agui.py` that translates HolmesGPT events into AG-UI events, but it only covers a subset of AG-UI's capabilities.

This document maps the two protocols event-by-event, identifies gaps, and highlights where HolmesGPT's protocol has capabilities AG-UI lacks.

---

## 1. Protocol Architecture Comparison

| Aspect | HolmesGPT SSE | AG-UI |
|--------|---------------|-------|
| **Transport** | SSE only (`text/event-stream`) | SSE primary, also supports HTTP streaming and WebSocket |
| **Format** | `event: <type>\ndata: <json>\n\n` | Structured event objects encoded via `EventEncoder` (content-negotiated) |
| **Direction** | Server → Client (unidirectional) | Bidirectional (events flow both ways; client sends tool results back) |
| **State model** | Stateless per-request; client manages `conversation_history` | Shared state with snapshots + JSON Patch deltas (RFC 6902) |
| **Session concept** | None (client manages conversation_history array) | Threads (`thread_id`) + Runs (`run_id`) with parent/child relationships |
| **Tool origin** | Backend-defined toolsets (YAML/Python) | Frontend-defined tools passed in request; backend tools also supported |
| **Streaming granularity** | Event-level (whole messages/results per event) | Token-level (text streams character-by-character via delta events) |

---

## 2. Event Type Mapping

### 2.1 Lifecycle Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `RUN_STARTED` | *(none)* | **Gap**: Holmes has no explicit "run started" event. The stream just begins. The AG-UI adapter synthesizes this. |
| `RUN_FINISHED` | `ai_answer_end` | **Similar**: Both signal completion. Holmes includes `analysis`, `conversation_history`, `follow_up_actions`, and cost metadata. AG-UI is leaner with just `thread_id`/`run_id`/`result`. |
| `RUN_ERROR` | `error` | **Similar**: Both carry error info. Holmes has structured `error_code` (5204 for rate limit, 1 for generic) + `description`/`msg`/`success`. AG-UI has `message` + `code`. |
| `STEP_STARTED` | *(none)* | **Gap**: AG-UI supports named steps within a run. Holmes has no step concept—tool calls are the closest analog. |
| `STEP_FINISHED` | *(none)* | **Gap**: Same as above. |

### 2.2 Text Message Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `TEXT_MESSAGE_START` | *(none)* | **Gap**: Holmes emits `ai_message` as a single atomic event with full content. No start/content/end streaming granularity. |
| `TEXT_MESSAGE_CONTENT` | `ai_message` (partial) | **Different granularity**: AG-UI streams token-by-token deltas. Holmes sends the entire message content at once. |
| `TEXT_MESSAGE_END` | *(none)* | **Gap**: No explicit end marker for text messages in Holmes. |
| `TEXT_MESSAGE_CHUNK` | `ai_message` | **Closest match**: AG-UI's convenience event that auto-expands to Start→Content→End. Holmes's `ai_message` is conceptually similar but non-streaming. |

### 2.3 Tool Call Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `TOOL_CALL_START` | `start_tool_calling` | **Equivalent**: Both signal tool invocation start. Holmes has `tool_name` + `id`. AG-UI has `toolCallId` + `toolCallName` + `parentMessageId`. |
| `TOOL_CALL_ARGS` | *(none)* | **Gap**: AG-UI streams tool arguments incrementally as JSON fragments. Holmes doesn't stream args—they're internal to tool execution. |
| `TOOL_CALL_END` | *(implicit in `tool_calling_result`)* | **Gap**: AG-UI has an explicit end marker separate from results. Holmes combines end + result into one event. |
| `TOOL_CALL_RESULT` | `tool_calling_result` | **Similar**: Both deliver tool output. Holmes is richer: includes `status` (success/error/approval_required), `data`, `error`, `params`, `description`, and `role`. AG-UI has `content`, `toolCallId`, `role`. |
| `TOOL_CALL_CHUNK` | *(none)* | **Gap**: AG-UI convenience event (Start→Args→End in one). No Holmes equivalent. |

### 2.4 State Management Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `STATE_SNAPSHOT` | *(none)* | **Major gap**: AG-UI supports typed shared state between agent and frontend. Holmes has no shared state—the client manages `conversation_history` externally. |
| `STATE_DELTA` | *(none)* | **Major gap**: JSON Patch (RFC 6902) incremental state updates. No Holmes equivalent. |
| `MESSAGES_SNAPSHOT` | `ai_answer_end` (partial) | **Partial**: Holmes includes `conversation_history` in the final event. AG-UI can send message snapshots at any point during the stream. |

### 2.5 Reasoning Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `REASONING_START` | *(none)* | **Partial gap**: Holmes includes `reasoning` field in `ai_message` but doesn't have dedicated reasoning lifecycle events. |
| `REASONING_MESSAGE_START` | *(none)* | **Gap**: No streaming reasoning in Holmes. |
| `REASONING_MESSAGE_CONTENT` | `ai_message.reasoning` | **Different**: Holmes sends reasoning as a single field. AG-UI streams it incrementally. |
| `REASONING_MESSAGE_END` | *(none)* | **Gap** |
| `REASONING_END` | *(none)* | **Gap** |
| `REASONING_ENCRYPTED_VALUE` | *(none)* | **Gap**: AG-UI supports encrypted chain-of-thought for privacy. No Holmes equivalent. |

### 2.6 Activity Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `ACTIVITY_SNAPSHOT` | *(none)* | **Gap**: AG-UI activity messages are frontend-only UI elements (progress/status) that are never forwarded to the agent. Holmes has no equivalent—`token_count` and `compaction_start` are the closest but serve different purposes. |
| `ACTIVITY_DELTA` | *(none)* | **Gap** |

### 2.7 Extension Events

| AG-UI Event | HolmesGPT Equivalent | Gap Analysis |
|-------------|----------------------|--------------|
| `RAW` | *(none)* | **Gap**: AG-UI container for external system events. |
| `CUSTOM` | *(none)* | **Gap**: AG-UI extension mechanism for custom event types. |
| `META_EVENT` | *(none)* | **Gap**: AG-UI side-band annotation events. |

### 2.8 HolmesGPT Events with No AG-UI Equivalent

| HolmesGPT Event | AG-UI Equivalent | Notes |
|------------------|------------------|-------|
| `approval_required` | *(partial via tools)* | **Holmes advantage**: First-class approval workflow with `pending_approvals` array, tool decisions, and resume capability. AG-UI supports human-in-the-loop via tool system but has no dedicated approval event type. |
| `token_count` | *(none)* | **Holmes advantage**: Real-time token usage tracking with `usage`, `tokens`, `max_tokens`, `max_output_tokens`, and cost data. No AG-UI equivalent. |
| `conversation_history_compaction_start` | *(could map to ACTIVITY_SNAPSHOT)* | **Holmes advantage**: Context window management with compaction progress. AG-UI has no built-in context window awareness. |
| `conversation_history_compacted` | *(could map to MESSAGES_SNAPSHOT)* | **Holmes advantage**: Detailed compaction stats (compression ratio, cost, before/after token counts). AG-UI's `MESSAGES_SNAPSHOT` could carry the compacted history but lacks the metadata. |

---

## 3. Detailed Gap Analysis

### 3.1 Gaps in HolmesGPT (AG-UI has, Holmes lacks)

#### Critical Gaps

1. **Token-level text streaming**: AG-UI streams text character-by-character (`TEXT_MESSAGE_CONTENT` deltas). Holmes sends complete messages atomically. This means Holmes clients can't show typing indicators or progressive text rendering.

2. **Shared state management**: AG-UI's `STATE_SNAPSHOT` and `STATE_DELTA` enable bidirectional state sync between agent and frontend using JSON Patch. Holmes is stateless per-request—the client manually manages `conversation_history`. This is a fundamental architectural difference.

3. **Frontend-defined tools**: In AG-UI, the frontend declares what tools are available and the agent calls them. The frontend executes the tool and returns results. Holmes tools are all backend-defined. The experimental adapter works around this by detecting specific tool results (Prometheus queries) and translating them into AG-UI tool calls, but this is hardcoded, not dynamic.

4. **Thread/Run identity**: AG-UI has first-class `thread_id` and `run_id` with parent/child relationships for sub-agents. Holmes has no session or run identity—each request is independent.

#### Moderate Gaps

5. **Step events**: AG-UI's `STEP_STARTED`/`STEP_FINISHED` let the frontend show progress through named phases. Holmes has no equivalent—the closest is the sequence of `start_tool_calling` events.

6. **Reasoning lifecycle**: AG-UI has 6 dedicated reasoning events with streaming and encryption support. Holmes has a `reasoning` field on `ai_message` but no lifecycle or streaming.

7. **Activity events**: AG-UI's activity messages are pure frontend UI (progress spinners, status text) that don't pollute the agent's context. Holmes has no equivalent separation.

8. **Extension mechanisms**: AG-UI's `RAW`, `CUSTOM`, and `META_EVENT` provide extensibility. Holmes has a fixed event set.

9. **Tool argument streaming**: AG-UI's `TOOL_CALL_ARGS` streams tool arguments as they're generated. Holmes doesn't expose tool arguments to the frontend at all.

### 3.2 Gaps in AG-UI (Holmes has, AG-UI lacks)

1. **First-class tool approval workflow**: Holmes has a dedicated `approval_required` event with structured `pending_approvals`, `tool_decisions`, and a resume protocol. AG-UI handles this through generic tool calls (human-in-the-loop tools), which is less structured.

2. **Token/cost tracking**: Holmes's `token_count` event provides real-time LLM resource consumption data. AG-UI has no built-in cost or token awareness.

3. **Context window compaction**: Holmes has dedicated events for conversation history compaction with detailed metrics. AG-UI has no equivalent—context management is left to the implementation.

4. **Rich tool results**: Holmes's `tool_calling_result` includes `status`, `error`, `params`, and `description` alongside `data`. AG-UI's `TOOL_CALL_RESULT` is simpler with just `content`.

5. **Follow-up actions**: Holmes's `ai_answer_end` includes `follow_up_actions` (clickable suggested next steps). AG-UI has no built-in concept for this.

---

## 4. Current Adapter Assessment (`server-agui.py`)

The existing experimental adapter translates Holmes → AG-UI but has significant limitations:

### What it does well
- Wraps Holmes stream in `RUN_STARTED` / `RUN_FINISHED` lifecycle
- Converts `ai_message` and `ai_answer_end` to `TextMessage` events
- Translates specific tool results (Prometheus timeseries) to AG-UI `ToolCall` events for frontend rendering
- Handles errors via `RUN_ERROR`
- Supports frontend tools for query execution (PromQL, PPL)

### What it's missing

| Gap | Impact |
|-----|--------|
| **No token-level streaming** | Text arrives in large chunks, not character-by-character |
| **No state management** | `input_data.state` is logged but ignored |
| **Tool result messages return 200 no-op** | Frontend tool results are discarded (line 103-104) |
| **Hardcoded frontend tool detection** | Only Prometheus and OpenSearch PPL tools trigger AG-UI tool calls (lines 172-209) |
| **No approval workflow translation** | `approval_required` events not mapped to AG-UI |
| **No compaction event translation** | `compaction_start`/`compacted` events silently dropped |
| **No token_count translation** | Token usage events silently dropped |
| **No reasoning event translation** | `ai_message.reasoning` not mapped to AG-UI reasoning events |
| **Tool results truncated to 200 chars** | Line 216: `data[:200]` loses most tool output |
| **No STEP events** | Tool execution phases not mapped to steps |
| **Conversation history lossy** | Tool messages converted to assistant messages (line 398) |

---

## 5. Recommendations

### Short-term (improve existing adapter)

1. **Map `approval_required` → AG-UI tool calls**: Create a frontend "approval" tool that the agent calls when approval is needed. The frontend shows a confirmation dialog and returns the decision.

2. **Map `token_count` → `CUSTOM` events**: Use AG-UI's custom event mechanism to pass token/cost data to the frontend.

3. **Map compaction events → `ACTIVITY_SNAPSHOT`**: Show compaction progress as an activity message.

4. **Remove 200-char tool result truncation**: Let the frontend decide how to display results.

5. **Map `reasoning` → `REASONING_MESSAGE_*` events**: When `ai_message` includes a `reasoning` field, emit proper AG-UI reasoning events.

### Medium-term (protocol alignment)

6. **Add token-level text streaming to Holmes core**: Modify `call_stream` to yield partial text tokens as they arrive from LiteLLM, rather than buffering the full response. This is a core change that benefits both the native SSE protocol and the AG-UI adapter.

7. **Add run/thread identity**: Generate `run_id` per request and optionally accept `thread_id` for conversation continuity. This is lightweight and doesn't require server-side state.

8. **Dynamic frontend tool discovery**: Instead of hardcoding which tools trigger AG-UI tool calls, let the frontend declare tools via `input_data.tools` and match them dynamically against Holmes tool results.

### Long-term (full AG-UI adoption)

9. **Shared state integration**: Implement `STATE_SNAPSHOT`/`STATE_DELTA` to sync investigation state (findings, tool results, context) bidirectionally.

10. **Frontend tool execution**: Allow AG-UI frontends to define and execute tools, with results flowing back into Holmes's investigation loop. This requires handling `tool` role messages from the frontend properly (currently they're converted to assistant messages).

11. **Step events**: Map Holmes's internal investigation phases (tool selection, execution, analysis) to AG-UI steps for richer progress visualization.

---

## 6. Event Count Summary

| Category | AG-UI Events | Holmes Events | Overlap |
|----------|-------------|---------------|---------|
| Lifecycle | 5 | 1 | ~1 |
| Text Messages | 4 | 1 | ~1 |
| Tool Calls | 5 | 2 | ~2 |
| State Management | 3 | 0 | 0 |
| Reasoning | 6 | 0 (field only) | 0 |
| Activity | 2 | 0 | 0 |
| Extensions | 3 | 0 | 0 |
| Holmes-specific | 0 | 5 | 0 |
| **Total** | **28** | **9** | **~4** |

AG-UI has ~28 event types. Holmes has 9. About 4 have direct equivalents. Holmes has 5 unique events (approval, token_count, compaction start/end, error with structured codes) that would need AG-UI `CUSTOM` or `RAW` events to represent.
