# Frontend Tools Integration Guide

This document is a complete reference for implementing HolmesGPT frontend tools in a client application. It covers the wire protocol, SSE event handling, and the full pause-resume lifecycle.

## Overview

Frontend tools allow your client to define tools that the HolmesGPT LLM can call, but that execute **on the client side** rather than on the Holmes server. When the LLM decides to call a frontend tool, the server pauses the SSE stream and asks your client to execute the tool and return results.

**Use cases:**

- Rendering charts or visualizations in the UI
- Navigating the user to a specific page or view
- Querying client-local databases or APIs
- Prompting the user for additional input
- Any action that requires client-side access or UI interaction

## Two Types of Frontend Tools

### Pause-mode tools (implemented)

The LLM calls the tool, the server **pauses** the stream, and your client executes the tool and sends results back. The LLM receives real results from your execution and continues reasoning with that data.

**When to use:** The LLM needs real data back from the tool execution (e.g., query results, user input, computed values).

### Noop-mode tools (future)

The LLM calls the tool, the server returns a canned response immediately, and the LLM continues without pausing. Your client sees the tool call in the SSE events and can execute it as a side effect, but the LLM doesn't wait for or receive real results.

**When to use:** Fire-and-forget actions where the LLM doesn't need results (e.g., "navigate to dashboard", "highlight this section").

**Note:** Noop-mode tools are not yet implemented. This guide covers pause-mode tools only.

## Wire Protocol

### Step 1: Declare frontend tools in the request

Send `frontend_tools` in your `/api/chat` request with `stream: true`:

```json
{
  "ask": "Show me CPU usage for the last hour",
  "stream": true,
  "frontend_tools": [
    {
      "name": "render_chart",
      "description": "Render a chart in the user's browser. Returns the chart URL and metadata.",
      "parameters": {
        "type": "object",
        "properties": {
          "chart_type": {
            "type": "string",
            "description": "Type of chart: line, bar, pie, area"
          },
          "data_source": {
            "type": "string",
            "description": "Metric name or data source identifier"
          },
          "time_range": {
            "type": "string",
            "description": "Time range for the chart (e.g., 1h, 24h, 7d)"
          }
        }
      }
    },
    {
      "name": "navigate_to_page",
      "description": "Navigate the user to a specific page in the application.",
      "parameters": {
        "type": "object",
        "properties": {
          "page": {
            "type": "string",
            "description": "Page path (e.g., /dashboards/cpu, /alerts)"
          }
        }
      }
    }
  ]
}
```

**Constraints:**

- `stream: true` is required (HTTP 400 if false)
- Tool names must not conflict with built-in Holmes tool names (HTTP 400 if they do)
- `parameters` follows [OpenAI function calling JSON Schema format](https://platform.openai.com/docs/guides/function-calling)
- Write good `description` fields — this is what the LLM uses to decide when to call the tool

### Step 2: Listen for SSE events

Connect to the SSE stream and process events. The key events for frontend tools are:

**`start_tool_calling`** — emitted when the LLM decides to call any tool:

```
event: start_tool_calling
data: {"tool_name": "render_chart", "id": "call_abc123"}
```

**`approval_required`** — emitted when the stream pauses for frontend tool execution:

```
event: approval_required
data: {
  "analysis": null,
  "conversation_history": [...],
  "follow_up_actions": [...],
  "requires_approval": true,
  "pending_approvals": [],
  "pending_frontend_tool_calls": [
    {
      "tool_call_id": "call_abc123",
      "tool_name": "render_chart",
      "arguments": {
        "chart_type": "line",
        "data_source": "cpu_usage",
        "time_range": "1h"
      }
    }
  ]
}
```

**How to distinguish frontend pauses from approval pauses:**

- `pending_frontend_tool_calls` is non-empty: frontend tools need execution
- `pending_approvals` is non-empty: backend tools need user approval
- Both can be non-empty in the same event (handle both)

### Step 3: Execute the tool client-side

When you receive an `approval_required` event with `pending_frontend_tool_calls`:

1. Extract each tool call from `pending_frontend_tool_calls`
2. Execute the tool in your application using `tool_name` and `arguments`
3. Collect the result as a **string** (JSON-encode objects)
4. Save the `conversation_history` from the event — you need it for the resume request

### Step 4: Resume the stream

Send a new request with the tool results and conversation history:

```json
{
  "ask": "Show me CPU usage for the last hour",
  "stream": true,
  "conversation_history": [...],
  "frontend_tool_results": [
    {
      "tool_call_id": "call_abc123",
      "tool_name": "render_chart",
      "result": "{\"rendered\": true, \"chart_url\": \"/charts/cpu-1h.png\", \"data_points\": 60}"
    }
  ]
}
```

**Important:**

- `conversation_history` must be the exact array from the `approval_required` event
- `tool_call_id` must match exactly
- `result` must be a string — JSON-encode objects
- Include results for ALL pending frontend tool calls (missing results get an error injected)
- You can include `frontend_tools` again if you want the tools available for subsequent LLM iterations

The stream resumes and the LLM continues with your results injected into its conversation.

## Complete Event Flow

```
Request 1: {ask, stream: true, frontend_tools: [...]}
  ← event: start_tool_calling  {tool_name: "kubectl_get", id: "call_1"}
  ← event: start_tool_calling  {tool_name: "render_chart", id: "call_2"}
  ← event: tool_calling_result {tool_call_id: "call_1", ...}  (backend tool executed)
  ← event: token_count         {...}
  ← event: approval_required   {pending_frontend_tool_calls: [{tool_call_id: "call_2", ...}]}
  [stream ends]

[Client executes render_chart locally]

Request 2: {ask, stream: true, conversation_history: [...], frontend_tool_results: [...]}
  ← event: tool_calling_result {tool_call_id: "call_2", ...}  (frontend result injected)
  ← event: ai_message          {content: "The chart shows..."}
  ← event: token_count         {...}
  ← event: ai_answer_end       {analysis: "Based on the CPU chart...", ...}
  [stream ends]
```

## Implementation Checklist

- [ ] Add `frontend_tools` to your chat request with `stream: true`
- [ ] Parse SSE events from the stream (`start_tool_calling`, `tool_calling_result`, `approval_required`, `ai_answer_end`)
- [ ] When `approval_required` fires, check `pending_frontend_tool_calls`
- [ ] Execute each pending tool client-side using `tool_name` and `arguments`
- [ ] Send a resume request with `frontend_tool_results` and the saved `conversation_history`
- [ ] Handle the case where both `pending_approvals` and `pending_frontend_tool_calls` are present
- [ ] Handle errors: if your tool execution fails, still send a result (with an error description as the `result` string) so the LLM can self-correct

## Error Handling

**Tool execution fails on your side:** Send the error as the result string. The LLM will see the error and can adjust:

```json
{
  "frontend_tool_results": [
    {
      "tool_call_id": "call_abc123",
      "tool_name": "render_chart",
      "result": "Error: chart rendering failed - data source 'cpu_usage' not found"
    }
  ]
}
```

**Missing results:** If you don't include a result for a pending tool call, Holmes injects an error message automatically: `"Error: frontend did not return a result for this tool call."` The LLM may retry or adjust.

**Name collision:** If a `frontend_tools` name matches a built-in Holmes tool name, the request returns HTTP 400.
