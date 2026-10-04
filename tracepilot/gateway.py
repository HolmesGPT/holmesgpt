"""Small protocol boundary independent of the optional integration stack."""

from typing import Protocol

from tracepilot.errors import classify_tool_error
from tracepilot.models import Candidate, ToolOutput, ToolSpec


class ToolGateway(Protocol):
    def catalog(self, refresh: bool = True) -> list[ToolSpec]: ...

    def execute(self, candidate: Candidate) -> ToolOutput: ...


def tool_error_output(exc: Exception) -> ToolOutput:
    details = classify_tool_error(exc)
    return ToolOutput(status="error", error=f"{type(exc).__name__}: {exc}",
                      error_kind=details.kind, retry_after_seconds=details.retry_after_seconds)
