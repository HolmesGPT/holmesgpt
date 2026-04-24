import concurrent.futures
import json
from json import tool
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union

# Named logger for user-facing display messages (tool progress, AI messages, etc.)
# In interactive mode this logger is silenced; the CLI renders from stream events instead.
display_logger = logging.getLogger("holmes.display.tool_calling_llm")

import sentry_sdk
from openai import BadRequestError
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
)
from pydantic import BaseModel, Field

from holmes.common.env_vars import (
    LOG_LLM_USAGE_RESPONSE,
    RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION,
    TEMPERATURE,
    load_bool,
)
from holmes.core.llm import LLM
from holmes.core.llm_usage import RequestStats
from holmes.core.models import (
    FrontendToolResult,
    PendingFrontendToolCall,
    PendingToolApproval,
    ToolApprovalDecision,
    ToolCallResult,
)
from holmes.core.oauth_config import OAuthTokenExchangeError, _get_exchange_manager, parse_oauth_decision
from holmes.core.oauth_utils import _get_token_manager
from holmes.core.safeguards import prevent_overly_repeated_tool_call
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.core.tools_utils.tool_context_window_limiter import (
    spill_oversized_tool_result,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.otel_tracing import (
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    ATTR_GEN_AI_USAGE_TOTAL_TOKENS,
    DIM_GEN_AI_REQUEST_MODEL,
    DIM_GEN_AI_SYSTEM,
    DIM_GEN_AI_TOKEN_TYPE,
    DIM_TOOL_NAME,
)
from holmes.core.tracing import DummySpan, TracingFactory
from holmes.core.truncation.input_context_window_limiter import (
    CompactionInsufficientError,
    check_compaction_needed,
    compact_if_necessary,
)
from holmes.utils.colors import AI_COLOR
from holmes.utils.stream import (
    StreamEvents,
    StreamMessage,
    add_token_count_to_metadata,
    build_stream_event_token_count,
)
from holmes.utils.tags import parse_messages_tags


class LLMInterruptedError(Exception):
    """Raised when the user interrupts an in-progress LLM call (e.g. via Escape key)."""

    pass


# Create a named logger for cost tracking
cost_logger = logging.getLogger("holmes.costs")


def _extract_text_from_content(content: Any) -> str:
    """Extract text from message content, handling both string and array formats.

    OpenAI/LiteLLM message content can be:
    - A plain string: "some text"
    - An array of content objects: [{"type": "text", "text": "some text", ...}]

    The array format is used by our cache_control feature (see llm.py add_cache_control_to_last_message)
    which converts string content to a single-item array. For tool messages, there's always
    only one text item containing the full tool output with tool_call_metadata at the start.

    Args:
        content: Message content (string or array)

    Returns:
        Extracted text as a string
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # Tool messages have a single text item (created by format_tool_result_data,
        # possibly wrapped in array by cache_control). Return the first text item.
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return item.get("text", "")

    return ""


def extract_bash_session_prefixes(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract bash session approved prefixes from conversation history.

    Scans tool result messages for bash_session_approved_prefixes stored in
    tool_call_metadata. These prefixes were approved by the user via the
    "Yes, and don't ask again" option.

    Args:
        messages: Conversation history messages

    Returns:
        List of approved prefixes accumulated from all tool results
    """
    prefixes: set[str] = set()

    for msg in messages:
        if msg.get("role") != "tool":
            continue

        content = _extract_text_from_content(msg.get("content", ""))
        if not content:
            continue

        # Extract tool_call_metadata from the content string
        # Format: tool_call_metadata={"tool_name": "...", ...}
        match = re.search(r"tool_call_metadata=(\{[^}]+\})", content)
        if not match:
            continue

        try:
            metadata = json.loads(match.group(1))
            if "bash_session_approved_prefixes" in metadata:
                prefixes.update(metadata["bash_session_approved_prefixes"])
        except (json.JSONDecodeError, KeyError):
            continue

    if prefixes:
        logging.info(
            f"Found {len(prefixes)} session-approved bash prefixes from conversation: {list(prefixes)}"
        )
    return list(prefixes)


def _try_process_oauth_decision(tool_call_id, oauth_code, request_context) -> bool:
    """Exchange an OAuth authorization code for tokens. Returns True on success."""
    try:
        _get_exchange_manager().complete_exchange(tool_call_id, oauth_code, request_context)
        return True
    except Exception as e:
        logging.error(f"Failed to process OAuth decision: {e}", exc_info=True)
        return False


# Callback type: receives a pending approval, returns (approved, optional_feedback)
ApprovalCallback = Callable[[PendingToolApproval], tuple[bool, Optional[str]]]


class LLMResult(RequestStats):
    tool_calls: Optional[List[ToolCallResult]] = None
    num_llm_calls: Optional[int] = None  # Number of LLM API calls (turns)
    result: Optional[str] = None
    unprocessed_result: Optional[str] = None
    instructions: List[str] = Field(default_factory=list)
    messages: Optional[List[dict]] = None
    metadata: Optional[Dict[Any, Any]] = None


class ToolCallWithDecision(BaseModel):
    message_index: int
    tool_call: ChatCompletionMessageToolCall
    decision: Optional[ToolApprovalDecision]


class ToolCallingLLM:
    llm: LLM

    def __init__(
        self,
        tool_executor: ToolExecutor,
        max_steps: int,
        llm: LLM,
        tool_results_dir: Optional[Path],
        tracer=None,
    ):
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.tracer = tracer
        self.llm = llm
        self.tool_results_dir = tool_results_dir

        self._runbook_in_use: bool = False

    def with_executor(self, tool_executor: ToolExecutor) -> "ToolCallingLLM":
        """Return a shallow copy with a different ToolExecutor.

        Used to inject per-request frontend tools via a cloned executor
        without mutating the shared ToolCallingLLM instance.
        """
        clone = ToolCallingLLM(
            tool_executor=tool_executor,
            max_steps=self.max_steps,
            llm=self.llm,
            tool_results_dir=self.tool_results_dir,
            tracer=self.tracer,
        )
        # Preserve transient state so resumed turns keep access to
        # runbook-unlocked (restricted) tools.
        clone._runbook_in_use = self._runbook_in_use
        return clone

    def reset_interaction_state(self) -> None:
        """
        For interactive loop, reset runbooks in use
        """
        self._runbook_in_use = False

    def _supports_vision(self) -> bool:
        """Check if vision/multimodal input is enabled.

        Always True unless explicitly disabled via HOLMES_DISABLE_VISION=true.
        """
        return not load_bool("HOLMES_DISABLE_VISION", False)

    def _has_bash_for_file_access(self) -> bool:
        """Check if bash toolset is available for reading saved tool result files."""
        for toolset in self.tool_executor.enabled_toolsets:
            if toolset.name == "bash":
                config = toolset.config
                if config:
                    return config.builtin_allowlist != "none"
                return False
        return False

    def _execute_tool_decisions(
        self,
        messages: List[Dict[str, Any]],
        tool_decisions: List[ToolApprovalDecision],
        request_context: Optional[Dict[str, Any]] = None,
        trace_span: Any = None,
    ) -> tuple[List[Dict[str, Any]], list[StreamMessage]]:
        """Execute approved tools and record rejections for denied ones.

        Called after the user (CLI callback or HTTP client) has decided on each
        pending tool call. Re-invokes approved tools with user_approved=True,
        and injects denial errors for rejected ones.

        Returns:
            Updated messages list with tool execution results and stream events.
        """
        if trace_span is None:
            trace_span = DummySpan()

        events: list[StreamMessage] = []
        if not tool_decisions:
            return messages, events

        # Create decision lookup
        decisions_by_tool_call_id = {
            decision.tool_call_id: decision for decision in tool_decisions
        }

        pending_tool_calls: list[ToolCallWithDecision] = []

        for i in reversed(range(len(messages))):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                message_tool_calls = msg.get("tool_calls", [])
                for tool_call in message_tool_calls:
                    decision = decisions_by_tool_call_id.get(tool_call.get("id"), None)
                    if tool_call.get("pending_approval"):
                        del tool_call[
                            "pending_approval"
                        ]  # Cleanup so that a pending approval is not tagged on message in a future response
                        pending_tool_calls.append(
                            ToolCallWithDecision(
                                tool_call=ChatCompletionMessageToolCall(**tool_call),
                                decision=decision,
                                message_index=i,
                            )
                        )

        if not pending_tool_calls:
            error_message = f"Received {len(tool_decisions)} tool decisions but no pending approvals found in conversation history"
            logging.error(error_message)
            raise Exception(error_message)
        # Extract existing session prefixes from conversation history
        session_prefixes = extract_bash_session_prefixes(messages)

        for tool_call_with_decision in pending_tool_calls:
            tool_call = tool_call_with_decision.tool_call
            tool_decision = tool_call_with_decision.decision
            tool_result: Optional[ToolCallResult] = None
            if tool_decision and tool_decision.approved:
                # Process OAuth auth code exchange if this decision carries one
                oauth_code = parse_oauth_decision(tool_decision.decision)
                user_id = (request_context or {}).get("user_id")
                if oauth_code and user_id:
                    oauth_success = _try_process_oauth_decision(tool_call.id, oauth_code, request_context)
                    toolset = self.tool_executor._tool_to_toolset.get(tool_call.function.name) if oauth_success else None
                    if oauth_success and toolset:
                        self.tool_executor.oauth_connector.load_tools_for_user(user_id, toolset, request_context)
                    else:
                        tool_result = ToolCallResult(
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.function.name,
                            description="",
                            result="OAuth authentication failed. Please try again.",  # type: ignore
                        )

                if not tool_result:
                    tool_result = self._invoke_llm_tool_call(
                        tool_to_call=tool_call,
                        previous_tool_calls=[],
                        trace_span=trace_span,
                        tool_number=None,
                        user_approved=True,
                        session_approved_prefixes=session_prefixes,
                        request_context=request_context,
                        enable_tool_approval=True,  # always True when processing decisions
                    )
            else:
                # Tool was rejected or no decision found, add rejection message
                feedback_text = (
                    f" User feedback: {tool_decision.feedback}"
                    if tool_decision and tool_decision.feedback
                    else ""
                )
                tool_result = ToolCallResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.function.name,
                    description=tool_call.function.name,
                    result=StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=f"Tool execution was denied by the user.{feedback_text}",
                    ),
                )

            events.append(
                StreamMessage(
                    event=StreamEvents.TOOL_RESULT,
                    data=tool_result.to_client_dict(),
                )
            )

            # If user chose "Yes, and don't ask again", include prefixes in metadata
            extra_metadata = None
            if tool_decision and tool_decision.approved and tool_decision.save_prefixes:
                logging.info(
                    f"Saving bash session prefixes for future commands: {tool_decision.save_prefixes}"
                )
                extra_metadata = {
                    "bash_session_approved_prefixes": tool_decision.save_prefixes
                }

            tool_call_message = tool_result.to_llm_message(
                extra_metadata=extra_metadata,
                supports_vision=self._supports_vision(),
            )

            # It is expected that the tool call result directly follows the tool call request from the LLM
            # The API call may contain a user ask which is appended to the messages so we can't just append
            # tool call results; they need to be inserted right after the llm's message requesting tool calls
            messages.insert(
                tool_call_with_decision.message_index + 1, tool_call_message
            )

        return messages, events

    @staticmethod
    def _process_frontend_tool_results(
        messages: List[Dict[str, Any]],
        frontend_tool_results: List[FrontendToolResult],
    ) -> tuple[List[Dict[str, Any]], list[StreamMessage]]:
        """Inject frontend tool results into the conversation history.

        Called when the client sends results for tools it executed locally.
        Finds the pending frontend tool calls in messages, clears their
        pending flag, and inserts tool result messages.

        Returns:
            Updated messages list and stream events for each result.
        """
        events: list[StreamMessage] = []
        if not frontend_tool_results:
            return messages, events

        results_by_id = {r.tool_call_id: r for r in frontend_tool_results}
        matched_ids: set[str] = set()

        # Find pending frontend tool calls in messages (reverse to insert correctly)
        for i in reversed(range(len(messages))):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tool_call in msg.get("tool_calls", []):
                    if not tool_call.get("pending_frontend"):
                        continue

                    tool_call_id = tool_call.get("id")
                    result = results_by_id.get(tool_call_id)
                    if tool_call_id:
                        matched_ids.add(tool_call_id)
                    if not result:
                        logging.warning(
                            f"No frontend result for pending tool call {tool_call.get('id')}"
                        )
                        # Insert an error so the LLM knows
                        tool_result_msg = {
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "name": tool_call.get("function", {}).get("name", "unknown"),
                            "content": "Error: frontend did not return a result for this tool call.",
                        }
                    else:
                        tool_result_msg = {
                            "tool_call_id": result.tool_call_id,
                            "role": "tool",
                            "name": result.tool_name,
                            "content": result.result,
                        }

                    # Clean up the pending flag
                    del tool_call["pending_frontend"]

                    # Insert result right after the assistant message
                    messages.insert(i + 1, tool_result_msg)

                    tool_result = ToolCallResult(
                        tool_call_id=tool_call["id"],
                        tool_name=tool_call.get("function", {}).get("name", "unknown"),
                        description=f"Frontend tool: {tool_call.get('function', {}).get('name', 'unknown')}",
                        result=StructuredToolResult(
                            status=StructuredToolResultStatus.SUCCESS if result else StructuredToolResultStatus.ERROR,
                            data=result.result if result else None,
                            error="Frontend did not return a result" if not result else None,
                        ),
                    )
                    events.append(
                        StreamMessage(
                            event=StreamEvents.TOOL_RESULT,
                            data=tool_result.to_client_dict(),
                        )
                    )

        # Warn about results that didn't match any pending frontend tool call
        unmatched = set(results_by_id.keys()) - matched_ids
        if unmatched:
            logging.warning(
                f"Frontend tool results provided for unknown tool_call_ids (ignored): {unmatched}"
            )

        return messages, events

    def _should_include_restricted_tools(self) -> bool:
        """Check if restricted tools should be included in the tools list."""
        return self._runbook_in_use

    def _get_tools(self) -> list:
        """Get tools list, filtering restricted tools based on authorization.

        If a user_id is available (from request_context), per-user OAuth tools
        replace _connect placeholders for authenticated users.
        """
        user_id = (self._request_context or {}).get("user_id") if hasattr(self, "_request_context") else None
        return self.tool_executor.get_all_tools_openai_format(
            include_restricted=self._should_include_restricted_tools(),
            user_id=user_id,
        )

    @sentry_sdk.trace
    def call(  # type: ignore
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        trace_span=DummySpan(),
        tool_number_offset: int = 0,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
        approval_callback: Optional[ApprovalCallback] = None,
    ) -> LLMResult:
        """Synchronous wrapper around call_stream(). Drains the generator
        and reconstructs an LLMResult."""

        all_tool_calls: list[dict] = []
        tool_decisions: Optional[List[ToolApprovalDecision]] = None
        total_num_llm_calls = 0
        accumulated_stats = RequestStats()

        while True:
            stream = self.call_stream(
                msgs=messages,
                response_format=response_format,
                enable_tool_approval=approval_callback is not None,
                tool_decisions=tool_decisions,
                trace_span=trace_span,
                cancel_event=cancel_event,
                tool_number_offset=tool_number_offset,
                request_context=request_context,
                iteration_offset=total_num_llm_calls,
            )

            tool_decisions = None
            terminal_data = None
            terminal_event = None
            start_tool_count = 0
            saw_tool_results = False

            for event in stream:
                # Log blank line when a tool batch ends (transition away from TOOL_RESULT)
                if saw_tool_results and event.event != StreamEvents.TOOL_RESULT:
                    display_logger.info("")
                    saw_tool_results = False

                if event.event == StreamEvents.START_TOOL:
                    start_tool_count += 1
                elif event.event == StreamEvents.TOOL_RESULT:
                    tool_number_offset += 1
                    saw_tool_results = True
                    all_tool_calls.append(event.data)
                    if start_tool_count > 0:
                        display_logger.info(
                            f"The AI requested [bold]{start_tool_count}[/bold] tool call(s)."
                        )
                        start_tool_count = 0
                elif event.event == StreamEvents.AI_MESSAGE:
                    reasoning = event.data.get("reasoning")
                    content = event.data.get("content")
                    if reasoning:
                        display_logger.info(
                            f"[italic dim]AI reasoning:\n\n{reasoning}[/italic dim]\n"
                        )
                    if content and content.strip():
                        display_logger.info(
                            f"[bold {AI_COLOR}]AI:[/bold {AI_COLOR}] {content}"
                        )
                elif event.event in (
                    StreamEvents.ANSWER_END,
                    StreamEvents.APPROVAL_REQUIRED,
                ):
                    terminal_data = event.data
                    terminal_event = event.event
                    break

            if not terminal_data:
                raise Exception("Stream ended without ANSWER_END or APPROVAL_REQUIRED")

            # call_stream returns the absolute iteration count (including offset),
            # so we assign rather than accumulate to avoid double-counting.
            total_num_llm_calls = terminal_data.get("num_llm_calls", 0)
            accumulated_stats += RequestStats(**terminal_data.get("costs", {}))

            if terminal_event == StreamEvents.APPROVAL_REQUIRED:
                # Check if there are frontend tool calls — can't execute in sync mode
                pending_frontend = terminal_data.get("pending_frontend_tool_calls", [])
                if pending_frontend:
                    logging.warning(
                        "Frontend tool calls requested but no frontend available in sync mode. "
                        f"Pending: {[fc['tool_name'] for fc in pending_frontend]}"
                    )
                    return LLMResult(
                        result="Investigation paused: the AI requested frontend-defined tools that cannot be executed in sync mode.",
                        tool_calls=all_tool_calls,  # type: ignore
                        num_llm_calls=total_num_llm_calls,
                        messages=terminal_data.get("messages"),
                        metadata=terminal_data.get("metadata"),
                        **accumulated_stats.model_dump(),
                    )

                # Only approval pauses — prompt via callback and continue
                messages = terminal_data["messages"]
                tool_decisions = self._prompt_for_approval_decisions(
                    terminal_data["pending_approvals"],
                    approval_callback,
                )
                continue

            # ANSWER_END — deduplicate tool calls keeping last per ID
            deduped: dict[str, dict] = {}
            for tc in all_tool_calls:
                deduped[tc.get("tool_call_id", id(tc))] = tc
            return LLMResult(
                result=terminal_data["content"],
                tool_calls=list(deduped.values()),
                num_llm_calls=total_num_llm_calls,
                messages=terminal_data["messages"],
                metadata=terminal_data.get("metadata"),
                **accumulated_stats.model_dump(),
            )

    def _prompt_for_approval_decisions(
        self,
        pending_approvals: List[dict],
        approval_callback: Optional[ApprovalCallback] = None,
    ) -> List[ToolApprovalDecision]:
        """Prompt the user for approval decisions on each pending tool call.

        For CLI: the approval_callback shows an interactive menu per tool.
        When a user approves one tool with "save prefix", a subsequent tool
        in the same batch with the same prefix is auto-approved (re-check).
        """
        decisions: List[ToolApprovalDecision] = []
        for approval_dict in pending_approvals:
            approval = PendingToolApproval(**approval_dict)

            # Re-check: a previous approval in this batch may have saved
            # the prefix to disk, making this tool no longer need approval.
            if self._is_tool_call_already_approved(approval.tool_name, approval.params):
                logging.debug(f"Approval no longer needed for {approval.tool_name}")
                decisions.append(
                    ToolApprovalDecision(
                        tool_call_id=approval.tool_call_id,
                        approved=True,
                    )
                )
                continue

            if not approval_callback:
                decisions.append(
                    ToolApprovalDecision(
                        tool_call_id=approval.tool_call_id,
                        approved=False,
                    )
                )
                continue

            approved, feedback = approval_callback(approval)
            decisions.append(
                ToolApprovalDecision(
                    tool_call_id=approval.tool_call_id,
                    approved=approved,
                    feedback=feedback if not approved else None,
                )
            )

        return decisions

    def _directly_invoke_tool_call(
        self,
        tool_name: str,
        tool_params: dict,
        user_approved: bool,
        tool_call_id: str,
        tool_number: Optional[int] = None,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        # Ensure the toolset is initialized (lazy initialization on first use)
        init_error = self.tool_executor.ensure_toolset_initialized(tool_name)
        if isinstance(init_error, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=init_error,
                params=tool_params,
            )

        user_id = (request_context or {}).get("user_id")
        tool = self.tool_executor.get_tool_by_name(tool_name, user_id=user_id)
        if not tool:
            logging.warning(
                f"Skipping tool execution for {tool_name}: args: {tool_params}"
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to find tool {tool_name}",
                params=tool_params,
            )

        try:
            invoke_context = ToolInvokeContext(
                tool_number=tool_number,
                user_approved=user_approved,
                llm=self.llm,
                max_token_count=self.llm.get_max_token_count_for_single_tool(),
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                session_approved_prefixes=session_approved_prefixes or [],
                request_context=request_context,
            )
            tool_response = tool.invoke(tool_params, context=invoke_context)

            # Store OAuth tools discovered by a _connect placeholder
            if tool_response.oauth_tools:
                effective_user = _get_token_manager().require_user_id(request_context)
                toolset_name = self.tool_executor.get_toolset_name(tool_name, user_id=user_id)
                if toolset_name:
                    self.tool_executor.oauth_connector.store_user_tools(effective_user, toolset_name, tool_response.oauth_tools)

            # Track runbook usage - if fetch_runbook is called successfully,
            # restricted tools become available for the rest of the current request
            if (
                tool_name == "fetch_runbook"
                and tool_response.status == StructuredToolResultStatus.SUCCESS
            ):
                self._runbook_in_use = True
                logging.debug("Runbook fetched - restricted tools now available")

        except Exception as e:
            logging.error(
                f"Tool call to {tool_name} failed with an Exception", exc_info=True
            )
            tool_response = StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Tool call failed: {e}",
                params=tool_params,
            )
        return tool_response

    @staticmethod
    def _log_tool_call_result(
        tool_span,
        tool_call_result: ToolCallResult,
        approval_possible=True,
        original_token_count=None,
        image_count=0,
    ):
        tool_span.set_attributes(name=tool_call_result.tool_name)
        status = tool_call_result.result.status

        is_oauth = "__oauth_metadata" in (tool_call_result.result.params or {})
        if (
            status == StructuredToolResultStatus.APPROVAL_REQUIRED
            and not approval_possible
            and not is_oauth
        ):
            status = StructuredToolResultStatus.ERROR

        if status == StructuredToolResultStatus.ERROR:
            error = (
                tool_call_result.result.error
                if tool_call_result.result.error
                else "Unspecified error"
            )
        else:
            error = None

        # Include images in output if present (before spill clears them)
        images = tool_call_result.result.images
        if images:
            output = {
                "data": tool_call_result.result.data,
                "images": [
                    {
                        "mimeType": img.get("mimeType", ""),
                        "data_length": len(img.get("data", "")),
                    }
                    for img in images
                ],
            }
        else:
            output = tool_call_result.result.data

        metadata = {
            "status": status,
            "description": tool_call_result.description,
            "return_code": tool_call_result.result.return_code,
            "error": tool_call_result.result.error,
            "original_token_count": original_token_count,
        }
        if image_count > 0:
            metadata["image_count"] = image_count

        tool_span.log(
            input=tool_call_result.result.params,
            output=output,
            error=error,
            metadata=metadata,
        )

    def _invoke_llm_tool_call(
        self,
        tool_to_call: ChatCompletionMessageToolCall,
        previous_tool_calls: list[dict],
        trace_span=None,
        tool_number=None,
        user_approved: bool = False,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
        enable_tool_approval: bool = False,
    ) -> ToolCallResult:
        if trace_span is None:
            trace_span = DummySpan()
        # Extract tool name early for span naming
        tool_name_for_span = getattr(getattr(tool_to_call, "function", None), "name", "unknown_tool")
        _tool_start = time.time()
        with trace_span.start_span(name=f"holmesgpt.tool.{tool_name_for_span}", type="tool") as tool_span:
            # ChatCompletionMessageToolCall is a union of FunctionToolCall (has 'function')
            # and CustomToolCall (has 'custom'). We only support function tool calls.
            if not hasattr(tool_to_call, "function"):
                logging.error(f"Unsupported custom tool call: {tool_to_call}")
                tool_call_result = ToolCallResult(
                    tool_call_id=tool_to_call.id,
                    tool_name="Unknown_Custom_Tool",
                    description="NA",
                    result=StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="Custom tool calls are not supported",
                        params=None,
                    ),
                )
                ToolCallingLLM._log_tool_call_result(
                    tool_span, tool_call_result, enable_tool_approval
                )
                return tool_call_result

            tool_name = tool_to_call.function.name
            tool_arguments = tool_to_call.function.arguments
            tool_id = tool_to_call.id

            tool_params = {}
            try:
                tool_params = json.loads(tool_arguments)
            except Exception:
                logging.warning(
                    f"Failed to parse arguments for tool: {tool_name}. args: {tool_arguments}"
                )

            tool_response = None
            if not user_approved:
                tool_response = prevent_overly_repeated_tool_call(
                    tool_name=tool_name,
                    tool_params=tool_params,
                    tool_calls=previous_tool_calls,
                )

            if not tool_response:
                tool_response = self._directly_invoke_tool_call(
                    tool_name=tool_name,
                    tool_params=tool_params,
                    user_approved=user_approved,
                    tool_number=tool_number,
                    tool_call_id=tool_id,
                    session_approved_prefixes=session_approved_prefixes,
                    request_context=request_context,
                )

            user_id = (request_context or {}).get("user_id")
            tool = self.tool_executor.get_tool_by_name(tool_name, user_id=user_id)
            toolset_name = self.tool_executor.get_toolset_name(tool_name, user_id=user_id)
            tool_call_result = ToolCallResult(
                tool_call_id=tool_id,
                tool_name=tool_name,
                description=str(tool.get_parameterized_one_liner(tool_params))
                if tool
                else "",
                result=tool_response,
                toolset_name=toolset_name if isinstance(toolset_name, str) else None,
            )

            # Save image count before spill_oversized_tool_result clears them
            image_count = (
                len(tool_call_result.result.images)
                if tool_call_result.result.images
                else 0
            )

            # See docs/reference/context-management.md for how this fits with compaction
            original_token_count = spill_oversized_tool_result(
                tool_call_result=tool_call_result,
                llm=self.llm,
                tool_results_dir=self.tool_results_dir
                if self.tool_results_dir and self._has_bash_for_file_access()
                else None,
            )

            # Record OTel tool span attributes
            tool_span.log(metadata={
                "holmesgpt.tool.name": tool_call_result.tool_name,
                "holmesgpt.tool.status": tool_call_result.result.status.value if tool_call_result.result.status else "unknown",
            })

            # Record OTel tool call metrics
            otel_metrics = TracingFactory.get_metrics()
            if otel_metrics:
                tool_attrs = {DIM_TOOL_NAME: tool_call_result.tool_name}
                otel_metrics.tool_call_count.add(1, tool_attrs)
                otel_metrics.tool_call_duration.record(time.time() - _tool_start, tool_attrs)
                if tool_call_result.result.status == StructuredToolResultStatus.ERROR:
                    otel_metrics.tool_call_errors.add(1, tool_attrs)

            ToolCallingLLM._log_tool_call_result(
                tool_span,
                tool_call_result,
                enable_tool_approval,
                original_token_count,
                image_count,
            )
            return tool_call_result

    def _is_tool_call_already_approved(
        self,
        tool_name: str,
        params: dict,
        session_approved_prefixes: Optional[List[str]] = None,
    ) -> bool:
        """Check whether a tool call would pass approval without user interaction.

        Checks both static allow lists (config + CLI-saved prefixes) and
        optionally session-approved prefixes from the conversation history.
        """
        tool = self.tool_executor.get_tool_by_name(tool_name)
        if not tool:
            return False
        context = ToolInvokeContext(
            llm=self.llm,
            max_token_count=self.llm.get_max_token_count_for_single_tool(),
            tool_name=tool_name,
            tool_call_id="",
            session_approved_prefixes=session_approved_prefixes or [],
        )
        approval = tool.requires_approval(params, context)
        return not approval or not approval.needs_approval

    def _emit_token_count(
        self,
        messages: list[dict],
        tools: Optional[list],
        full_response: Any,
        limit_result: Any,
        metadata: Dict[Any, Any],
        stats: RequestStats,
    ) -> StreamMessage:
        """Build a TOKEN_COUNT event with current token usage and costs."""
        tokens = self.llm.count_tokens(messages=messages, tools=tools)
        add_token_count_to_metadata(
            tokens=tokens,
            full_llm_response=full_response,
            max_context_size=limit_result.max_context_size,
            maximum_output_token=limit_result.maximum_output_token,
            metadata=metadata,
        )
        metadata["costs"] = stats.model_dump()
        return build_stream_event_token_count(metadata=metadata)

    def call_stream(
        self,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        msgs: Optional[list[dict]] = None,
        enable_tool_approval: bool = False,
        tool_decisions: List[ToolApprovalDecision] | None = None,
        frontend_tool_results: Optional[List[FrontendToolResult]] = None,
        request_context: Optional[Dict[str, Any]] = None,
        trace_span: Any = None,
        cancel_event: Optional[threading.Event] = None,
        tool_number_offset: int = 0,
        iteration_offset: int = 0,
    ):
        """
        This function DOES NOT call llm.completion(stream=true).
        This function streams holmes one iteration at a time instead of waiting for all iterations to complete.

        Frontend tools: Frontend tools are registered as FrontendPauseTool instances
        in the ToolExecutor (via clone_with_extra_tools). When the LLM calls one,
        it returns FRONTEND_PAUSE status. call_stream handles this by pausing the
        stream with an APPROVAL_REQUIRED event containing pending_frontend_tool_calls.
        The client executes the tool and resumes by sending frontend_tool_results.
        """
        if trace_span is None:
            trace_span = DummySpan()

        self._request_context = request_context
        all_tool_calls: list[dict] = []

        # Process tool decisions if provided (approval resume)
        if msgs and tool_decisions:
            logging.info(f"Processing {len(tool_decisions)} tool decisions")
            msgs, events = self._execute_tool_decisions(
                msgs, tool_decisions, request_context, trace_span=trace_span
            )
            for ev in events:
                yield ev
                # Collect tool results from approval re-invocations
                if ev.event == StreamEvents.TOOL_RESULT:
                    all_tool_calls.append(ev.data)

        # Process frontend tool results if provided (frontend tool resume)
        if msgs and frontend_tool_results:
            logging.info(f"Processing {len(frontend_tool_results)} frontend tool results")
            msgs, events = self._process_frontend_tool_results(msgs, frontend_tool_results)
            for ev in events:
                yield ev
                if ev.event == StreamEvents.TOOL_RESULT:
                    all_tool_calls.append(ev.data)

        messages: list[dict] = list(msgs) if msgs else []
        tool_calls: list[dict] = []
        tools: Optional[list] = self._get_tools()
        max_steps = self.max_steps
        metadata: Dict[Any, Any] = {}
        stats = RequestStats()
        if iteration_offset < 0:
            raise ValueError("iteration_offset must be non-negative")
        i = iteration_offset

        while i < max_steps:
            if cancel_event and cancel_event.is_set():
                raise LLMInterruptedError()

            i += 1
            logging.debug(f"running iteration {i}")

            tools = None if i == max_steps else tools
            tool_choice = "auto" if tools else None

            compaction_start_event = check_compaction_needed(self.llm, messages, tools)
            if compaction_start_event:
                yield compaction_start_event

            try:
                limit_result = compact_if_necessary(
                    llm=self.llm, messages=messages, tools=tools
                )
            except CompactionInsufficientError as e:
                yield from e.events
                if e.compaction_usage and e.compaction_usage.total_tokens > 0:
                    stats += e.compaction_usage
                raise

            yield from limit_result.events
            messages = limit_result.messages
            metadata = metadata | limit_result.metadata

            # After compaction, emit a fresh token count so clients can update
            if limit_result.conversation_history_compacted:
                yield build_stream_event_token_count(
                    metadata={
                        "tokens": limit_result.tokens.model_dump(),
                        "max_tokens": limit_result.max_context_size,
                        "max_output_tokens": limit_result.maximum_output_token,
                    }
                )

            # Accumulate compaction costs
            compaction = limit_result.compaction_usage
            if compaction and compaction.total_tokens > 0:
                compaction.num_compactions = 1
                stats += compaction
                cost_logger.debug(
                    f"Compaction cost (streaming): ${compaction.total_cost:.6f} | "
                    f"Tokens: {compaction.prompt_tokens} prompt + {compaction.completion_tokens} completion = {compaction.total_tokens} total"
                )

            if (
                limit_result.conversation_history_compacted
                and RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION
            ):
                tool_calls = []

            logging.debug(f"sending messages={messages}\n\ntools={tools}")

            # Create a child gen_ai.chat span for each LLM call iteration.
            # The span is activated in context so httpx calls during completion()
            # (e.g. LiteLLM HTTP calls) become children of this gen_ai.chat span.
            with trace_span.start_span(name="gen_ai.chat") as llm_span:
              try:
                _llm_call_start = time.time()
                full_response = self.llm.completion(
                    messages=parse_messages_tags(messages),  # type: ignore
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    temperature=TEMPERATURE,
                    stream=False,
                    drop_params=True,
                )

                # Accumulate cost information for this iteration
                response_stats = RequestStats.from_response(full_response)
                if response_stats.total_tokens > 0:
                    cost_logger.debug(
                        f"LLM iteration cost: ${response_stats.total_cost:.6f} | "
                        f"Tokens: {response_stats.prompt_tokens} prompt + {response_stats.completion_tokens} completion = {response_stats.total_tokens} total"
                    )
                elif response_stats.total_cost > 0:
                    cost_logger.debug(
                        f"LLM iteration cost: ${response_stats.total_cost:.6f} | Token usage not available"
                    )
                if LOG_LLM_USAGE_RESPONSE:
                    usage = getattr(full_response, "usage", None)
                    if usage:
                        logging.info(f"LLM usage response:\n{usage}\n")
                stats += response_stats

                # Record OTel LLM metrics
                otel_metrics = TracingFactory.get_metrics()
                if otel_metrics:
                    model_attrs = {DIM_GEN_AI_REQUEST_MODEL: self.llm.model, DIM_GEN_AI_SYSTEM: "litellm"}
                    if response_stats.prompt_tokens > 0:
                        otel_metrics.token_usage.add(response_stats.prompt_tokens, {**model_attrs, DIM_GEN_AI_TOKEN_TYPE: "input"})
                    if response_stats.completion_tokens > 0:
                        otel_metrics.token_usage.add(response_stats.completion_tokens, {**model_attrs, DIM_GEN_AI_TOKEN_TYPE: "output"})
                    llm_duration = time.time() - _llm_call_start
                    otel_metrics.llm_call_duration.record(llm_duration, model_attrs)

                # Log GenAI semantic convention attributes on the LLM child span
                llm_span.log(metadata={
                    ATTR_GEN_AI_SYSTEM: "litellm",
                    ATTR_GEN_AI_REQUEST_MODEL: self.llm.model,
                    ATTR_GEN_AI_USAGE_INPUT_TOKENS: stats.prompt_tokens,
                    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS: stats.completion_tokens,
                    ATTR_GEN_AI_USAGE_TOTAL_TOKENS: stats.total_tokens,
                    "holmesgpt.iteration": i,
                })

              # catch a known error that occurs with Azure and replace the error message with something more obvious to the user
              except BadRequestError as e:
                if "Unrecognized request arguments supplied: tool_choice, tools" in str(
                    e
                ):
                    raise Exception(
                        "The Azure model you chose is not supported. Model version 1106 and higher required."
                    ) from e
                else:
                    logging.error(
                        f"LLM BadRequestError on model={self.llm.model} (streaming iteration {i}): {e}",
                        exc_info=True,
                    )
                    raise
              except Exception as e:
                logging.error(
                    f"LLM call failed on model={self.llm.model} (streaming iteration {i}): "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise

            if cancel_event and cancel_event.is_set():
                raise LLMInterruptedError()

            response_message = full_response.choices[0].message  # type: ignore

            messages.append(
                response_message.model_dump(
                    exclude_defaults=True, exclude_unset=True, exclude_none=True
                )
            )

            yield self._emit_token_count(
                messages, tools, full_response, limit_result, metadata, stats
            )

            tools_to_call = getattr(response_message, "tool_calls", None)
            if not tools_to_call:
                yield StreamMessage(
                    event=StreamEvents.ANSWER_END,
                    data={
                        "content": response_message.content,
                        "messages": messages,
                        "metadata": metadata,
                        "tool_calls": all_tool_calls,
                        "num_llm_calls": i,
                        "prompt": json.dumps(messages, indent=2),
                        "costs": stats.model_dump(),
                    },
                )
                return

            reasoning = getattr(response_message, "reasoning_content", None)
            message = response_message.content
            if reasoning or message:
                yield StreamMessage(
                    event=StreamEvents.AI_MESSAGE,
                    data={
                        "content": message,
                        "reasoning": reasoning,
                        "metadata": metadata,
                    },
                )

            # Check if any tools require approval or are frontend-defined
            pending_approvals = []
            pending_frontend_calls: list[PendingFrontendToolCall] = []

            # Extract session approved prefixes from conversation history
            session_prefixes = extract_bash_session_prefixes(messages)

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = []
                for tool_index, t in enumerate(tools_to_call, 1):  # type: ignore
                    tool_number = tool_number_offset + tool_index

                    future = executor.submit(
                        self._invoke_llm_tool_call,
                        tool_to_call=t,  # type: ignore
                        previous_tool_calls=tool_calls,
                        trace_span=trace_span,
                        tool_number=tool_number,
                        session_approved_prefixes=session_prefixes,
                        request_context=request_context,
                        enable_tool_approval=enable_tool_approval,
                    )
                    futures.append(future)
                    yield StreamMessage(
                        event=StreamEvents.START_TOOL,
                        data={"tool_name": t.function.name, "id": t.id},
                    )

                for future in concurrent.futures.as_completed(futures):
                    if cancel_event and cancel_event.is_set():
                        for f in futures:
                            f.cancel()
                        raise LLMInterruptedError()

                    tool_call_result: ToolCallResult = future.result()

                    tool_result_dict = tool_call_result.to_client_dict()

                    if (
                        tool_call_result.result.status
                        == StructuredToolResultStatus.APPROVAL_REQUIRED
                    ):
                        # OAuth approvals are always sent to frontend (user must authenticate)
                        is_oauth = "__oauth_metadata" in (tool_call_result.result.params or {})
                        if enable_tool_approval or is_oauth:
                            pending_approvals.append(
                                PendingToolApproval(
                                    tool_call_id=tool_call_result.tool_call_id,
                                    tool_name=tool_call_result.tool_name,
                                    description=tool_call_result.description,
                                    params=tool_call_result.result.params or {},
                                )
                            )

                            all_tool_calls.append(tool_result_dict)
                            yield StreamMessage(
                                event=StreamEvents.TOOL_RESULT,
                                data=tool_result_dict,
                            )
                        else:
                            tool_call_result.result.status = (
                                StructuredToolResultStatus.ERROR
                            )
                            tool_call_result.result.error = f"Tool call rejected for security reasons: {tool_call_result.result.error}"
                            tool_result_dict = tool_call_result.to_client_dict()

                            tool_calls.append(tool_result_dict)
                            all_tool_calls.append(tool_result_dict)
                            messages.append(
                                tool_call_result.to_llm_message(
                                    supports_vision=self._supports_vision()
                                )
                            )

                            yield StreamMessage(
                                event=StreamEvents.TOOL_RESULT,
                                data=tool_result_dict,
                            )

                    elif (
                        tool_call_result.result.status
                        == StructuredToolResultStatus.FRONTEND_PAUSE
                    ):
                        # Frontend tool — collect for pause, don't feed result to LLM
                        pending_frontend_calls.append(
                            PendingFrontendToolCall(
                                tool_call_id=tool_call_result.tool_call_id,
                                tool_name=tool_call_result.tool_name,
                                arguments=tool_call_result.result.params or {},
                            )
                        )
                        frontend_call_dict = {
                            "tool_call_id": tool_call_result.tool_call_id,
                            "tool_name": tool_call_result.tool_name,
                            "name": tool_call_result.tool_name,
                        }
                        tool_calls.append(frontend_call_dict)
                        all_tool_calls.append(frontend_call_dict)

                    else:
                        tool_calls.append(tool_result_dict)
                        all_tool_calls.append(tool_result_dict)
                        messages.append(tool_call_result.to_llm_message())

                        yield StreamMessage(
                            event=StreamEvents.TOOL_RESULT,
                            data=tool_result_dict,
                        )

                # Emit updated token counts after tool results
                yield self._emit_token_count(
                    messages, tools, full_response, limit_result, metadata, stats
                )

                # Mark any pending frontend tool calls in assistant messages
                if pending_frontend_calls:
                    for fc in pending_frontend_calls:
                        tool_call = self.find_assistant_tool_call_request(
                            tool_call_id=fc.tool_call_id, messages=messages
                        )
                        tool_call["pending_frontend"] = True

                # Mark any pending approval tool calls in assistant messages
                if pending_approvals:
                    for approval in pending_approvals:
                        tool_call = self.find_assistant_tool_call_request(
                            tool_call_id=approval.tool_call_id, messages=messages
                        )
                        tool_call["pending_approval"] = True

                # If either type of pause is needed, emit a single APPROVAL_REQUIRED
                # event that carries both pending_approvals and pending_frontend_tool_calls.
                # The client checks which lists are populated and handles accordingly.
                if pending_approvals or pending_frontend_calls:
                    yield StreamMessage(
                        event=StreamEvents.APPROVAL_REQUIRED,
                        data={
                            "content": None,
                            "messages": messages,
                            "pending_approvals": [
                                approval.model_dump() for approval in pending_approvals
                            ],
                            "pending_frontend_tool_calls": [
                                fc.model_dump() for fc in pending_frontend_calls
                            ],
                            "num_llm_calls": i,
                            "costs": stats.model_dump(),
                        },
                    )
                    return

                # Update the tool number offset for the next iteration
                tool_number_offset += len(tools_to_call)

                # Re-fetch tools if the tool list changed (runbook activation, OAuth tool discovery, etc.)
                if tools is not None:
                    new_tools = self._get_tools()
                    old_names = {t["function"]["name"] for t in tools}
                    new_names = {t["function"]["name"] for t in new_tools}
                    if old_names != new_names:
                        logging.warning(
                            f"Tool list changed - refreshing ({len(tools)} -> {len(new_tools)} tools)"
                        )
                        tools = new_tools

        raise Exception(
            f"Too many LLM calls - exceeded max_steps: {i}/{self.max_steps}"
        )

    def find_assistant_tool_call_request(
        self, tool_call_id: str, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        for message in messages:
            if message.get("role") == "assistant":
                for tool_call in message.get("tool_calls", []):
                    if tool_call.get("id") == tool_call_id:
                        return tool_call

        # Should not happen unless there is a bug.
        # If we are here
        raise Exception(
            f"Failed to find assistant request for a tool_call in conversation history. tool_call_id={tool_call_id}"
        )
