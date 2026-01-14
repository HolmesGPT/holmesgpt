"""OpenTelemetry span attribute constants following Gen AI semantic conventions.

Uses opentelemetry-semantic-conventions library for standard attributes.
Custom HolmesGPT-specific attributes defined below.

References:
- https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
"""

from typing import Optional

# Import standard Gen AI attributes from OTEL semantic conventions library
from opentelemetry.semconv._incubating.attributes.gen_ai_attributes import (
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_TOKEN_TYPE,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GenAiOperationNameValues,
)

# ============ Re-export library constants with short names for convenience ============
# These provide backward compatibility and shorter import paths

# Gen AI Required Attributes
OPERATION_NAME = GEN_AI_OPERATION_NAME
PROVIDER_NAME = GEN_AI_PROVIDER_NAME

# Correlation IDs
CONVERSATION_ID = GEN_AI_CONVERSATION_ID

# LLM Request/Response
SYSTEM = GEN_AI_SYSTEM
MODEL = GEN_AI_REQUEST_MODEL
RESPONSE_MODEL = GEN_AI_RESPONSE_MODEL
TEMPERATURE = GEN_AI_REQUEST_TEMPERATURE
MAX_TOKENS = GEN_AI_REQUEST_MAX_TOKENS
FINISH_REASON = GEN_AI_RESPONSE_FINISH_REASONS

# Token Usage
INPUT_TOKENS = GEN_AI_USAGE_INPUT_TOKENS
OUTPUT_TOKENS = GEN_AI_USAGE_OUTPUT_TOKENS

# Tool/Function Calling
TOOL_NAME = GEN_AI_TOOL_NAME
TOOL_CALL_ID = GEN_AI_TOOL_CALL_ID

# Agent-Specific
AGENT_NAME = GEN_AI_AGENT_NAME

# Token Type
TOKEN_TYPE = GEN_AI_TOKEN_TYPE

# ============ HolmesGPT Custom Attributes ============
# Not in OTEL spec - specific to HolmesGPT

REQUEST_ID = "gen_ai.request.id"  # Maps to AG-UI run_id
AGENT_TYPE = "gen_ai.agent.type"  # Legacy, use AGENT_NAME
AGENT_ITERATION = "gen_ai.agent.iteration"  # Current iteration number (1-indexed)

TOOL_INPUT = "gen_ai.tool.input"
TOOL_OUTPUT = "gen_ai.tool.output"
TOOL_DURATION_MS = "gen_ai.tool.duration_ms"
TOOL_STATUS = "gen_ai.tool.status"  # SUCCESS, FAILURE

TOTAL_TOKENS = "gen_ai.usage.total_tokens"
COST_USD = "gen_ai.usage.cost_usd"  # Cost in USD for this operation

# Context Attributes
MESSAGES_IN_CONTEXT = "gen_ai.context.message_count"
TOOLS_AVAILABLE = "gen_ai.context.tools_available"
CONTEXT_TOKENS_USED = "gen_ai.context.tokens_used"
CONTEXT_TOKENS_LIMIT = "gen_ai.context.tokens_limit"
USER_QUERY_LENGTH = "gen_ai.request.query_length"
ANSWER_LENGTH = "gen_ai.response.answer_length"
TOOL_CALL_COUNT = "gen_ai.tool.call_count"

# Result Attributes
RESULT_SUCCESS = "result.success"
RESULT_OUTPUT = "result.output"
ERROR_MESSAGE = "error.message"
ERROR_TYPE = "error.type"

# ============ Span Names ============
# Use library enum values for Gen AI semantic convention span names

SPAN_INVOKE_AGENT = GenAiOperationNameValues.INVOKE_AGENT.value
SPAN_CHAT = GenAiOperationNameValues.CHAT.value
SPAN_EXECUTE_TOOL = GenAiOperationNameValues.EXECUTE_TOOL.value

# Legacy span names (for backwards compatibility)
SPAN_AGENT_RUN = "agent.run"
SPAN_LLM_INFERENCE = "llm.inference"
SPAN_TOOL_EXECUTE = "tool.execute"

# ============ Granular Span Names ============
# More detailed spans for better observability

# Message preparation spans
SPAN_PREPARE_MESSAGES = "prepare_messages"
SPAN_BUILD_SYSTEM_PROMPT = "build_system_prompt"
SPAN_BUILD_CONVERSATION_HISTORY = "build_conversation_history"

# LLM call spans
SPAN_LLM_COMPLETION = "llm_completion"
SPAN_PARSE_RESPONSE = "parse_response"

# Tool execution sub-spans
SPAN_PARSE_ARGUMENTS = "parse_arguments"
SPAN_INVOKE_TOOL = "invoke_tool"
SPAN_PROCESS_RESULT = "process_result"

# Context management spans
SPAN_CHECK_CONTEXT_LIMITS = "check_context_limits"
SPAN_COMPACT_HISTORY = "compact_history"

# Final answer span
SPAN_GENERATE_ANSWER = "generate_final_answer"

# Error handling spans
SPAN_HANDLE_LLM_ERROR = "handle_llm_error"
SPAN_HANDLE_TOOL_ERROR = "handle_tool_error"
SPAN_RETRY_ATTEMPT = "retry_attempt"
SPAN_CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"
SPAN_FALLBACK_STRUCTURED_OUTPUT = "fallback_structured_output"

# ============ Metric Names ============
# Following Gen AI semantic conventions for metrics

METRIC_TOKEN_USAGE = "gen_ai.client.token.usage"
METRIC_OPERATION_DURATION = "gen_ai.client.operation.duration"
METRIC_TOOL_DURATION = "gen_ai.tool.duration"
METRIC_AGENT_ITERATIONS = "gen_ai.agent.iterations"
METRIC_AGENT_TOOL_CALLS = "gen_ai.agent.tool_calls"
METRIC_AGENT_ERRORS = "gen_ai.agent.errors"

# Token type values (for metrics)
TOKEN_TYPE_INPUT = "input"
TOKEN_TYPE_OUTPUT = "output"

# ============ Truncation ============
# Prevent "payload too large" errors when exporting to OSIS
MAX_ATTRIBUTE_SIZE = 8192
TRUNCATION_MARKER = "...[TRUNCATED]"


def truncate(value: Optional[str], max_size: int = MAX_ATTRIBUTE_SIZE) -> str:
    """Truncate a string value to prevent OTEL payload size errors.

    Based on OTEL best practices for attribute size limits.
    See: https://opentelemetry.io/docs/specs/otel/common/#attribute-limits

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
    # Account for marker length to stay within max_size
    return value[: max_size - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
