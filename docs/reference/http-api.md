# HolmesGPT API Reference

## Overview
The HolmesGPT API provides endpoints for conversational troubleshooting. This document describes each endpoint, its purpose, request fields, and example usage.

## Model Parameter Behavior

When using the API with a Helm deployment, the `model` parameter must reference a model name from your `modelList` configuration in your Helm values, **not** the direct model identifier.

**Example Configuration:**
```yaml
# In your values.yaml
modelList:
  fast-model:
    api_key: "{{ env.ANTHROPIC_API_KEY }}"
    model: anthropic/claude-sonnet-4-5-20250929
    temperature: 0
  accurate-model:
    api_key: "{{ env.ANTHROPIC_API_KEY }}"
    model: anthropic/claude-opus-4-5-20251101
    temperature: 0
```

**Correct API Usage:**
```bash
# Use the modelList key name, not the actual model identifier
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"ask": "list pods", "model": "fast-model"}'
```

**Incorrect Usage:**
```bash
# This will fail - don't use the direct model identifier
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"ask": "list pods", "model": "anthropic/claude-sonnet-4-5-20250929"}'
```

For complete setup instructions with `modelList` configuration, see the [Kubernetes Installation Guide](../installation/kubernetes-installation.md).

---

## Endpoints

### `/api/chat` (POST)
**Description:** General-purpose chat endpoint for interacting with the AI assistant. Supports open-ended questions and troubleshooting.

#### Request Fields

