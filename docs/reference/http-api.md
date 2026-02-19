# HolmesGPT API Reference

## Overview
The HolmesGPT API provides endpoints for automated investigations and conversational troubleshooting. This document describes each endpoint, its purpose, request fields, and example usage.

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
| enable_tool_approval    | No       | false   | boolean   | Require approval for certain tool executions    |
| additional_system_prompt| No       |         | string    | Additional instructions appended to system prompt|

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

```bash
<!-- test: status=200, has_fields=analysis|conversation_history, id=chat_basic -->
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

---

### `/api/investigate` (POST)
**Description:** Initiate an automated investigation of an issue or incident.

#### Request Fields

| Field                   | Required | Default                                    | Type      | Description                                      |
|-------------------------|----------|--------------------------------------------|-----------|--------------------------------------------------|
| source                  | Yes      |                                            | string    | Source of the issue (e.g., "prometheus")         |
| title                   | Yes      |                                            | string    | Title of the investigation                       |
| description             | Yes      |                                            | string    | Description of the issue                         |
| subject                 | Yes      |                                            | object    | Subject details (e.g., resource info)            |
| context                 | Yes      |                                            | object    | Additional context                               |
| source_instance_id      | No       | "ApiRequest"                               | string    | Source instance identifier                       |
| include_tool_calls      | No       | false                                      | boolean   | Include tool calls in response                   |
| include_tool_call_results| No      | false                                      | boolean   | Include tool call results in response            |
| prompt_template         | No       | "builtin://generic_investigation.jinja2"   | string    | Prompt template to use                           |
| sections                | No       |                                            | object    | Structured output sections                       |
| model                   | No       |                                            | string    | Model name from your `modelList` configuration  |

**Example**
```bash
curl -X POST http://<HOLMES-URL>/api/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "source": "prometheus",
    "title": "Pod CrashLoopBackOff",
    "description": "Pod is crashing repeatedly",
    "subject": {"namespace": "default", "pod": "my-pod"},
    "context": {},
    "include_tool_calls": true,
    "model": "claude-sonnet"
  }'
```

> **Note:** The `model` value must be a key name from your `modelList` configuration, not a direct model identifier.

**Example** Response
```json
{
  "analysis": "The pod 'my-pod' in namespace 'default' is crashing due to an OOMKilled event. Consider increasing memory limits.",
  "sections": {
    "Alert Explanation": "...",
    "Key Findings": "...",
    "Conclusions and Possible Root Causes": "...",
    "Next Steps": "...",
    "App or Infra?": "...",
    "External links": "..."
  },
  "tool_calls": [
    {
      "tool_call_id": "1",
      "tool_name": "kubectl_logs",
      "description": "Fetch pod logs",
      "result": {"logs": "..."}
    }
  ],
  "instructions": [...]
}
```

---

### `/api/stream/investigate` (POST)
**Description:** Same as `/api/investigate`, but returns results as a stream for real-time updates.

#### Request Fields
Same as [`/api/investigate`](#apiinvestigate-post).

**Example**
```bash
curl -N -X POST http://<HOLMES-URL>/api/stream/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "source": "prometheus",
    "title": "Pod CrashLoopBackOff",
    "description": "Pod is crashing repeatedly",
    "subject": {"namespace": "default", "pod": "my-pod"},
    "context": {},
    "include_tool_calls": true,
    "model": "claude-sonnet"
  }'
```

**Example** Response (streamed)
```bash
event: start_tool_calling
data: {"tool_name": "kubectl_describe", "id": "call_0"}

event: start_tool_calling
data: {"tool_name": "kubectl_logs", "id": "call_1"}

event: start_tool_calling
data: {"tool_name": "kubectl_previous_logs", "id": "call_2"}

event: start_tool_calling
data: {"tool_name": "kubectl_memory_requests_namespace", "id": "call_3"}

event: tool_calling_result
data: {"tool_call_id": "call_3", "role": "tool", "description": "kubectl get pods -n default -o ...", "name": "kubectl_memory_requests_namespace", "result": {...}}

event: tool_calling_result
data: {"tool_call_id": "call_0", "role": "tool", "description": "kubectl describe pod my-pod -n default", "name": "kubectl_describe", "result": {...}}

event: tool_calling_result
data: {"tool_call_id": "call_2", "role": "tool", "description": "kubectl logs my-pod -n default --previous", "name": "kubectl_previous_logs", "result": {...}}

event: tool_calling_result
data: {"tool_call_id": "call_1", "role": "tool", "description": "kubectl logs my-pod -n default", "name": "kubectl_logs", "result": {...}}

event: ai_answer_end
data: {"sections": {"Alert Explanation": ...}}
```

---

### `/api/issue_chat` (POST)
**Description:** Conversational interface for discussing a specific issue or incident, with context from a previous investigation.

#### Request Fields

| Field                   | Required | Default | Type      | Description                                      |
|-------------------------|----------|---------|-----------|--------------------------------------------------|
| ask                     | Yes      |         | string    | User's question                                  |
| investigation_result    | Yes      |         | object    | Previous investigation result (see below)        |
| issue_type              | Yes      |         | string    | Type of issue                                    |
| conversation_history    | No       |         | list      | Conversation history (first message must be system)|
| model                   | No       |         | string    | Model name from your `modelList` configuration  |

**investigation_result** object:
- `result` (string, optional): Previous analysis
- `tools` (list, optional): Tools used/results

**Example**
```bash
<!-- test: status=200, has_fields=analysis|conversation_history, id=issue_chat_basic -->
curl -X POST http://<HOLMES-URL>/api/issue_chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "How do I fix this issue?",
    "investigation_result": {
      "result": "Pod crashed due to OOM.",
      "tools": []
    },
    "issue_type": "CrashLoopBackOff",
    "conversation_history": [
      {"role": "system", "content": "You are a helpful assistant."}
    ]
  }'
