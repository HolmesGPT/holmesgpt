"""OpenTelemetry span attribute constants following Gen AI semantic conventions.

Based on OpenTelemetry Gen AI Semantic Conventions:
https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
"""

from typing import Optional

# ============ Gen AI Required Attributes ============
# These are required per OTEL Gen AI semantic conventions

OPERATION_NAME = "gen_ai.operation.name"  # e.g., 'chat', 'invoke_agent', 'execute_tool'
PROVIDER_NAME = "gen_ai.provider.name"  # e.g., 'litellm', 'openai', 'anthropic'

# ============ Correlation IDs ============
# Critical for linking traces across requests

REQUEST_ID = "gen_ai.request.id"  # Maps to AG-UI run_id
CONVERSATION_ID = "gen_ai.conversation.id"  # Maps to AG-UI thread_id

# ============ LLM Request/Response ============

SYSTEM = "gen_ai.system"  # e.g., 'litellm', 'openai', 'anthropic' (legacy, use PROVIDER_NAME)
MODEL = "gen_ai.request.model"  # Model ID being used for request
RESPONSE_MODEL = "gen_ai.response.model"  # Model ID that generated the response
TEMPERATURE = "gen_ai.request.temperature"
MAX_TOKENS = "gen_ai.request.max_tokens"
FINISH_REASON = "gen_ai.response.finish_reasons"

# ============ Token Usage ============

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

AGENT_NAME = "gen_ai.agent.name"  # e.g., 'HolmesGPT'
AGENT_TYPE = "gen_ai.agent.type"  # e.g., 'HolmesGPT' (legacy, use AGENT_NAME)
AGENT_ITERATION = "gen_ai.agent.iteration"  # Current iteration number (1-indexed)

# ============ Result Attributes ============

RESULT_SUCCESS = "result.success"
RESULT_OUTPUT = "result.output"
ERROR_MESSAGE = "error.message"
ERROR_TYPE = "error.type"

# ============ Cost Tracking ============

COST_USD = "gen_ai.usage.cost_usd"  # Cost in USD for this operation

# ============ Span Names ============
# Following Gen AI semantic conventions for span naming

# Legacy span names (for backwards compatibility)
SPAN_AGENT_RUN = "agent.run"
SPAN_LLM_INFERENCE = "llm.inference"
SPAN_TOOL_EXECUTE = "tool.execute"

# Gen AI semantic convention span name prefixes
# Usage: f"{SPAN_INVOKE_AGENT} {agent_name}" -> "invoke_agent HolmesGPT"
SPAN_INVOKE_AGENT = "invoke_agent"  # Root span for agent invocation
SPAN_CHAT = "chat"  # LLM inference/chat completion span
SPAN_EXECUTE_TOOL = "execute_tool"  # Tool execution span

# ============ Metric Names ============
# Following Gen AI semantic conventions for metrics

METRIC_TOKEN_USAGE = "gen_ai.client.token.usage"
METRIC_OPERATION_DURATION = "gen_ai.client.operation.duration"
METRIC_TOOL_DURATION = "gen_ai.tool.duration"
METRIC_AGENT_ITERATIONS = "gen_ai.agent.iterations"
METRIC_AGENT_TOOL_CALLS = "gen_ai.agent.tool_calls"
METRIC_AGENT_ERRORS = "gen_ai.agent.errors"

# ============ Token Type (for metrics) ============

TOKEN_TYPE = "gen_ai.token.type"
TOKEN_TYPE_INPUT = "input"
TOKEN_TYPE_OUTPUT = "output"

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
