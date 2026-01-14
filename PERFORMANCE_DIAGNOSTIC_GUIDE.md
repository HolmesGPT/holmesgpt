# Performance Diagnostic Guide: Chat API vs Direct Eval

## Problem Statement
The `/api/chat` endpoint with relay (Holmes AI) is significantly slower than direct eval tests with AWS Bedrock Claude, despite frontend timing showing minimal client-side delays.

## Instrumented Measurements

The code now tracks these timing events in the streaming response:

### 1. Configuration & Initialization
- `config_load_start` → `config_load_end`
- Metadata: `num_toolsets`

### 2. Database Operations
- `dal_operation` events
- Metadata: `operation` (e.g., "get_global_instructions"), `duration_ms`

### 3. Message Building & Prompt Construction
- `message_build_start` → `message_build_end`
- `prompt_construction_start` → `prompt_construction_end`
- Metadata: `num_messages`, `num_runbooks`

### 4. Context Window Management
- `context_limiting_start` → `context_limiting_end`
- Metadata: `tokens_before`, `tokens_after`

### 5. LLM Calls
- `llm_call_start` → `llm_call_end`
- Metadata: `model`, `iteration`

### 6. Tool Execution
- `tool_call_start` → `tool_call_end`
- Metadata: `tool_name`, `tool_id`

### 7. History Management
- `history_compacted`

---

## Theories & Diagnostic Approach

### Theory 1: Network Latency to Bedrock
**Hypothesis**: The server's network path to AWS Bedrock is slower than the eval environment's path.

**Measurements to Check**:
```
Look for: llm_call_start → llm_call_end duration
Compare: Chat API vs Eval for the SAME model call
```

**Prediction**:
- If this is the issue: Duration will be consistently 200-500ms+ longer in chat API
- Expected pattern: ALL LLM calls are proportionally slower
- What you'll see: `llm_call_end.timestamp - llm_call_start.timestamp` is 2-3x larger

**How to test**:
```bash
# In eval environment
time curl https://bedrock.amazonaws.com/...

# From server environment
time curl https://bedrock.amazonaws.com/...
```

**Fix if confirmed**:
- Deploy server closer to Bedrock region
- Use VPC endpoints if available
- Consider regional Bedrock endpoints

---

### Theory 2: Tool Initialization Overhead
**Hypothesis**: Chat API loads more toolsets or takes longer to initialize tools than eval.

**Measurements to Check**:
```
Look for: config_load_start → config_load_end duration
Check metadata: num_toolsets in chat vs eval
```

**Prediction**:
- If this is the issue: 50-200ms delay before first LLM call
- Expected pattern: Delay happens ONCE at start, not per-iteration
- What you'll see: `config_load_end.timestamp` is significantly > 0.1s

**Typical values**:
- Fast: < 50ms (5-10 toolsets)
- Moderate: 50-150ms (15-25 toolsets)
- Slow: > 150ms (30+ toolsets or complex initialization)

**Fix if confirmed**:
- Lazy-load toolsets
- Cache toolset initialization
- Remove unused toolsets from chat config

---

### Theory 3: Database/DAL Overhead
**Hypothesis**: Chat API makes database calls that eval doesn't (global instructions, user settings, etc.)

**Measurements to Check**:
```
Look for: dal_operation events
Sum up all: duration_ms in metadata
Compare: Number of DAL operations in chat vs eval
```

**Prediction**:
- If this is the issue: 10-100ms per database call
- Expected pattern: Multiple `dal_operation` events before first LLM call
- What you'll see: Several events with `duration_ms` > 10ms

**Common culprits**:
- `get_global_instructions`: Should be < 20ms (if slow: database connection issue)
- `get_workload_issues`: Should be < 50ms (if slow: unindexed query)
- `get_resource_instructions`: Should be < 30ms

**Fix if confirmed**:
- Add database indexes
- Cache global instructions (they rarely change)
- Use connection pooling
- Consider Redis cache layer

---

### Theory 4: Message Building Complexity
**Hypothesis**: Chat API builds more complex messages with conversation history, runbooks, etc.

**Measurements to Check**:
```
Look for: message_build_start → message_build_end duration
Check metadata: num_messages, num_runbooks
```

**Prediction**:
- If this is the issue: 20-100ms for complex prompts
- Expected pattern: Scales with num_messages and num_runbooks
- What you'll see: `message_build_end.timestamp - message_build_start.timestamp` > 0.05s

**Benchmarks**:
- Simple (no history): < 10ms
- Moderate (3-5 messages, 2-3 runbooks): 20-50ms
- Complex (10+ messages, 10+ runbooks): 50-150ms

**Fix if confirmed**:
- Pre-compile Jinja2 templates
- Cache rendered runbooks
- Simplify system prompts
- Remove unnecessary formatting

---

### Theory 5: Context Window Limiting Overhead
**Hypothesis**: Chat API spends time truncating messages/tools to fit context window.

