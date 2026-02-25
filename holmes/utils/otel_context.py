"""
OpenTelemetry context propagation for thread-pool and async boundaries.

When tool calls (including MCP) run in ThreadPoolExecutor workers, the trace
context from the request thread is not automatically available. This module
provides helpers to capture the current OTEL context and attach it when
running work in another thread so that LiteLLM spans, MCP spans, and tool
execution stay under the same trace as the HTTP request.
"""
from typing import Callable, TypeVar

try:
    from opentelemetry import context
except ImportError:
    context = None  # type: ignore[assignment]

T = TypeVar("T")


def run_with_current_otel_context(fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    """
    Run fn(*args, **kwargs) with the current OpenTelemetry context attached.

    Use this when invoking the callable in a different thread (e.g. executor)
    so that spans created inside fn (e.g. by LiteLLM otel callback or
    MCP instrumentation) are attached to the same trace as the caller.

    If opentelemetry is not installed or context is empty, fn is run as-is.
    """
    if context is None:
        return fn(*args, **kwargs)
    current = context.get_current()
    token = context.attach(current)
    try:
        return fn(*args, **kwargs)
    finally:
        context.detach(token)


def bind_with_current_otel_context(
    fn: Callable[..., T], *args: object, **kwargs: object
) -> Callable[[], T]:
    """
    Return a zero-argument callable that runs fn(*args, **kwargs) with the
    current OpenTelemetry context attached.

    Use when submitting to ThreadPoolExecutor: capture the context in the
    request thread, then pass the returned callable to executor.submit().
    """
    if context is None:
        return lambda: fn(*args, **kwargs)
    current = context.get_current()

    def task() -> T:
        token = context.attach(current)
        try:
            return fn(*args, **kwargs)
        finally:
            context.detach(token)

    return task
