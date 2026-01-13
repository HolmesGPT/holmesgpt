"""OpenTelemetry instrumentation for HolmesGPT experimental endpoints."""

from experimental.otel.tracing import (
    init_otel_tracer,
    get_tracer,
    shutdown_otel_tracer,
    set_span_error,
)
from experimental.otel.attributes import (
    # Correlation IDs
    REQUEST_ID,
    CONVERSATION_ID,
    # Gen AI Required Attributes
    OPERATION_NAME,
    PROVIDER_NAME,
    # LLM Request/Response
    SYSTEM,
    MODEL,
    RESPONSE_MODEL,
    TEMPERATURE,
    MAX_TOKENS,
    FINISH_REASON,
    # Token Usage
    INPUT_TOKENS,
    OUTPUT_TOKENS,
    TOTAL_TOKENS,
    # Tool/Function Calling
    TOOL_NAME,
    TOOL_CALL_ID,
    TOOL_INPUT,
    TOOL_OUTPUT,
    TOOL_DURATION_MS,
    TOOL_STATUS,
    # Agent-Specific
    AGENT_NAME,
    AGENT_TYPE,
    AGENT_ITERATION,
    # Result Attributes
    RESULT_SUCCESS,
    RESULT_OUTPUT,
    ERROR_MESSAGE,
    ERROR_TYPE,
    # Cost Tracking
    COST_USD,
    # Span Names
    SPAN_AGENT_RUN,
    SPAN_LLM_INFERENCE,
    SPAN_TOOL_EXECUTE,
    SPAN_INVOKE_AGENT,
    SPAN_CHAT,
    SPAN_EXECUTE_TOOL,
    # Metric Names
    METRIC_TOKEN_USAGE,
    METRIC_OPERATION_DURATION,
    METRIC_TOOL_DURATION,
    METRIC_AGENT_ITERATIONS,
    METRIC_AGENT_TOOL_CALLS,
    METRIC_AGENT_ERRORS,
    # Token Type
    TOKEN_TYPE,
    TOKEN_TYPE_INPUT,
    TOKEN_TYPE_OUTPUT,
    # Truncation
    truncate,
    MAX_ATTRIBUTE_SIZE,
)
from experimental.otel.metrics import (
    init_otel_metrics,
    get_meter,
    shutdown_otel_metrics,
    record_token_usage,
    record_operation_duration,
    record_tool_duration,
    increment_iterations,
    increment_tool_calls,
    increment_errors,
)
from experimental.otel.otel_logging import (
    OTELContextFormatter,
    setup_otel_logging,
    get_otel_logger,
    log_llm_call,
    log_tool_execution,
    log_agent_start,
    log_agent_complete,
    log_agent_error,
)

__all__ = [
    # Tracing
    "init_otel_tracer",
    "get_tracer",
    "shutdown_otel_tracer",
    "set_span_error",
    # Metrics
    "init_otel_metrics",
    "get_meter",
    "shutdown_otel_metrics",
    "record_token_usage",
    "record_operation_duration",
    "record_tool_duration",
    "increment_iterations",
    "increment_tool_calls",
    "increment_errors",
    # Logging
    "OTELContextFormatter",
    "setup_otel_logging",
    "get_otel_logger",
    "log_llm_call",
    "log_tool_execution",
    "log_agent_start",
    "log_agent_complete",
    "log_agent_error",
    # Attributes - Correlation IDs
    "REQUEST_ID",
    "CONVERSATION_ID",
    # Attributes - Gen AI Required
    "OPERATION_NAME",
    "PROVIDER_NAME",
    # Attributes - LLM Request/Response
    "SYSTEM",
    "MODEL",
    "RESPONSE_MODEL",
    "TEMPERATURE",
    "MAX_TOKENS",
    "FINISH_REASON",
    # Attributes - Token Usage
    "INPUT_TOKENS",
    "OUTPUT_TOKENS",
    "TOTAL_TOKENS",
    # Attributes - Tool/Function Calling
    "TOOL_NAME",
    "TOOL_CALL_ID",
    "TOOL_INPUT",
    "TOOL_OUTPUT",
    "TOOL_DURATION_MS",
    "TOOL_STATUS",
    # Attributes - Agent-Specific
    "AGENT_NAME",
    "AGENT_TYPE",
    "AGENT_ITERATION",
    # Attributes - Result
    "RESULT_SUCCESS",
    "RESULT_OUTPUT",
    "ERROR_MESSAGE",
    "ERROR_TYPE",
    # Attributes - Cost
    "COST_USD",
    # Span Names
    "SPAN_AGENT_RUN",
    "SPAN_LLM_INFERENCE",
    "SPAN_TOOL_EXECUTE",
    "SPAN_INVOKE_AGENT",
    "SPAN_CHAT",
    "SPAN_EXECUTE_TOOL",
    # Metric Names
    "METRIC_TOKEN_USAGE",
    "METRIC_OPERATION_DURATION",
    "METRIC_TOOL_DURATION",
    "METRIC_AGENT_ITERATIONS",
    "METRIC_AGENT_TOOL_CALLS",
    "METRIC_AGENT_ERRORS",
    # Token Type
    "TOKEN_TYPE",
    "TOKEN_TYPE_INPUT",
    "TOKEN_TYPE_OUTPUT",
    # Utilities
    "truncate",
    "MAX_ATTRIBUTE_SIZE",
]