**Measurements to Check**:
```
Look for: context_limiting_start → context_limiting_end duration
Check metadata: tokens_before vs tokens_after
```

**Prediction**:
- If this is the issue: 50-300ms per iteration when compaction happens
- Expected pattern: Duration correlates with (tokens_before - tokens_after)
- What you'll see: Large gap when `tokens_before` >> `tokens_after`

**When it's expensive**:
- No compaction needed: < 20ms
- Light compaction (10% reduction): 30-80ms
- Heavy compaction (50%+ reduction): 100-300ms
- LLM-based summarization: 500-2000ms (if enabled)

**Fix if confirmed**:
- Increase context window limit
- Improve compaction algorithm efficiency
- Cache compacted messages
- Use faster summarization method

---

### Theory 6: Tool Response Processing
**Hypothesis**: Chat API has larger tool responses that need processing/truncation.

**Measurements to Check**:
```
Look for: tool_call_start → tool_call_end duration
Compare: Duration for SAME tool in chat vs eval
```

**Prediction**:
- If this is the issue: 20-100ms extra per tool call
- Expected pattern: Some tools consistently slower
- What you'll see: `tool_call_end.timestamp - tool_call_start.timestamp` varies widely

**Typical tool durations**:
- Fast tools (kubectl get pod): 50-200ms
- Medium tools (prometheus query): 200-800ms
- Slow tools (kubectl logs): 500-2000ms

**Processing overhead should be**:
- < 10ms for small responses (< 10KB)
- 10-50ms for medium responses (10-100KB)
- 50-200ms for large responses (> 100KB)

**Fix if confirmed**:
- Implement streaming tool responses
- Truncate tool output earlier in pipeline
- Cache frequently-used tool results
- Parallel tool execution (already implemented)

---

### Theory 7: Multiple LLM Iterations
**Hypothesis**: Chat API requires more iterations (tool calls) than eval for the same task.

**Measurements to Check**:
```
Count: Total llm_call_start events
Compare: Number of iterations in chat vs eval
```

**Prediction**:
- If this is the issue: Chat has 2-3x more iterations
- Expected pattern: Total time = iterations × per-iteration-time
- What you'll see: Many `iteration` numbers in metadata

**Why this happens**:
- More tools available → LLM tries more things
- Different system prompt → different behavior
- Conversation history → context affects decisions

**Fix if confirmed**:
- Reduce max_steps if appropriate
- Improve system prompt to be more direct
- Filter available tools to only relevant ones
- Add tool call deduplication

---

### Theory 8: Prompt Size & Token Counting
**Hypothesis**: Chat API has larger prompts that take longer to process/count tokens.

**Measurements to Check**:
```
Look for: First llm_call_start timestamp
Check: How long after request_start
Compare: Token counts in metadata
```

**Prediction**:
- If this is the issue: 50-200ms before first LLM call
- Expected pattern: Scales with prompt size
- What you'll see: Long delay between `request_start` and first `llm_call_start`

**Token counting overhead**:
- < 10K tokens: < 20ms
- 10-50K tokens: 20-100ms
- > 50K tokens: 100-300ms

**Fix if confirmed**:
- Cache token counts for static portions
- Use approximate token counting
- Simplify prompts
- Remove redundant instructions

---

### Theory 9: Streaming Response Overhead
**Hypothesis**: SSE (Server-Sent Events) overhead and JSON serialization slow things down.

**Measurements to Check**:
```
Compare: Total server time vs time to first byte (frontend measurement)
Look for: Gaps between server events and client receipt
```

**Prediction**:
- If this is the issue: Consistent 10-50ms delay per message
- Expected pattern: Small overhead on each SSE message
- What you'll see: Client timestamps lag server by constant amount

**Typical overhead**:
- SSE framing: < 5ms per message
- JSON serialization: < 10ms per message
- Network buffering: 5-20ms

**Fix if confirmed**:
- Reduce SSE message frequency
- Batch updates where possible
- Optimize JSON serialization
- Use MessagePack instead of JSON

---

### Theory 10: LiteLLM Proxy Overhead
**Hypothesis**: LiteLLM adds overhead in request/response processing.

**Measurements to Check**:
```
Look for: llm_call duration
Compare: Direct Bedrock call vs through LiteLLM
Check logs: Any LiteLLM warnings/retries
```

**Prediction**:
- If this is the issue: 20-100ms per LLM call
- Expected pattern: Consistent overhead on every call
- What you'll see: All `llm_call` durations are 50-150ms longer

**LiteLLM overhead sources**:
- Request transformation: 10-30ms
- Response parsing: 10-30ms
- Token counting: 10-50ms
- Error handling/retries: Variable

**Fix if confirmed**:
- Use direct Bedrock client for production
- Disable unnecessary LiteLLM features
- Cache LiteLLM transformations
- Update to latest LiteLLM version

---

## How to Use This Guide

### Step 1: Collect Timing Data
Make identical requests to both:
1. `/api/chat` with streaming
2. Direct eval test

