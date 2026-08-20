# Context Management

HolmesGPT uses two mechanisms to keep conversations within the LLM's context window.
They run at different points in the pipeline and serve different purposes.

## 1. Single Tool Result Spill-to-Disk

**Function:** `spill_oversized_tool_result()` in `holmes/core/tools_utils/tool_context_window_limiter.py`

**When:** Immediately after each tool call returns, before the result is added to conversation history.

**What it does:**

- Counts the tokens in the single tool result.
- If it exceeds `max_token_count_for_single_tool` (configured via `TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT`):
    - Saves the full text result to a file on disk.
    - If the result contains images, saves them as separate files on disk.
    - Replaces the in-conversation result with a pointer message containing the file path, a preview, and instructions for the LLM to `cat` the file or use `read_image_file` to load images back.
    - If disk storage is unavailable, drops the data entirely and returns an error asking the LLM to narrow its query.

**Called from:** `tool_calling_llm.py` → `_invoke_llm_tool_call()`, after tool execution.

**Scope:** One tool result at a time. Does not look at the overall conversation size.

## 2. Conversation History Compaction

**Function:** `compact_conversation_history()` in `holmes/core/truncation/compaction.py`, orchestrated by `compact_if_necessary()` in `holmes/core/truncation/input_context_window_limiter.py`

**When:** Before each LLM call in the agentic loop, if the total conversation tokens exceed a compaction threshold.

**What it does:**

- Checks if `(total_tokens + max_output_tokens) > (context_window_size * threshold_pct / 100)`.
- If so, sends the conversation history to the LLM with a compaction prompt, asking it to produce a concise summary.
- Replaces the old messages with: system prompt + compacted summary + last user message.
- Tracks compaction cost in `RequestStats`.

**Guard:** Controlled by `ENABLE_CONVERSATION_HISTORY_COMPACTION` env var (defaults to true).

**Called from:** `tool_calling_llm.py` → `call_stream()`, at the top of each agentic loop iteration.

**Scope:** The entire conversation history. Uses an LLM call (costs tokens/money).

## How They Interact

```
Tool executes
    │
    ▼
┌─────────────────────────────┐
│ 1. spill_oversized_tool_result │  ← caps single tool result
└─────────────────────────────┘
    │
    ▼
Tool result added to conversation
    │
    ▼
┌─────────────────────────────┐
│ 2. compaction (if needed)   │  ← summarizes full conversation via LLM
└─────────────────────────────┘
    │
    ▼
LLM called with messages
```

In practice:

- Mechanism 1 prevents any single tool from blowing up the context.
- Mechanism 2 prevents the cumulative conversation from growing unbounded.

## Output Token Limit

**Function:** `get_maximum_output_token()` in `holmes/core/llm.py`, enforced by `DefaultLLM.completion()` as `max_tokens` (or forwarded as `max_completion_tokens` when the model args already provide it)

Every LLM request includes an explicit output-token limit. It is the same value mechanism 2 reserves when budgeting input space (`max_output_tokens` in the threshold check above), so the enforced cap matches the reserved budget. Without an explicit limit, some providers fall back to small defaults (for example, litellm defaults Anthropic-family models that are missing from its cost map — such as proxy aliases — to 4096), which truncates long answers mid-response with `finish_reason: "length"`.

The value is resolved in this order:

1. A non-null `max_tokens` or `max_completion_tokens` in the model's args (model list / `custom_args`) takes precedence (a `null` value is stripped, so it does not block the computed default below).
2. `OVERRIDE_MAX_OUTPUT_TOKEN` environment variable.
3. Computed: `max(64000, 12% of the context window)` — so a 200k-context (or unknown) model reserves 64k and a 1M-context model reserves 120k — further capped by the model's `max_output_tokens` from litellm's cost map when the model is known.