```

**Example** Response
```json
{
  "analysis": "To fix the CrashLoopBackOff, increase the memory limit for the pod and check for memory leaks in the application.",
  "conversation_history": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "How do I fix this issue?"},
    {"role": "assistant", "content": "To fix the CrashLoopBackOff, increase the memory limit for the pod and check for memory leaks in the application."}
  ],
  "tool_calls": [...],
  "follow_up_actions": [...]
}
```

---

### `/api/model` (GET)
**Description:** Returns a list of available AI models that can be used for investigations and chat.

**Example**
```bash
<!-- test: status=200, has_fields=model_name, id=model_list -->
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

All streaming endpoints (`/api/stream/investigate`, `/api/stream/chat`, `/api/stream/issue_chat`, etc.) emit Server-Sent Events (SSE) to provide real-time updates during the investigation or chat process.

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

Emitted when the investigation or chat is complete. This is the final event in the stream.

**For RCA/Investigation (`/api/stream/investigate`):**
```json
{
  "sections": {
    "Alert Explanation": "...",
    "Key Findings": "...",
    "Conclusions and Possible Root Causes": "...",
    "Next Steps": "...",
    "App or Infra?": "...",
    "External links": "..."
  },
  "analysis": "Full analysis text...",
  "instructions": ["runbook1", "runbook2"],
  "metadata": {...}
}
```

**For Chat (`/api/stream/chat`, `/api/stream/issue_chat`):**
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

**Common Fields:**

- `metadata` (object): See [Metadata Object Reference](#metadata-object-reference) for complete structure including token usage, truncations, and compaction info

**RCA-Specific Fields:**

- `sections` (object): Structured investigation output with predefined sections (customizable via request)
- `analysis` (string): Full analysis text (markdown format)
- `instructions` (array): List of runbooks that were used during investigation

**Chat-Specific Fields:**

- `analysis` (string): The AI's response (markdown format)
- `conversation_history` (array): Complete conversation history including the latest response
- `follow_up_actions` (array|null): Optional follow-up actions the user can take
  - `id` (string): Unique identifier for the action
  - `action_label` (string): Display label for the action
  - `pre_action_notification_text` (string): Text to show before executing the action
  - `prompt` (string): The prompt to send when the action is triggered

---

#### `approval_required`

Emitted when tool execution requires user approval (e.g., potentially destructive operations). The stream pauses until the user provides approval decisions via a subsequent request.

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
  ]
}
```

**Fields:**

- `content` (null): No AI content when approval is required
- `conversation_history` (array): Current conversation state
- `follow_up_actions` (array|null): Optional follow-up actions
- `requires_approval` (boolean): Always true for this event
- `pending_approvals` (array): List of tools awaiting approval
  - `tool_call_id` (string): Unique identifier for the tool call
  - `tool_name` (string): Name of the tool requiring approval
  - `description` (string): Human-readable description
  - `params` (object): Parameters for the tool call

To continue after approval, send a new request with `tool_decisions`:
```json
{
  "conversation_history": [...],
  "tool_decisions": [
    {"tool_call_id": "call_xyz789", "approved": true}
  ]
}
```

---

#### `token_count`

Emitted periodically to provide token usage updates during the investigation. This event is sent after each LLM iteration to help track resource consumption in real-time.

**Payload:**
```json
{
  "metadata": {...}
}
```

**Fields:**

- `metadata` (object): See [Metadata Object Reference](#metadata-object-reference) for complete token usage structure. This event provides the same metadata structure as other events, allowing you to monitor token consumption throughout the investigation

---

#### `conversation_history_compacted`

Emitted when the conversation history has been compacted to fit within the context window. This happens automatically when the conversation grows too large.

**Payload:**
```json
{
  "content": "Conversation history was compacted to fit within context limits.",
  "messages": [...],
  "metadata": {
    "initial_tokens": 150000,
    "compacted_tokens": 80000
  }
}
```

**Fields:**

- `content` (string): Human-readable description of the compaction
- `messages` (array): The compacted conversation history
- `metadata` (object): Token information about the compaction
  - `initial_tokens` (integer): Token count before compaction
  - `compacted_tokens` (integer): Token count after compaction

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

### Typical RCA Investigation Flow

```
1. start_tool_calling (tool 1)
2. start_tool_calling (tool 2)
3. tool_calling_result (tool 1)
4. tool_calling_result (tool 2)
5. token_count
6. start_tool_calling (tool 3)
7. tool_calling_result (tool 3)
8. token_count
9. ai_answer_end
```

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
[investigation resumes]
```

### Chat with History Compaction

```
1. conversation_history_compacted
2. start_tool_calling (tool 1)
3. tool_calling_result (tool 1)
4. token_count
5. ai_answer_end
```
