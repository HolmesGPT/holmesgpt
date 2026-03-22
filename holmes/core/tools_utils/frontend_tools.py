"""Frontend tool implementations for client-side tool execution.

FrontendPauseTool: When the LLM calls this tool, it returns a FRONTEND_PAUSE
status. The call_stream loop handles this status by pausing the stream and
emitting an approval_required event with pending_frontend_tool_calls. The
client executes the tool and resumes by sending frontend_tool_results.

FrontendNoopTool: When the LLM calls this tool, it returns SUCCESS with a
canned response immediately. The LLM continues without pausing. The client
sees the tool call in SSE events (start_tool_calling + tool_calling_result)
and can execute it as a fire-and-forget side effect.

This keeps frontend tool awareness OUT of call_stream's separation logic —
the tool itself declares its behavior via its return status, and call_stream
handles it generically like it handles APPROVAL_REQUIRED.
"""

from typing import Any, Dict, Optional

from holmes.common.env_vars import STRICT_TOOL_CALLS_ENABLED
from holmes.core.openai_formatting import apply_strict_mode
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
)

DEFAULT_NOOP_RESPONSE = "The action was performed successfully in the user's browser."


class _FrontendToolBase(Tool):
    """Base for frontend tools that pass the client's raw JSON Schema through
    to the LLM instead of going through the lossy ToolParameter pipeline.

    The raw schema preserves required, enum, nested objects, arrays, anyOf,
    etc. — everything that _parse_tool_parameters used to drop.
    """

    raw_json_schema: Optional[Dict[str, Any]] = None

    def get_openai_format(self) -> Dict[str, Any]:
        """Emit the client's raw JSON Schema directly, with strict mode applied."""
        params_block = self.raw_json_schema or {"type": "object", "properties": {}}
        result: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params_block,
            },
        }
        if STRICT_TOOL_CALLS_ENABLED:
            result = apply_strict_mode(result)
        return result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{self.name}({params})"

    def _get_approval_requirement(self, params: Dict, context: Any) -> None:
        return None

    def _is_restricted(self) -> bool:
        return False


class FrontendPauseTool(_FrontendToolBase):
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


class FrontendNoopTool(_FrontendToolBase):
    """A tool that returns a canned response immediately without pausing.

    The LLM sees the tool and can call it. When it does, the server returns
    a pre-configured response and the LLM continues. The client sees the
    tool call in start_tool_calling and tool_calling_result SSE events and
    can execute it as a fire-and-forget side effect.
    """

    canned_response: str = DEFAULT_NOOP_RESPONSE

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=self.canned_response,
            params=params,
        )

    def invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Skip parent's approval/coercion/transformer logic — just return canned response."""
        return self._invoke(params, context)


def build_frontend_pause_tool(
    name: str,
    description: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> FrontendPauseTool:
    """Create a FrontendPauseTool from a frontend tool definition."""
    return FrontendPauseTool(
        name=name,
        description=description,
        raw_json_schema=parameters,
    )


def build_frontend_noop_tool(
    name: str,
    description: str,
    parameters: Optional[Dict[str, Any]] = None,
    canned_response: Optional[str] = None,
) -> FrontendNoopTool:
    """Create a FrontendNoopTool from a frontend tool definition.

    Args:
        name: Tool name as declared by the client.
        description: Tool description for the LLM.
        parameters: JSON Schema dict for the tool's parameters (OpenAI format).
        canned_response: Response the LLM sees when it calls this tool.
            Defaults to a generic success message.
    """
    return FrontendNoopTool(
        name=name,
        description=description,
        raw_json_schema=parameters,
        canned_response=canned_response or DEFAULT_NOOP_RESPONSE,
    )