| Field                   | Required | Default | Type      | Description                                      |
|-------------------------|----------|---------|-----------|--------------------------------------------------|
| ask                     | Yes      |         | string    | User's question                                  |
| conversation_history    | No       |         | list      | Conversation history (first message must be system)|
| model                   | No       |         | string    | Model name from your `modelList` configuration  |
| response_format         | No       |         | object    | JSON schema for structured output (see below)   |
| images                  | No       |         | array     | Image URLs, base64 data URIs, or objects with `url` (required), `detail` (low/high/auto), and `format` (MIME type). Requires vision-enabled model. See [Image Analysis](#image-analysis) |
| stream                  | No       | false   | boolean   | Enable streaming response (SSE)                 |
| enable_tool_approval    | No       | false   | boolean   | Require approval for certain tool executions (see [Tool Approval Behavior](#tool-approval-behavior))    |
| frontend_tools          | No       |         | array     | Tools defined by the frontend client (see [Frontend Tools](#frontend-tools)). Requires `stream: true`. |
| frontend_tool_results   | No       |         | array     | Results from frontend-executed tools, sent to resume a paused stream (see [Frontend Tools](#frontend-tools)). |
| additional_system_prompt| No       |         | string    | Additional instructions appended to system prompt|
| behavior_controls       | No       |         | object    | Override prompt sections to enable/disable them (see [Fast Mode & Prompt Controls](#fast-mode-prompt-controls)) |

#### Fast Mode & Prompt Controls

The `behavior_controls` field lets you selectively enable or disable sections of the system and user prompts. This is the API equivalent of the CLI's `--fast-mode` flag and gives you fine-grained control over which prompt components HolmesGPT includes.

**Fast mode example** — skip the TodoWrite planning phase for faster, more direct responses:

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Why is my pod crashing?",
    "behavior_controls": {
      "todowrite_instructions": false,
      "todowrite_reminder": false
    }
  }'
```

**Minimal prompt example** — disable most sections to reduce token usage and latency:

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "List all pods in the default namespace",
    "behavior_controls": {
      "todowrite_instructions": false,
      "todowrite_reminder": false,
      "ai_safety": false,
      "style_guide": false,
      "general_instructions": false
    }
  }'
```

**Precedence rules:**

1. **`ENABLED_PROMPTS` env var** (highest) — If set on the server, it restricts which sections are allowed. The API cannot re-enable a section the env var disables.
2. **`behavior_controls`** — Enables or disables sections within what the env var allows.
3. **Default** (lowest) — All sections are enabled.

The `ENABLED_PROMPTS` env var accepts a comma-separated list of section keys (e.g., `"files,ai_safety,toolset_instructions"`) or `"none"` to disable all sections.

**Available prompt sections:**

| Section Key               | Prompt   | Description                                  |
|---------------------------|----------|----------------------------------------------|
| `intro`                   | System   | Introduction and identity                   |
| `ask_user`                | System   | Instructions for asking clarifying questions |
| `todowrite_instructions`  | System   | TodoWrite planning tool instructions         |
| `ai_safety`               | System   | Safety guidelines (disabled by default)      |
| `toolset_instructions`    | System   | Tool definitions and usage instructions      |
| `permission_errors`       | System   | Permission error handling guidance           |
| `general_instructions`    | System   | General investigation instructions           |
| `style_guide`             | System   | Output formatting and style guide            |
| `cluster_name`            | System   | Kubernetes cluster name context              |
| `system_prompt_additions` | System   | Custom additions from configuration          |
| `files`                   | User     | Attached file contents                       |
| `todowrite_reminder`      | User     | Reminder to use TodoWrite for task tracking  |
| `time_runbooks`           | User     | Runbook content and custom instructions      |

#### Structured Output with `response_format`

The `response_format` field allows you to request structured JSON output from the AI. This is useful when you need the response in a specific format for programmatic processing.

!!! note
    Always include `"strict": true` in your `json_schema` to ensure the response matches your schema exactly.

**Format:**

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "YourSchemaName",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "field1": {"type": "string", "description": "Description of field1"},
        "field2": {"type": "boolean", "description": "Description of field2"}
      },
      "required": ["field1", "field2"],
      "additionalProperties": false
    }
  }
}
```

**Example with Structured Output:**

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Is the cluster healthy?",
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "ClusterHealthResult",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "is_healthy": {"type": "boolean", "description": "Whether the cluster is healthy"},
            "summary": {"type": "string", "description": "Brief summary of cluster status"},
            "issues": {"type": "array", "items": {"type": "string"}, "description": "List of issues found"}
          },
          "required": ["is_healthy", "summary", "issues"],
          "additionalProperties": false
        }
      }
    }
  }'
```

**Example Response with Structured Output:**

```json
{
  "analysis": "{\"is_healthy\": true, \"summary\": \"All nodes are ready and workloads running normally.\", \"issues\": []}",
  "conversation_history": [...],
  "tool_calls": [...],
  "follow_up_actions": [...]
}
```

!!! note
    When using `response_format`, the `analysis` field in the response will contain a JSON string matching your schema. You'll need to parse this JSON string to access the structured data.

**Example without Structured Output:**

<!-- test: status=200, has_fields=analysis|conversation_history, id=chat_basic -->
```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "What is the status of my cluster?",
    "conversation_history": [
      {"role": "system", "content": "You are a helpful assistant."}
    ]
  }'
```

**Example Response:**

```json
{
  "analysis": "Your cluster is healthy. All nodes are ready and workloads are running as expected.",
  "conversation_history": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the status of my cluster?"},
    {"role": "assistant", "content": "Your cluster is healthy. All nodes are ready and workloads are running as expected."}
  ],
  "tool_calls": [...],
  "follow_up_actions": [...]
}
```

#### Image Analysis

The `/api/chat` endpoint supports image analysis with vision-enabled models. Include images as URLs or base64 data URIs in the `images` field.

**Image Formats:**

Images can be provided as:
- **Simple strings**: URLs or base64 data URIs
- **Dict format**: Objects with `url`, `detail`, and `format` fields for advanced control

**Example with Image URL:**

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "What is in this image?",
    "model": "anthropic/claude-sonnet-4-5-20250929",
    "images": [
      "https://example.com/screenshot.png"
    ]
  }'
```

**Example with Base64 Data URI:**

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Analyze this diagram",
    "model": "anthropic/claude-sonnet-4-5-20250929",
    "images": [
      "data:image/png;base64,iVBORw0KGgoAAAANS..."
    ]
  }'
```

**Example with Advanced Format:**

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Compare these images",
    "model": "anthropic/claude-opus-4-5-20251101",
    "images": [
      "https://example.com/before.png",
      {
        "url": "https://example.com/after.png",
        "detail": "high"
      }
    ]
  }'
```

**Vision Model Support:**

Vision capabilities are available in recent models from major providers including OpenAI (GPT-4o and later), Anthropic (Claude 4.5 family and later), Google (Gemini family), and others supported by LiteLLM.

For the most up-to-date list of vision-enabled models, see the [LiteLLM Vision Documentation](https://docs.litellm.ai/docs/completion/vision).

**Advanced Parameters (dict format only):**

| Field  | Type   | Description                                              |
|--------|--------|----------------------------------------------------------|
| url    | string | Image URL or base64 data URI (required)                |
| detail | string | OpenAI-specific: `low`, `high`, or `auto` for resolution control |
| format | string | MIME type (e.g., `image/jpeg`) for providers that need explicit format |

#### Tool Approval Behavior

The `enable_tool_approval` field controls how HolmesGPT handles tools that require approval (e.g., bash commands not in the allow list, or commands that bashlex cannot parse).

**When `enable_tool_approval: true` (interactive clients):**

The stream pauses and emits an `approval_required` event with the pending tool calls. The client must send a follow-up request with `tool_decisions` to approve or deny each tool call. See the [approval_required](#approval_required) event for details.

**When `enable_tool_approval: false` (default, server/automation):**

Tools that would require approval are automatically converted to errors. The error message is fed back to the LLM as a tool result, giving it a chance to self-correct and retry with a valid command. For example, if the LLM generates a bash command with unquoted special characters that can't be parsed, it receives an error and can retry with proper quoting.

This means server-mode integrations (e.g., Keep workflows) do not need a human in the loop — the LLM handles recoverable validation failures automatically.

#### Frontend Tools

Frontend tools let the client define tools that the LLM can call, but that execute on the **client side** rather than on the Holmes server. This enables use cases like rendering charts, navigating UIs, querying client-local databases, or any action that requires client-side execution.

Frontend tools have two modes:

- **`pause`** (default): The stream pauses when the LLM calls the tool. The client executes the tool and resumes by sending results back. The LLM receives real results and continues reasoning with that data.
- **`noop`**: The server returns a canned response immediately and the LLM continues without pausing. The client sees the tool call in SSE events (`start_tool_calling` + `tool_calling_result`) and can execute it as a fire-and-forget side effect.

**Pause mode spans two HTTP requests.** What would normally be a single request is split across a request–pause–resume cycle:

1. **Request 1** — the client sends `ask` + `frontend_tools`. The server streams SSE events until the LLM calls a pause-mode tool, then emits an `approval_required` event containing `pending_frontend_tool_calls` and `conversation_history`. The stream ends here.
2. The client executes the tool locally (render a chart, query a local DB, etc.).
3. **Request 2** — the client sends a new POST to `/api/chat` with the `conversation_history` from request 1, plus `frontend_tool_results` containing the tool output. The server feeds the results back to the LLM, which continues reasoning and streams the rest of its answer.

If the LLM calls multiple pause-mode tools in one iteration, they all appear in a single `approval_required` event — the client executes all of them and sends all results together in request 2. If the LLM calls another pause-mode tool in a later iteration, the cycle repeats (request 3, 4, etc.).

Noop-mode tools do **not** pause — the entire request completes without interruption, regardless of how many LLM iterations it takes.

**Declaring frontend tools:**

Each tool in the `frontend_tools` array has:

| Field         | Required | Default | Type   | Description                                           |
|---------------|----------|---------|--------|-------------------------------------------------------|
| name          | Yes      |         | string | Tool name (must not conflict with built-in tool names)|
| description   | Yes      |         | string | Description shown to the LLM                         |
| parameters    | No       |         | object | JSON Schema describing the tool's parameters          |
| mode          | No       | pause   | string | `"pause"` or `"noop"`                                |
| noop_response | No       |         | string | Custom canned response for noop-mode tools            |

**Example with both modes:**

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Show me a CPU usage chart and navigate to the dashboards page",
    "stream": true,
    "frontend_tools": [
      {
        "name": "render_chart",
        "description": "Render a chart in the user interface. Returns chart metadata.",
        "mode": "pause",
        "parameters": {
          "type": "object",
          "properties": {
            "chart_type": {"type": "string", "description": "Type of chart (line, bar, pie)"},
            "data_source": {"type": "string", "description": "Metric or data source to chart"},
            "time_range": {"type": "string", "description": "Time range (e.g. 1h, 24h, 7d)"}
          }
        }
      },
      {
        "name": "navigate_to_page",
        "description": "Navigate the user to a page in the application.",
        "mode": "noop",
        "noop_response": "Navigation triggered successfully.",
        "parameters": {
          "type": "object",
          "properties": {
            "page": {"type": "string", "description": "Page path (e.g. /dashboards, /alerts)"}
          }
        }
      }
    ]
  }'
```

**Pause-mode: resuming after frontend tool execution:**

When the stream pauses, the `approval_required` event contains `pending_frontend_tool_calls` with the tool name, call ID, and arguments. Execute the tool client-side, then resume:

```bash
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "Show me a CPU usage chart for the last hour",
    "stream": true,
    "conversation_history": [...],
    "frontend_tool_results": [
      {
        "tool_call_id": "call_abc123",
        "tool_name": "render_chart",
        "result": "{\"rendered\": true, \"chart_url\": \"/charts/cpu-1h.png\"}"
      }
    ]
  }'
```

**Noop-mode: no resume needed:**

Noop tools execute instantly on the server with a canned response. The client sees the tool call in `start_tool_calling` and `tool_calling_result` SSE events and can act on them (e.g., navigate to a page), but the LLM continues without waiting.

**Constraints:**

- Pause-mode tools require `stream: true` (returns HTTP 400 otherwise). Noop-mode tools work with both streaming and non-streaming.
- Frontend tool names must not conflict with built-in Holmes tool names (returns HTTP 400)
- `frontend_tool_results.result` must be a string (JSON-encode objects)
- Both `pending_approvals` and `pending_frontend_tool_calls` can appear in the same `approval_required` event if the LLM calls both types in one iteration

#### Implementing Frontend Tools in Your Client

This section walks through building client-side support for pause-mode frontend tools. The key thing to understand is that what would normally be a single request is split across **two HTTP requests**: the first streams until the LLM calls your tool, and the second resumes the LLM after you return results.

**1. Define your tools in the request**

Pass tool definitions in the `frontend_tools` array. Each tool needs a `name`, `description`, and optionally `parameters` (JSON Schema) and `mode`.

```javascript
const frontendTools = [
  {
    name: "render_chart",
    description: "Render a chart in the UI with the given metric and time range.",
    mode: "pause",
    parameters: {
      type: "object",
      properties: {
        chart_type: { type: "string", enum: ["line", "bar", "pie"] },
        metric: { type: "string" },
        time_range: { type: "string" }
      },
      required: ["chart_type", "metric"]
    }
  },
  {
    name: "navigate_to_page",
    description: "Navigate the user to a page in the application.",
    mode: "noop",
    noop_response: "Navigation triggered."
  }
];
```

**2. Send the streaming request and parse SSE events**

Since `/api/chat` is a POST endpoint, use an SSE library that supports POST requests, such as [fetch-event-source](https://github.com/Azure/fetch-event-source) or [sse.js](https://github.com/mpetazzoni/sse.js).

This helper is called for both request 1 (initial) and request 2 (resume with tool results):

```javascript
import { fetchEventSource } from "@microsoft/fetch-event-source";

function streamChat({ ask, conversationHistory, frontendToolResults }) {
  fetchEventSource("http://<HOLMES-URL>/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ask,
      stream: true,
      frontend_tools: frontendTools,
      conversation_history: conversationHistory,       // undefined on first request
      frontend_tool_results: frontendToolResults,       // undefined on first request
    }),
    onmessage(event) {
      const payload = JSON.parse(event.data);
      handleEvent(event.event, payload);
    },
  });
}
```

**3. Handle the stream pause (end of request 1)**

When the LLM calls a pause-mode frontend tool, the stream emits an `approval_required` event with `pending_frontend_tool_calls` and then ends. Execute the tool locally, then start request 2 to resume.

```javascript
function handleEvent(eventType, payload) {
  switch (eventType) {
    case "approval_required":
      // Handle frontend tool calls
      if (payload.pending_frontend_tool_calls?.length > 0) {
        handleFrontendToolCalls(
          payload.pending_frontend_tool_calls,
          payload.conversation_history
        );
      }
      break;

    case "start_tool_calling":
      console.log(`Tool started: ${payload.tool_name}`);
      break;

    case "tool_calling_result":
      console.log(`Tool result: ${payload.name}`, payload.result);
      break;

    case "ai_message":
      renderMarkdown(payload.content);
      break;

    case "ai_answer_end":
      // Store conversation_history for follow-up messages
      saveConversationHistory(payload.conversation_history);
      break;
  }
}
```

**4. Execute tools and send request 2 to resume**

For each pending frontend tool call, run your local implementation, then open a new stream (request 2) with the results. The server feeds the results back to the LLM, which continues its answer.

```javascript
async function handleFrontendToolCalls(pendingCalls, conversationHistory) {
  const results = [];

  for (const call of pendingCalls) {
    let result;
    switch (call.tool_name) {
      case "render_chart":
        result = await renderChartInUI(call.arguments);
        break;
      default:
        result = { error: `Unknown tool: ${call.tool_name}` };
    }

    results.push({
      tool_call_id: call.tool_call_id,
      tool_name: call.tool_name,
      result: JSON.stringify(result)  // Must be a string
    });
  }

  // Request 2: resume the LLM with tool results
  streamChat({
    ask: originalQuestion,
    conversationHistory,
    frontendToolResults: results,
  });
}
```

**5. Handle noop-mode tools (fire-and-forget)**

Noop tools don't pause the stream. Watch for `start_tool_calling` events and execute side effects.

```javascript
case "start_tool_calling":
  if (payload.tool_name === "navigate_to_page") {
    // Fire-and-forget: execute without blocking the stream
    navigateToPage(payload);
  }
  break;
```

**Key implementation notes:**

- **Re-send `frontend_tools`** on every request, including resume requests — tool definitions are not persisted server-side
- **`result` must be a string** — JSON-encode objects before sending
- **`conversation_history`** from the `approval_required` event must be passed back when resuming
- **Mixed pauses**: If both `pending_approvals` and `pending_frontend_tool_calls` appear in the same event, send both `tool_decisions` and `frontend_tool_results` in the resume request
- **Error handling**: If your tool fails, return a JSON error string as the `result` — the LLM will see it and can adapt

---

### `/api/model` (GET)
**Description:** Returns a list of available AI models that can be used for chat.

**Example**
<!-- test: status=200, has_fields=model_name, id=model_list -->
```bash
curl http://<HOLMES-URL>/api/model
```

**Example** Response
```json
{
  "model_name": ["anthropic/claude-sonnet-4-5-20250929", "anthropic/claude-opus-4-5-20251101", "robusta"]
}
```

---

## Server-Sent Events (SSE) Reference

Streaming endpoints (e.g., `/api/chat` with `stream: true`) emit Server-Sent Events (SSE) to provide real-time updates during the chat process.

### Metadata Object Reference

Many events include a `metadata` object that provides detailed information about token usage, context window limits, and message truncation. This section describes the complete structure of the metadata object.

#### Token Usage Information

**Structure:**
```json
{
  "metadata": {
    "usage": {
      "prompt_tokens": 2500,
      "completion_tokens": 150,
      "total_tokens": 2650
    },
    "tokens": {
      "total_tokens": 2650,
      "tools_tokens": 100,
      "system_tokens": 500,
      "user_tokens": 300,
      "tools_to_call_tokens": 50,
      "assistant_tokens": 1600,
      "other_tokens": 100
    },
    "max_tokens": 128000,
    "max_output_tokens": 16384
  }
}
```

**Fields:**

- `usage` (object): Token usage from the LLM provider (raw response from the model)
  - `prompt_tokens` (integer): Tokens in the prompt (input)
  - `completion_tokens` (integer): Tokens in the completion (output)
  - `total_tokens` (integer): Total tokens used (prompt + completion)

- `tokens` (object): HolmesGPT's detailed token count breakdown by message role
  - `total_tokens` (integer): Total tokens in the conversation
  - `tools_tokens` (integer): Tokens used by tool definitions
  - `system_tokens` (integer): Tokens in system messages
  - `user_tokens` (integer): Tokens in user messages
  - `tools_to_call_tokens` (integer): Tokens used for tool call requests from the assistant
  - `assistant_tokens` (integer): Tokens in assistant messages (excluding tool calls)
  - `other_tokens` (integer): Tokens from other message types

- `max_tokens` (integer): Maximum context window size for the model
- `max_output_tokens` (integer): Maximum tokens reserved for model output

#### Truncation Information

When messages are truncated to fit within context limits, the metadata includes truncation details:

**Structure:**
```json
{
  "metadata": {
    "truncations": [
      {
        "tool_call_id": "call_abc123",
        "start_index": 0,
        "end_index": 5000,
        "tool_name": "kubectl_logs",
        "original_token_count": 15000
      }
    ]
  }
}
```

**Fields:**

- `truncations` (array): List of truncated tool results
  - `tool_call_id` (string): ID of the truncated tool call
  - `start_index` (integer): Character index where truncation starts (always 0)
  - `end_index` (integer): Character index where content was cut off
  - `tool_name` (string): Name of the tool whose output was truncated
  - `original_token_count` (integer): Original token count before truncation

Truncated content will include a `[TRUNCATED]` marker at the end.

---

### Event Types

#### `start_tool_calling`

Emitted when the AI begins executing a tool. This event is sent before the tool runs.

**Payload:**
```json
{
  "tool_name": "kubectl_describe",
  "id": "call_abc123"
}
```

**Fields:**

- `tool_name` (string): The name of the tool being called
- `id` (string): Unique identifier for this tool call

---

#### `tool_calling_result`

Emitted when a tool execution completes. Contains the tool's output and metadata.

**Payload:**
```json
{
  "tool_call_id": "call_abc123",
  "role": "tool",
  "description": "kubectl describe pod my-pod -n default",
  "name": "kubectl_describe",
  "result": {
    "status": "success",
    "data": "...",
    "error": null,
    "params": {"pod": "my-pod", "namespace": "default"}
  }
}
```

**Fields:**

- `tool_call_id` (string): Unique identifier matching the `start_tool_calling` event
- `role` (string): Always "tool"
- `description` (string): Human-readable description of what the tool did
- `name` (string): The name of the tool that was called
- `result` (object): Tool execution result
  - `status` (string): One of "success", "error", "approval_required"
  - `data` (string|object): The tool's output data (stringified if complex)
  - `error` (string|null): Error message if the tool failed
  - `params` (object): Parameters that were passed to the tool

---

#### `ai_message`

Emitted when the AI has a text message or reasoning to share (typically before tool calls).

**Payload:**
```json
{
  "content": "I need to check the pod logs to understand the issue.",
  "reasoning": "The pod is crashing, so examining logs will reveal the root cause.",
  "metadata": {...}
}
```

**Fields:**

- `content` (string|null): The AI's message content
- `reasoning` (string|null): The AI's internal reasoning (only present for models that support reasoning like o1)
- `metadata` (object): See [Metadata Object Reference](#metadata-object-reference) for complete structure

---

#### `ai_answer_end`

Emitted when the chat is complete. This is the final event in the stream.

**Payload:**
```json
{
  "analysis": "The issue can be resolved by...",
  "conversation_history": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "follow_up_actions": [
    {
      "id": "action1",
      "action_label": "Run diagnostics",
      "pre_action_notification_text": "Running diagnostics...",
      "prompt": "Run diagnostic checks"
    }
  ],
  "metadata": {...}
}
```

**Fields:**

- `metadata` (object): See [Metadata Object Reference](#metadata-object-reference) for complete structure including token usage, truncations, and compaction info
- `analysis` (string): The AI's response (markdown format)
- `conversation_history` (array): Complete conversation history including the latest response
- `follow_up_actions` (array|null): Optional follow-up actions the user can take
  - `id` (string): Unique identifier for the action
  - `action_label` (string): Display label for the action
  - `pre_action_notification_text` (string): Text to show before executing the action
  - `prompt` (string): The prompt to send when the action is triggered

---

#### `approval_required`

Emitted when the stream needs to pause for external action — either tool approval (destructive operations) or frontend tool execution. The stream pauses until the client sends a follow-up request.

**Payload:**
```json
{
  "content": null,
  "conversation_history": [...],
  "follow_up_actions": [...],
  "requires_approval": true,
  "pending_approvals": [
    {
      "tool_call_id": "call_xyz789",
      "tool_name": "kubectl_delete",
      "description": "kubectl delete pod failed-pod -n default",
      "params": {"pod": "failed-pod", "namespace": "default"}
    }
  ],
  "pending_frontend_tool_calls": [
    {
      "tool_call_id": "call_abc123",
      "tool_name": "show_chart",
      "arguments": {"chart_type": "line", "data_source": "cpu_usage"}
    }
  ]
}
```

**Fields:**

- `content` (null): No AI content when approval is required
- `conversation_history` (array): Current conversation state
- `follow_up_actions` (array|null): Optional follow-up actions
- `requires_approval` (boolean): Always true for this event
- `pending_approvals` (array): List of tools awaiting user approval
  - `tool_call_id` (string): Unique identifier for the tool call
  - `tool_name` (string): Name of the tool requiring approval
  - `description` (string): Human-readable description
  - `params` (object): Parameters for the tool call
- `pending_frontend_tool_calls` (array): List of frontend tools awaiting client execution (see [Frontend Tools](#frontend-tools))
  - `tool_call_id` (string): Unique identifier for the tool call
  - `tool_name` (string): Name of the frontend tool to execute
  - `arguments` (object): Arguments the LLM passed to the tool

**Resuming after tool approval:**
```json
{
  "conversation_history": [...],
  "tool_decisions": [
    {"tool_call_id": "call_xyz789", "approved": true}
  ]
}
```

**Resuming after frontend tool execution:**
```json
{
  "conversation_history": [...],
  "frontend_tool_results": [
    {
      "tool_call_id": "call_abc123",
      "tool_name": "show_chart",
      "result": "{\"rendered\": true, \"data_points\": 42}"
    }
  ]
}
```

---

#### `token_count`

Emitted periodically to provide token usage updates during the chat. This event is sent after each LLM iteration to help track resource consumption in real-time.

**Payload:**
```json
{
  "metadata": {...}
}
```

**Fields:**

- `metadata` (object): See [Metadata Object Reference](#metadata-object-reference) for complete token usage structure. This event provides the same metadata structure as other events, allowing you to monitor token consumption throughout the chat

---

#### `conversation_history_compaction_start`

Emitted when the conversation history is about to be compacted. This event fires before the compaction LLM call, allowing clients to show a loading state.

**Payload:**
```json
{
  "content": "Compacting conversation history (150000 tokens, 42 messages)...",
  "metadata": {
    "initial_tokens": 150000,
    "num_messages": 42,
    "max_context_size": 128000,
    "threshold_pct": 95
  }
}
```

**Fields:**

- `content` (string): Human-readable status message
- `metadata` (object): Context window state before compaction
  - `initial_tokens` (integer): Current token count triggering compaction
  - `num_messages` (integer): Number of messages in the conversation
  - `max_context_size` (integer): Model's maximum context window size
  - `threshold_pct` (integer): Context window usage percentage that triggered compaction

---

#### `conversation_history_compacted`

Emitted when the conversation history has been compacted to fit within the context window. This happens automatically when the conversation grows too large. Contains detailed statistics about the compaction result.

**Payload:**
```json
{
  "content": "The conversation history has been compacted from 150000 to 80000 tokens",
  "compaction_summary": "<analysis>\n1. Primary Request: User asked to investigate pod crashes...\n2. Key Technical Concepts: OOMKilled, memory limits...\n...\n</analysis>",
  "messages": [...],
  "metadata": {
    "initial_tokens": 150000,
    "compacted_tokens": 80000,
    "compression_ratio_pct": 46.7,
    "num_messages_before": 42,
    "num_messages_after": 4,
    "max_context_size": 128000,
    "threshold_pct": 95,
    "compaction_cost": {
      "total_cost": 0.003542,
      "prompt_tokens": 12000,
      "completion_tokens": 800,
      "total_tokens": 12800
    }
  }
}
```

**Fields:**

- `content` (string): Human-readable description of the compaction
- `compaction_summary` (string|null): The LLM-generated summary of the previous conversation history. This is the full text the model produced to condense the conversation, wrapped in `<analysis>` tags. Useful for debugging to verify that important context was preserved during compaction.
- `messages` (array): The compacted conversation history
- `metadata` (object): Detailed compaction statistics
  - `initial_tokens` (integer): Token count before compaction
  - `compacted_tokens` (integer): Token count after compaction
  - `compression_ratio_pct` (number): Percentage of tokens saved (e.g., 46.7 means 46.7% reduction)
  - `num_messages_before` (integer): Number of messages before compaction
  - `num_messages_after` (integer): Number of messages after compaction (typically 3-4)
  - `max_context_size` (integer): Model's maximum context window size
  - `threshold_pct` (integer): Context window usage percentage that triggered compaction
  - `compaction_cost` (object, optional): Cost of the compaction LLM call
    - `total_cost` (number): Dollar cost of the compaction call
    - `prompt_tokens` (integer): Prompt tokens used for compaction
    - `completion_tokens` (integer): Completion tokens generated during compaction
    - `total_tokens` (integer): Total tokens used for compaction

---

#### `error`

Emitted when an error occurs during processing.

**Payload:**
```json
{
  "description": "Rate limit exceeded",
  "error_code": 5204,
  "msg": "Rate limit exceeded",
  "success": false
}
```

**Fields:**

- `description` (string): Detailed error description
- `error_code` (integer): Numeric error code
- `msg` (string): Error message
- `success` (boolean): Always false

**Common Error Codes:**

- `5204`: Rate limit exceeded
- `1`: Generic error

---

## Event Flow Examples

### Chat with Approval Flow

```
1. ai_message
2. start_tool_calling (safe tool)
3. start_tool_calling (requires approval)
4. tool_calling_result (safe tool)
5. tool_calling_result (approval required with status: "approval_required")
6. approval_required
[Client sends approval decisions]
1. tool_calling_result (approved tool executed)
[chat resumes]
```

### Chat with Frontend Pause Tool

```
1. ai_message
2. start_tool_calling (backend tool)
3. start_tool_calling (frontend pause tool)
4. tool_calling_result (backend tool)
5. token_count
6. approval_required (pending_frontend_tool_calls populated)
[Client executes frontend tool locally]
[Client sends new request with frontend_tool_results + conversation_history]
1. tool_calling_result (frontend tool result injected)
2. ai_message
3. token_count
4. ai_answer_end
```

### Chat with Frontend Noop Tool

```
1. ai_message
2. start_tool_calling (noop tool)
3. tool_calling_result (noop tool - canned response, no pause)
4. token_count
5. ai_message
6. ai_answer_end
[Client sees start_tool_calling + tool_calling_result and executes side effect]
```

### Chat with History Compaction

```
1. conversation_history_compaction_start
2. conversation_history_compacted
3. ai_message (compaction notice)
4. start_tool_calling (tool 1)
5. tool_calling_result (tool 1)
6. token_count
7. ai_answer_end
```
