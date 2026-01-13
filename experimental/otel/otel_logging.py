"""OpenTelemetry correlated logging for HolmesGPT.

Provides logging utilities that automatically include trace context (trace_id, span_id)
for correlation between logs and traces in observability backends.
"""

import logging
import os
from typing import Optional

from opentelemetry import trace


class OTELContextFormatter(logging.Formatter):
    """Formatter that adds trace_id and span_id to log records.

    This allows logs to be correlated with traces in observability backends
    like OpenSearch, Datadog, or other OTEL-compatible systems.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with trace context."""
        span = trace.get_current_span()
        ctx = span.get_span_context()

        if ctx.is_valid:
            # Format as 32-char hex trace_id and 16-char hex span_id
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
            record.trace_sampled = ctx.trace_flags.sampled
        else:
            record.trace_id = "0" * 32
            record.span_id = "0" * 16
            record.trace_sampled = False

        return super().format(record)


def _get_otel_enabled() -> bool:
    """Check if OTEL is enabled via environment variable."""
    return os.environ.get("OTEL_ENABLED", "false").lower() == "true"


def _get_logs_enabled() -> bool:
    """Check if OTEL log export is enabled."""
    return os.environ.get("OTEL_LOGS_ENABLED", "true").lower() == "true"


def _get_log_content_enabled() -> bool:
    """Check if logging prompt/response content is enabled (privacy-sensitive)."""
    return os.environ.get("OTEL_LOG_CONTENT", "false").lower() == "true"


def setup_otel_logging(
    logger_name: Optional[str] = None,
    log_level: int = logging.INFO,
    include_trace_context: bool = True,
) -> logging.Logger:
    """Set up a logger with OTEL trace context formatting.

    Args:
        logger_name: Name of the logger (None for root logger)
        log_level: Logging level (default: INFO)
        include_trace_context: Whether to include trace_id/span_id in format

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)

    # Only add handler if logger doesn't have one
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(log_level)

        if include_trace_context and _get_otel_enabled():
            # Format with trace context
            format_str = (
                "%(asctime)s [%(trace_id)s/%(span_id)s] "
                "%(levelname)-8s %(name)s - %(message)s"
            )
            formatter = OTELContextFormatter(format_str)
        else:
            # Standard format without trace context
            format_str = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
            formatter = logging.Formatter(format_str)

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def get_otel_logger(name: str = "holmesgpt.agui") -> logging.Logger:
    """Get a logger configured for OTEL trace context.

    Args:
        name: Logger name

    Returns:
        Logger with OTEL context formatting
    """
    return setup_otel_logging(logger_name=name)


# Structured logging helpers for AI-specific events


def log_llm_call(
    logger: logging.Logger,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    finish_reason: Optional[str] = None,
    cost_usd: Optional[float] = None,
    iteration: Optional[int] = None,
) -> None:
    """Log an LLM call completion with structured data.

    Args:
        logger: Logger instance
        model: Model name/ID
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        finish_reason: Why the LLM stopped
        cost_usd: Cost in USD
        iteration: Iteration number
    """
    msg_parts = [
        f"LLM call completed: model={model}",
        f"tokens={prompt_tokens}+{completion_tokens}",
    ]

    if iteration is not None:
        msg_parts.insert(0, f"[iteration {iteration}]")

    if finish_reason:
        msg_parts.append(f"finish={finish_reason}")

    if cost_usd:
        msg_parts.append(f"cost=${cost_usd:.6f}")

    logger.info(" ".join(msg_parts))


def log_tool_execution(
    logger: logging.Logger,
    tool_name: str,
    duration_ms: int,
    success: bool = True,
    tool_call_id: Optional[str] = None,
) -> None:
    """Log a tool execution with structured data.

    Args:
        logger: Logger instance
        tool_name: Name of the tool
        duration_ms: Execution duration in milliseconds
        success: Whether execution succeeded
        tool_call_id: Tool call identifier
    """
    status = "completed" if success else "failed"
    msg_parts = [f"Tool {status}: {tool_name}", f"duration={duration_ms}ms"]

    if tool_call_id:
        msg_parts.append(f"id={tool_call_id}")

    log_level = logging.INFO if success else logging.WARNING
    logger.log(log_level, " ".join(msg_parts))


def log_agent_start(
    logger: logging.Logger,
    agent_name: str,
    model: str,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """Log agent run start with structured data.

    Args:
        logger: Logger instance
        agent_name: Name of the agent
        model: Model being used
        run_id: Run identifier
        thread_id: Conversation thread identifier
    """
    msg_parts = [f"Agent run started: {agent_name}", f"model={model}"]

    if run_id:
        msg_parts.append(f"run_id={run_id}")
    if thread_id:
        msg_parts.append(f"thread_id={thread_id}")

    logger.info(" ".join(msg_parts))


def log_agent_complete(
    logger: logging.Logger,
    agent_name: str,
    total_tokens: int,
    tool_count: int,
    duration_seconds: float,
    iterations: int,
    success: bool = True,
) -> None:
    """Log agent run completion with structured data.

    Args:
        logger: Logger instance
        agent_name: Name of the agent
        total_tokens: Total tokens used
        tool_count: Number of tool calls
        duration_seconds: Total duration in seconds
        iterations: Number of LLM iterations
        success: Whether the run succeeded
    """
    status = "completed" if success else "failed"
    msg_parts = [
        f"Agent run {status}: {agent_name}",
        f"tokens={total_tokens}",
        f"tools={tool_count}",
        f"iterations={iterations}",
        f"duration={duration_seconds:.2f}s",
    ]

    log_level = logging.INFO if success else logging.ERROR
    logger.log(log_level, " ".join(msg_parts))


def log_agent_error(
    logger: logging.Logger,
    agent_name: str,
    error: Exception,
    error_type: Optional[str] = None,
) -> None:
    """Log agent error with structured data.

    Args:
        logger: Logger instance
        agent_name: Name of the agent
        error: The exception that occurred
        error_type: Type/category of error
    """
    err_type = error_type or type(error).__name__
    logger.error(
        f"Agent error: {agent_name} error_type={err_type} message={str(error)}",
        exc_info=True,
    )