Save the `timing_events` arrays from both.

### Step 2: Analyze Patterns
For each theory above:
1. Extract the relevant timing events
2. Calculate durations
3. Compare chat vs eval
4. Look for the predicted pattern

### Step 3: Prioritize Fixes
Calculate impact:
```
Impact Score = (Average Overhead) × (Frequency per Request)
```

Fix high-impact issues first.

### Step 4: Validate Fixes
After each fix:
1. Re-run both tests
2. Compare new timing data
3. Verify improvement
4. Document the change

---

## Example Analysis Script

```python
import json

def analyze_timing(timing_events):
    """Analyze timing events to identify bottlenecks."""

    # Theory 1: Network latency
    llm_calls = [e for e in timing_events if e['event_type'] == 'llm_call_start']
    llm_ends = [e for e in timing_events if e['event_type'] == 'llm_call_end']
    for start, end in zip(llm_calls, llm_ends):
        duration = (end['timestamp'] - start['timestamp']) * 1000
        print(f"LLM call iteration {start['metadata']['iteration']}: {duration:.0f}ms")

    # Theory 2: Tool initialization
    config_start = next((e for e in timing_events if e['event_type'] == 'config_load_start'), None)
    config_end = next((e for e in timing_events if e['event_type'] == 'config_load_end'), None)
    if config_start and config_end:
        duration = (config_end['timestamp'] - config_start['timestamp']) * 1000
        num_toolsets = config_end['metadata']['num_toolsets']
        print(f"Config load: {duration:.0f}ms ({num_toolsets} toolsets)")

    # Theory 3: Database operations
    dal_ops = [e for e in timing_events if e['event_type'] == 'dal_operation']
    total_dal_time = sum(float(e['metadata']['duration_ms']) for e in dal_ops if e['metadata'].get('duration_ms'))
    print(f"Total DAL time: {total_dal_time:.0f}ms across {len(dal_ops)} operations")

    # Theory 4: Message building
    msg_start = next((e for e in timing_events if e['event_type'] == 'message_build_start'), None)
    msg_end = next((e for e in timing_events if e['event_type'] == 'message_build_end'), None)
    if msg_start and msg_end:
        duration = (msg_end['timestamp'] - msg_start['timestamp']) * 1000
        num_messages = msg_end['metadata']['num_messages']
        print(f"Message build: {duration:.0f}ms ({num_messages} messages)")

    # Theory 5: Context limiting
    ctx_starts = [e for e in timing_events if e['event_type'] == 'context_limiting_start']
    ctx_ends = [e for e in timing_events if e['event_type'] == 'context_limiting_end']
    for start, end in zip(ctx_starts, ctx_ends):
        duration = (end['timestamp'] - start['timestamp']) * 1000
        print(f"Context limiting: {duration:.0f}ms")

    # Theory 6: Tool execution
    tool_starts = [e for e in timing_events if e['event_type'] == 'tool_call_start']
    tool_ends = [e for e in timing_events if e['event_type'] == 'tool_call_end']
    for start, end in zip(tool_starts, tool_ends):
        duration = (end['timestamp'] - start['timestamp']) * 1000
        tool_name = start['metadata']['tool_name']
        print(f"Tool {tool_name}: {duration:.0f}ms")

    # Theory 7: Total iterations
    num_iterations = len(llm_calls)
    print(f"\nTotal LLM iterations: {num_iterations}")

    # Overall timing
    request_start = next(e for e in timing_events if e['event_type'] == 'request_start')
    last_event = timing_events[-1]
    total_time = (last_event['timestamp'] - request_start['timestamp']) * 1000
    print(f"Total request time: {total_time:.0f}ms")

    # Time to first LLM call
    if llm_calls:
        first_llm = llm_calls[0]
        time_to_first_llm = (first_llm['timestamp'] - request_start['timestamp']) * 1000
        print(f"Time to first LLM call: {time_to_first_llm:.0f}ms")

# Usage:
# chat_data = json.loads(chat_response_sse['data']['timing'])
# analyze_timing(chat_data['timing_events'])
```

---

## Quick Diagnostic Checklist

Run through this checklist to quickly identify the most likely culprit:

- [ ] **Is config load time > 100ms?** → Theory 2 (Tool initialization)
- [ ] **Are there many dal_operation events?** → Theory 3 (Database overhead)
- [ ] **Is message_build time > 50ms?** → Theory 4 (Message complexity)
- [ ] **Do you see context_limiting events > 100ms?** → Theory 5 (Context limiting)
- [ ] **Are LLM call durations > 2 seconds each?** → Theory 1 (Network latency)
- [ ] **Are there 5+ LLM iterations?** → Theory 7 (Too many iterations)
- [ ] **Is time_to_first_llm > 200ms?** → Theory 8 (Prompt processing)
- [ ] **Do individual tools take > 1 second?** → Theory 6 (Tool overhead)

The most likely culprits are usually **Theories 1, 3, and 5** for production systems.
