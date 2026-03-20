"""Frontend tool implementations for client-side tool execution.

FrontendPauseTool: When the LLM calls this tool, it returns a FRONTEND_PAUSE
status. The call_stream loop handles this status by pausing the stream and
emitting an approval_required event with pending_frontend_tool_calls. The
client executes the tool and resumes by sending frontend_tool_results.

This keeps frontend tool awareness OUT of call_stream's separation logic —
the tool itself declares its behavior via its return status, and call_stream
handles it generically like it handles APPROVAL_REQUIRED.
"""

from typing import Any, Dict, Optional

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)


class FrontendPauseTool(Tool):
    """A tool that pauses the stream so the client can execute it.

    When invoked, returns FRONTEND_PAUSE status with the call arguments
    in params. call_stream handles this by emitting an approval_required
    event with pending_frontend_tool_calls, identical to the current
    wire protocol.
    """

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.FRONTEND_PAUSE,
            data=None,
            params=params,
        )

    def invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Skip parent's approval/coercion/transformer logic — just return FRONTEND_PAUSE."""
        return self._invoke(params, context)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{self.name}({params})"

    def _get_approval_requirement(self, params: Dict, context: Any) -> None:
        return None

    def _is_restricted(self) -> bool:
        return False


def build_frontend_pause_tool(
    name: str,
    description: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> FrontendPauseTool:
    """Create a FrontendPauseTool from a frontend tool definition.

    Args:
        name: Tool name as declared by the client.
        description: Tool description for the LLM.
        parameters: JSON Schema dict for the tool's parameters (OpenAI format).
    """
    tool_params: Dict[str, ToolParameter] = {}
    if parameters and "properties" in parameters:
        for param_name, param_schema in parameters["properties"].items():
            tool_params[param_name] = ToolParameter(
                type=param_schema.get("type", "string"),
                description=param_schema.get("description", ""),
            )

    return FrontendPauseTool(
        name=name,
        description=description,
        parameters=tool_params,
    )
