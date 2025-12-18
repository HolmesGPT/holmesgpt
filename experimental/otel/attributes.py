"""OpenTelemetry span attribute constants following Gen AI semantic conventions.

Based on patterns from ml-commons AgentSemanticAttributes.java
"""

from typing import Optional

# ============ Gen AI Standard Attributes ============
# These follow the Gen AI semantic conventions for observability

# Correlation IDs - Critical for linking traces
REQUEST_ID = "gen_ai.request.id"  # Maps to AG-UI run_id
CONVERSATION_ID = "gen_ai.conversation.id"  # Maps to AG-UI thread_id

# LLM Request/Response
SYSTEM = "gen_ai.system"  # e.g., 'litellm', 'openai', 'anthropic'
MODEL = "gen_ai.request.model"  # Model ID being used
TEMPERATURE = "gen_ai.request.temperature"
MAX_TOKENS = "gen_ai.request.max_tokens"
FINISH_REASON = "gen_ai.response.finish_reasons"

# Token Usage
INPUT_TOKENS = "gen_ai.usage.input_tokens"  # Prompt tokens
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"  # Completion tokens
TOTAL_TOKENS = "gen_ai.usage.total_tokens"  # Total tokens

# ============ Tool/Function Calling ============
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call_id"
TOOL_INPUT = "gen_ai.tool.input"
TOOL_OUTPUT = "gen_ai.tool.output"
TOOL_DURATION_MS = "gen_ai.tool.duration_ms"
TOOL_STATUS = "gen_ai.tool.status"  # SUCCESS, FAILURE

# ============ Agent-Specific ============
AGENT_TYPE = "gen_ai.agent.type"  # e.g., 'HolmesGPT'
AGENT_ITERATION = "gen_ai.agent.iteration"

# ============ Result Attributes ============
RESULT_SUCCESS = "result.success"
RESULT_OUTPUT = "result.output"
ERROR_MESSAGE = "error.message"
ERROR_TYPE = "error.type"

# ============ Span Names ============
SPAN_AGENT_RUN = "agent.run"
SPAN_LLM_INFERENCE = "llm.inference"
SPAN_TOOL_EXECUTE = "tool.execute"

# ============ Truncation ============
# Prevent "payload too large" errors when exporting to OSIS
MAX_ATTRIBUTE_SIZE = 8192


def truncate(value: Optional[str], max_size: int = MAX_ATTRIBUTE_SIZE) -> str:
    """Truncate a string value to prevent OTEL payload size errors.

    Based on ml-commons AgentTracer.truncate() pattern.

    Args:
        value: The string to truncate (can be None)
        max_size: Maximum allowed size (default 8KB)

    Returns:
        Original string if within limits, otherwise truncated with marker.
        Returns empty string if value is None.
    """
    if value is None:
        return ""
    if len(value) <= max_size:
        return value
    return value[:max_size] + "...[TRUNCATED]"
