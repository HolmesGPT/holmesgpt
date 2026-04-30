import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


class ToolCallResult(BaseModel):
    tool_call_id: str
    tool_name: str
    description: str
    result: StructuredToolResult
    size: Optional[int] = None
    toolset_name: Optional[str] = None

    def to_llm_message(self, extra_metadata: Optional[Dict[str, Any]] = None, supports_vision: bool = True):
        text_content = format_tool_result_data(
            tool_result=self.result,
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
            extra_metadata=extra_metadata,
        )
        if self.result.images and supports_vision:
            text_content += _build_image_embed_hint(
                tool_call_id=self.tool_call_id,
                url=self.result.url,
            )
            content: List[Dict[str, Any]] = [{"type": "text", "text": text_content}]
            for img in self.result.images:
                data_uri = f"data:{img['mimeType']};base64,{img['data']}"
                content.append({"type": "image_url", "image_url": {"url": data_uri}})
            return {
                "tool_call_id": self.tool_call_id,
                "role": "tool",
                "name": self.tool_name,
                "content": content,
            }
        return {
            "tool_call_id": self.tool_call_id,
            "role": "tool",
            "name": self.tool_name,
            "content": text_content,
        }

    def to_client_dict(self):
        result_dump = self.result.model_dump()
        result_dump["data"] = self.result.get_stringified_data()

        d = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "name": self.tool_name,  # backwards compat: streaming consumers read "name"
            "description": self.description,
            "role": "tool",
            "result": result_dump,
        }
        if self.toolset_name:
            d["toolset_name"] = self.toolset_name
        return d


def _build_image_embed_hint(tool_call_id: str, url: Optional[str] = None) -> str:
    """Build a hint for the LLM explaining how to embed this image in its response.

    The LLM can use ![caption](tool-image://<tool_call_id>) syntax in its analysis.
    The frontend resolves these references against the tool_calls array, rendering
    the base64 image as a clickable link to the source URL (e.g. Grafana dashboard).
    """
    hint = (
        f"\n\nTo embed this image in your response, use exactly this markdown syntax:\n"
        f"![<descriptive caption>](tool-image://{tool_call_id})\n"
        f"The client will render the image inline in your response"
    )
    if url:
        hint += f" with a link to {url}"
    hint += "."
    return hint


def format_tool_result_data(
    tool_result: StructuredToolResult,
    tool_call_id: str,
    tool_name: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    tool_call_metadata: Dict[str, Any] = {}
    if extra_metadata:
        tool_call_metadata.update(extra_metadata)
    # Required fields always take precedence
    tool_call_metadata["tool_name"] = tool_name
    tool_call_metadata["tool_call_id"] = tool_call_id
    tool_response = f"tool_call_metadata={json.dumps(tool_call_metadata)}"

    if tool_result.status == StructuredToolResultStatus.ERROR:
        tool_response += f"{tool_result.error or 'Tool execution failed'}:\n\n"

    tool_response += tool_result.get_stringified_data()

    if tool_result.params:
        tool_response = (
            f"Params used for the tool call: {json.dumps(tool_result.params)}. The tool call output follows on the next line.\n"
            + tool_response
        )
    return tool_response


class PendingToolApproval(BaseModel):
    """Represents a tool call that requires user approval."""

    tool_call_id: str
    tool_name: str
    description: str
    params: Dict[str, Any]


class ToolApprovalDecision(BaseModel):
    """Represents a user's decision on a tool approval."""

    tool_call_id: str
    approved: bool
    save_prefixes: Optional[List[str]] = None  # Prefixes to remember for session
    feedback: Optional[str] = None  # User feedback when denying a tool call
    decision: Optional[Dict[str, Any]] = None  # Structured decision data (e.g. OAuth callback)


class OAuthCallbackRequest(BaseModel):
    toolset_name: str
    code: str
    code_verifier: Optional[str] = None  # Optional: frontend provides when it generated PKCE, Holmes provides when it generated PKCE
    redirect_uri: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None  # Required by some IdPs (e.g. Supabase) that don't support public clients
    user_id: Optional[str] = None


class OAuthCallbackResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class FrontendToolMode(str, Enum):
    PAUSE = "pause"
    NOOP = "noop"


class FrontendToolDefinition(BaseModel):
    """A tool defined by the frontend client for the LLM to call.

    mode="pause" (default): Holmes pauses the stream and asks the client to
    execute the tool, returning results in the next request.

    mode="noop": Holmes returns a canned response immediately and the LLM
    continues without pausing. The client sees the tool call in SSE events
    and can execute it as a fire-and-forget side effect.
    """

    name: str
    description: str
    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="JSON Schema object describing the tool's parameters (OpenAI function calling format)",
    )
    mode: FrontendToolMode = Field(
        default=FrontendToolMode.PAUSE,
        description="'pause' (default): stream pauses, client executes and returns results. "
        "'noop': server returns canned response immediately, client executes as side effect.",
    )
    noop_response: Optional[str] = Field(
        default=None,
        description="Custom canned response for noop-mode tools. "
        "Defaults to 'The action was performed successfully in the user's browser.'",
    )


class FrontendToolResult(BaseModel):
    """Result of a frontend-executed tool, sent by the client to resume the stream."""

    tool_call_id: str
    tool_name: str
    result: str


class PendingFrontendToolCall(BaseModel):
    """A frontend tool call that the LLM requested, awaiting client execution."""

    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any]


class ChatRequestBaseModel(BaseModel):
    conversation_history: Optional[list[dict]] = None
    model: Optional[str] = None
    stream: bool = Field(default=False)
    enable_tool_approval: Optional[bool] = (
        False  # Optional boolean for backwards compatibility
    )
    tool_decisions: Optional[List[ToolApprovalDecision]] = None
    frontend_tools: Optional[List[FrontendToolDefinition]] = Field(
        default=None,
        description="Tools defined by the frontend client. When the LLM calls one, Holmes pauses and asks the client to execute it.",
    )
    frontend_tool_results: Optional[List[FrontendToolResult]] = Field(
        default=None,
        description="Results from frontend-executed tools, sent to resume a paused stream.",
    )
    additional_system_prompt: Optional[str] = None
    trace_span: Optional[Any] = (
        None  # Optional span for tracing and heartbeat callbacks
    )
    user_id: Optional[str] = None  # User ID from relay session token validation

    # In our setup with litellm, the first message in conversation_history
    # should follow the structure [{"role": "system", "content": ...}],
    # where the "role" field is expected to be "system".
    @model_validator(mode="before")
    def check_first_item_role(cls, values):
        conversation_history = values.get("conversation_history")
        if (
            conversation_history
            and isinstance(conversation_history, list)
            and len(conversation_history) > 0
        ):
            first_item = conversation_history[0]
            if not first_item.get("role") == "system":
                raise ValueError(
                    "The first item in conversation_history must contain 'role': 'system'"
                )
        return values


class ChatRequest(ChatRequestBaseModel):
    ask: str
    images: Optional[List[Union[str, Dict[str, Any]]]] = Field(
        default=None,
        description=(
            "List of images to analyze with vision-enabled models. Each item can be:\n"
            "- A string: URL (https://...) or base64 data URI (data:image/jpeg;base64,...)\n"
            "- A dict with keys:\n"
            "  - url (required): URL or base64 data URI\n"
            "  - detail (optional): 'low', 'high', or 'auto' (OpenAI-specific)\n"
            "  - format (optional): MIME type like 'image/jpeg' (for providers that need it)"
        ),
    )
    response_format: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional JSON schema for structured output. Format: {'type': 'json_schema', 'json_schema': {'name': 'ResultName', 'strict': true, 'schema': {...}}}",
    )
    behavior_controls: Optional[Dict[str, bool]] = Field(
        default=None,
        description="Override prompt components (e.g., {'todowrite_instructions': false}). Env var ENABLED_PROMPTS takes precedence.",
    )


class FollowUpAction(BaseModel):
    id: str
    action_label: str
    pre_action_notification_text: str
    prompt: str


class ChatResponse(BaseModel):
    analysis: str
    conversation_history: list[dict]
    tool_calls: Optional[List[ToolCallResult]] = []
    follow_up_actions: Optional[List[FollowUpAction]] = []
    pending_approvals: Optional[List[PendingToolApproval]] = None
    metadata: Optional[Dict[Any, Any]] = None
