# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE

# Safe to import networked libs below
import json
import logging
import time
import uuid
from typing import Any

import uvicorn
import colorlog

# OTEL tracing imports (optional - gracefully degrade if not available)
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

try:
    from opentelemetry import trace
    from holmes.core.tracing import TracingFactory
    from experimental.otel.tracing import get_tracer, set_span_error
    from experimental.otel import attributes as otel_attr
    from experimental.otel.metrics import (
        init_otel_metrics,
        record_token_usage,
        record_operation_duration,
        record_tool_duration,
        increment_iterations,
        increment_tool_calls,
        increment_errors,
    )

    _otel_available = True
except ImportError:
    _otel_available = False
    TracingFactory = None  # type: ignore
    get_tracer = None  # type: ignore

    def init_otel_metrics():  # type: ignore
        return False

    def record_token_usage(**kwargs):  # type: ignore
        pass

    def record_operation_duration(**kwargs):  # type: ignore
        pass

    def record_tool_duration(**kwargs):  # type: ignore
        pass

    def increment_iterations(**kwargs):  # type: ignore
        pass

    def increment_tool_calls(**kwargs):  # type: ignore
        pass

    def increment_errors(**kwargs):  # type: ignore
        pass

    # No-op stubs so event_generator code doesn't need conditionals everywhere
    class _NoopSpan:
        def set_attribute(self, *args, **kwargs):
            pass

        def end(self):
            pass

        def start_span(self, *args, **kwargs):
            return _NoopSpan()

    class _NoopTracer:
        def start_span(self, *args, **kwargs):
            return _NoopSpan()

    class _OtelAttrStub:
        """Provides attribute constants as empty strings when OTEL is unavailable."""

        def __getattr__(self, name):
            return ""

        @staticmethod
        def truncate(value):
            if value is None:
                return ""
            return str(value)[:8192]

    otel_attr = _OtelAttrStub()  # type: ignore

    class _TraceStub:
        @staticmethod
        def set_span_in_context(span):
            return None

    trace = _TraceStub()  # type: ignore

    def set_span_error(span, error):  # type: ignore
        pass


from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.responses import PlainTextResponse

from holmes.utils.stream import StreamMessage, StreamEvents
from holmes.common.env_vars import (
    HOLMES_HOST,
    HOLMES_PORT,
)
from holmes.config import Config
from holmes.core.conversations import (
    build_chat_messages,
)
from holmes.core.models import (
    ChatRequest,
)

from ag_ui.core import (
    AssistantMessage,
    RunAgentInput,
    EventType,
    RunStartedEvent,
    RunFinishedEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    RunErrorEvent,
)
from ag_ui.encoder import EventEncoder


def init_logging():
    logging_level = os.environ.get("LOG_LEVEL", "INFO")
    logging_format = "%(log_color)s%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s"
    logging_datefmt = "%Y-%m-%d %H:%M:%S"

    print("setting up colored logging")
    colorlog.basicConfig(
        format=logging_format, level=logging_level, datefmt=logging_datefmt
    )
    logging.getLogger().setLevel(logging_level)

    httpx_logger = logging.getLogger("httpx")
    if httpx_logger:
        httpx_logger.setLevel(logging.WARNING)

    logging.info(f"logger initialized using {logging_level} log level")


init_logging()

# Initialize OTEL tracer if enabled (using unified TracingFactory)
_otel_enabled = False
if _otel_available and os.environ.get("OTEL_SDK_DISABLED", "true").lower() != "true":
    otel_initialized = TracingFactory.init_otel()
    if otel_initialized:
        logging.info("OTEL tracing enabled for AG-UI endpoint")
        _otel_enabled = True
    tracer = get_tracer("holmesgpt.agui")

    # Initialize OTEL metrics if enabled
    metrics_initialized = init_otel_metrics()
    if metrics_initialized:
        logging.info("OTEL metrics enabled for AG-UI endpoint")
else:
    tracer = _NoopTracer()  # type: ignore

config = Config.load_from_env()
dal = config.dal

app = FastAPI()

# Add CORS middleware front-end access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/agui/chat/health")
def agui_chat_health(request: Request):
    return JSONResponse(content="ok")


@app.post("/api/agui/chat")
def agui_chat(input_data: RunAgentInput, request: Request):
    accept_header = request.headers.get("accept", "")
    encoder = EventEncoder(accept=accept_header)

    logging.debug(f"AG-UI context: {input_data.context}")
    logging.debug(f"AG-UI state: {input_data.state}")
    # Ignore front-end tool result messages. Not supported for now. Use chat history/context instead.
    if _is_tool_result_message(input_data):
        return PlainTextResponse("OK", status_code=200)

    chat_request = _agui_input_to_holmes_chat_request(input_data=input_data)
    if not chat_request.ask:
        return PlainTextResponse(
            "Bad request. Chat message cannot be empty", status_code=400
        )

    ai = config.create_agui_toolcalling_llm(dal=dal, model=chat_request.model)
    global_instructions = dal.get_global_instructions_for_account()
    messages = build_chat_messages(
        chat_request.ask,
        chat_request.conversation_history,
        ai=ai,
        config=config,
        global_instructions=global_instructions,
        additional_system_prompt=chat_request.additional_system_prompt,
    )

    # Hijack the existing HolmesGPT chat stream output and format as AG-UI events.

    async def event_generator(message_history):
        # Start OTEL root span for this agent run - must be inside generator for streaming
        # Following Gen AI semantic conventions: "invoke_agent {agent_name}"
        model_name = chat_request.model or "default"
        operation_start_time = time.time()  # Track for operation duration metric
        root_span = tracer.start_span(f"{otel_attr.SPAN_INVOKE_AGENT} HolmesGPT")
        try:
            # Set correlation attributes for linking traces
            root_span.set_attribute(otel_attr.REQUEST_ID, input_data.run_id or "")
            root_span.set_attribute(
                otel_attr.CONVERSATION_ID, input_data.thread_id or ""
            )
            root_span.set_attribute(otel_attr.OPERATION_NAME, "invoke_agent")
            root_span.set_attribute(otel_attr.AGENT_NAME, "HolmesGPT")
            root_span.set_attribute(otel_attr.MODEL, model_name)

            logging.info(
                f"[AG-UI] Starting agent run for run_id={input_data.run_id}, sending RUN_STARTED"
            )
            yield encoder.encode(
                RunStartedEvent(
                    type=EventType.RUN_STARTED,
                    thread_id=input_data.thread_id,
                    run_id=input_data.run_id,
                )
            )
            hgpt_chat_stream_response: StreamMessage = ai.call_stream(
                msgs=message_history,
                enable_tool_approval=chat_request.enable_tool_approval or False,
            )
            tool_call_count = 0
            tool_start_times: dict[str, float] = {}  # Float timestamps only
            active_spans: dict[str, Any] = {}  # Span objects for cleanup

            # Track current LLM iteration span for proper span hierarchy
            current_chat_span = None
            total_input_tokens = 0
            total_output_tokens = 0
            total_cost_usd = 0.0
            iteration_count = 0

            for chunk in hgpt_chat_stream_response:
                if hasattr(chunk, "event"):
                    event_type = (
                        chunk.event.value
                        if hasattr(chunk.event, "value")
                        else str(chunk.event)
                    )
                    logging.debug(f"Streaming chunk: {event_type}")
                else:
                    event_type = "unknown"
                    logging.debug(f"Streaming chunk: {chunk}")
                # Handle LLM iteration events for proper span hierarchy
                # NOTE: event_type is a string (e.g., "llm_iteration_start"), so compare with .value
                if event_type == StreamEvents.LLM_ITERATION_START.value:
                    # End any existing chat span before starting new one
                    if current_chat_span is not None:
                        current_chat_span.end()

                    # Create new chat span as child of root span
                    # Following Gen AI semantic conventions: "chat {model}"
                    iteration_model = (
                        chunk.data.get("model", model_name)
                        if hasattr(chunk, "data")
                        else model_name
                    )
                    current_chat_span = tracer.start_span(
                        f"{otel_attr.SPAN_CHAT} {iteration_model}",
                        context=trace.set_span_in_context(root_span),
                    )
                    current_iteration = (
                        chunk.data.get("iteration", 0) if hasattr(chunk, "data") else 0
                    )
                    current_chat_span.set_attribute(otel_attr.OPERATION_NAME, "chat")
                    current_chat_span.set_attribute(otel_attr.MODEL, iteration_model)
                    current_chat_span.set_attribute(
                        otel_attr.AGENT_ITERATION, current_iteration
                    )
                    # Propagate correlation attributes to child spans for querying
                    current_chat_span.set_attribute(
                        otel_attr.REQUEST_ID, input_data.run_id or ""
                    )
                    current_chat_span.set_attribute(
                        otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                    )
                    iteration_count = current_iteration
                    continue

                elif event_type == StreamEvents.LLM_ITERATION_COMPLETE.value:
                    if current_chat_span is not None and hasattr(chunk, "data"):
                        # Set token usage and cost attributes on chat span
                        prompt_tokens = chunk.data.get("prompt_tokens", 0)
                        completion_tokens = chunk.data.get("completion_tokens", 0)
                        total_tokens = chunk.data.get("total_tokens", 0)
                        cost_usd = chunk.data.get("cost_usd", 0.0)
                        finish_reason = chunk.data.get("finish_reason")
                        iteration_model = chunk.data.get("model", model_name)

                        current_chat_span.set_attribute(
                            otel_attr.INPUT_TOKENS, prompt_tokens
                        )
                        current_chat_span.set_attribute(
                            otel_attr.OUTPUT_TOKENS, completion_tokens
                        )
                        current_chat_span.set_attribute(
                            otel_attr.TOTAL_TOKENS, total_tokens
                        )
                        if cost_usd:
                            current_chat_span.set_attribute(
                                otel_attr.COST_USD, cost_usd
                            )
                        if finish_reason:
                            current_chat_span.set_attribute(
                                otel_attr.FINISH_REASON, finish_reason
                            )

                        # Record token usage metrics
                        record_token_usage(
                            tokens=prompt_tokens,
                            token_type=otel_attr.TOKEN_TYPE_INPUT,
                            model=iteration_model,
                            operation_name="chat",
                        )
                        record_token_usage(
                            tokens=completion_tokens,
                            token_type=otel_attr.TOKEN_TYPE_OUTPUT,
                            model=iteration_model,
                            operation_name="chat",
                        )

                        # Accumulate totals for root span
                        total_input_tokens += prompt_tokens
                        total_output_tokens += completion_tokens
                        total_cost_usd += cost_usd or 0.0
                    continue

                # Handle granular tracing events
                elif event_type == StreamEvents.TOOL_INVOKE_START.value:
                    # Create granular span for tool invocation
                    if hasattr(chunk, "data"):
                        tool_name = chunk.data.get("tool_name", "unknown")
                        tool_call_id = chunk.data.get("tool_call_id", "unknown")
                        parent_span = (
                            current_chat_span if current_chat_span else root_span
                        )
                        invoke_span = tracer.start_span(
                            f"{otel_attr.SPAN_INVOKE_TOOL} {tool_name}",
                            context=trace.set_span_in_context(parent_span),
                        )
                        invoke_span.set_attribute(otel_attr.TOOL_NAME, tool_name)
                        invoke_span.set_attribute(otel_attr.TOOL_CALL_ID, tool_call_id)
                        # Propagate correlation attributes
                        invoke_span.set_attribute(
                            otel_attr.REQUEST_ID, input_data.run_id or ""
                        )
                        invoke_span.set_attribute(
                            otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                        )
                        if chunk.data.get("tool_arguments"):
                            invoke_span.set_attribute(
                                otel_attr.TOOL_INPUT,
                                otel_attr.truncate(chunk.data.get("tool_arguments")),
                            )
                        # Store span for later end
                        active_spans[f"invoke_{tool_call_id}"] = invoke_span
                    continue

                elif event_type == StreamEvents.TOOL_INVOKE_END.value:
                    if hasattr(chunk, "data"):
                        tool_call_id = chunk.data.get("tool_call_id", "unknown")
                        invoke_span = active_spans.pop(f"invoke_{tool_call_id}", None)
                        if invoke_span:
                            invoke_span.set_attribute(
                                otel_attr.TOOL_STATUS,
                                chunk.data.get("status", "UNKNOWN"),
                            )
                            if chunk.data.get("result"):
                                invoke_span.set_attribute(
                                    otel_attr.TOOL_OUTPUT,
                                    otel_attr.truncate(chunk.data.get("result")),
                                )
                            if chunk.data.get("error"):
                                invoke_span.set_attribute(
                                    otel_attr.ERROR_MESSAGE, chunk.data.get("error")
                                )
                            invoke_span.end()
                    continue

                elif event_type == StreamEvents.PARSE_RESPONSE_START.value:
                    # Create span for response parsing
                    parent_span = current_chat_span if current_chat_span else root_span
                    parse_span = tracer.start_span(
                        otel_attr.SPAN_PARSE_RESPONSE,
                        context=trace.set_span_in_context(parent_span),
                    )
                    # Propagate correlation attributes
                    parse_span.set_attribute(
                        otel_attr.REQUEST_ID, input_data.run_id or ""
                    )
                    parse_span.set_attribute(
                        otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                    )
                    active_spans["parse_response"] = parse_span
                    continue

                elif event_type == StreamEvents.PARSE_RESPONSE_END.value:
                    parse_span = active_spans.pop("parse_response", None)
                    if parse_span and hasattr(chunk, "data"):
                        if chunk.data.get("tool_call_count") is not None:
                            parse_span.set_attribute(
                                otel_attr.TOOL_CALL_COUNT,
                                chunk.data.get("tool_call_count"),
                            )
                        if chunk.data.get("finish_reason"):
                            parse_span.set_attribute(
                                otel_attr.FINISH_REASON, chunk.data.get("finish_reason")
                            )
                        parse_span.end()
                    continue

                elif event_type == StreamEvents.CONTEXT_CHECK_START.value:
                    # Create span for context limit checking
                    context_span = tracer.start_span(
                        otel_attr.SPAN_CHECK_CONTEXT_LIMITS,
                        context=trace.set_span_in_context(root_span),
                    )
                    # Propagate correlation attributes
                    context_span.set_attribute(
                        otel_attr.REQUEST_ID, input_data.run_id or ""
                    )
                    context_span.set_attribute(
                        otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                    )
                    active_spans["context_check"] = context_span
                    continue

                elif event_type == StreamEvents.CONTEXT_CHECK_END.value:
                    context_span = active_spans.pop("context_check", None)
                    if context_span and hasattr(chunk, "data"):
                        if chunk.data.get("tokens_used") is not None:
                            context_span.set_attribute(
                                otel_attr.CONTEXT_TOKENS_USED,
                                chunk.data.get("tokens_used"),
                            )
                        if chunk.data.get("tokens_limit") is not None:
                            context_span.set_attribute(
                                otel_attr.CONTEXT_TOKENS_LIMIT,
                                chunk.data.get("tokens_limit"),
                            )
                        if chunk.data.get("compaction_needed"):
                            context_span.set_attribute(
                                "compaction_needed", chunk.data.get("compaction_needed")
                            )
                        context_span.end()
                    continue

                elif event_type == StreamEvents.ERROR_HANDLING_START.value:
                    # Create span for error handling
                    parent_span = current_chat_span if current_chat_span else root_span
                    error_span = tracer.start_span(
                        otel_attr.SPAN_HANDLE_LLM_ERROR,
                        context=trace.set_span_in_context(parent_span),
                    )
                    # Propagate correlation attributes
                    error_span.set_attribute(
                        otel_attr.REQUEST_ID, input_data.run_id or ""
                    )
                    error_span.set_attribute(
                        otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                    )
                    if hasattr(chunk, "data"):
                        if chunk.data.get("error_type"):
                            error_span.set_attribute(
                                otel_attr.ERROR_TYPE, chunk.data.get("error_type")
                            )
                        if chunk.data.get("error_message"):
                            error_span.set_attribute(
                                otel_attr.ERROR_MESSAGE, chunk.data.get("error_message")
                            )
                        if chunk.data.get("will_retry") is not None:
                            error_span.set_attribute(
                                "will_retry", chunk.data.get("will_retry")
                            )
                    active_spans["error_handling"] = error_span
                    continue

                elif event_type == StreamEvents.ERROR_HANDLING_END.value:
                    error_span = active_spans.pop("error_handling", None)
                    if error_span:
                        error_span.end()
                    continue

                if hasattr(chunk, "data"):
                    tool_name = chunk.data.get(
                        "tool_name", chunk.data.get("name", "Tool")
                    )
                    if event_type in (
                        StreamEvents.AI_MESSAGE.value,
                        StreamEvents.ANSWER_END.value,
                        "unknown",
                    ):
                        async for event in _stream_agui_text_message_event(
                            message=str(chunk.data.get("content", ""))
                        ):
                            yield encoder.encode(event)
                    elif event_type == StreamEvents.START_TOOL.value:
                        # Record tool start time for duration calculation
                        tool_call_count += 1
                        tool_call_id = chunk.data.get(
                            "tool_call_id", chunk.data.get("id", "unknown")
                        )
                        tool_start_times[tool_call_id] = time.time()
                        async for event in _stream_agui_text_message_event(
                            message=f"🔧 Using Agent tool: `{tool_name}`..."
                        ):
                            yield encoder.encode(event)
                    elif event_type == StreamEvents.TOOL_RESULT.value:
                        logging.debug(
                            f"🔧 TOOL_RESULT received - tool_name: {tool_name}"
                        )
                        # Create OTEL child span for tool execution
                        # Parent is current chat span if available, otherwise root span
                        tool_call_id = chunk.data.get(
                            "tool_call_id", chunk.data.get("id", "unknown")
                        )
                        # Calculate duration from tracked start time
                        start_time = tool_start_times.pop(tool_call_id, None)
                        duration_ms = (
                            int((time.time() - start_time) * 1000) if start_time else 0
                        )

                        # Tool spans are children of the current chat span (proper hierarchy)
                        parent_span = (
                            current_chat_span if current_chat_span else root_span
                        )
                        tool_span = tracer.start_span(
                            f"{otel_attr.SPAN_EXECUTE_TOOL} {tool_name}",
                            context=trace.set_span_in_context(parent_span),
                        )
                        tool_span.set_attribute(
                            otel_attr.OPERATION_NAME, "execute_tool"
                        )
                        tool_span.set_attribute(otel_attr.TOOL_NAME, tool_name)
                        tool_span.set_attribute(otel_attr.TOOL_CALL_ID, tool_call_id)
                        tool_span.set_attribute(otel_attr.TOOL_DURATION_MS, duration_ms)
                        tool_span.set_attribute(
                            otel_attr.TOOL_OUTPUT,
                            otel_attr.truncate(str(chunk.data.get("result", {}))),
                        )
                        # Propagate correlation attributes
                        tool_span.set_attribute(
                            otel_attr.REQUEST_ID, input_data.run_id or ""
                        )
                        tool_span.set_attribute(
                            otel_attr.CONVERSATION_ID, input_data.thread_id or ""
                        )
                        tool_span.end()

                        # Record tool execution metrics
                        record_tool_duration(
                            duration_seconds=duration_ms / 1000.0,
                            tool_name=tool_name,
                            success=True,
                        )
                        increment_tool_calls(
                            count=1, tool_name=tool_name, model=model_name
                        )

                        front_end_tool_invoked = False
                        if _should_graph_timeseries_data(tool_name=tool_name):
                            front_end_tool_invoked = True
                            logging.debug(
                                f"🔧 Should graph timeseries data for tool: {tool_name}"
                            )
                            ts_data = _parse_timeseries_data(chunk.data)
                            # TODO [FUTURE]: Automate front-end tools discovery and let LLM decide which to invoke.
                            async for tool_event in _invoke_front_end_tool(
                                tool_call_id=tool_call_id,
                                tool_call_name="graph_timeseries_data",
                                tool_call_args=ts_data,
                            ):
                                yield encoder.encode(tool_event)
                        if _should_execute_suggested_query(
                            backend_tool_name=tool_name, frontend_tools=input_data.tools
                        ):
                            front_end_tool_invoked = True
                            front_end_query_tool = None
                            if tool_name == "opensearch_ppl_query_assist":
                                front_end_query_tool = "execute_ppl_query"
                            elif tool_name in (
                                "execute_prometheus_range_query",
                                "execute_prometheus_instant_query",
                            ):
                                front_end_query_tool = "execute_promql_query"

                            async for tool_event in _invoke_front_end_tool(
                                tool_call_id=tool_call_id,
                                tool_call_name=front_end_query_tool,
                                tool_call_args={"query": _parse_query(chunk.data)},
                            ):
                                yield encoder.encode(tool_event)
                        if not front_end_tool_invoked:
                            # TODO [FUTURE]: Render "TodoWrite" tool_name results prettier. Use code block for now.
                            #                 Ideally using TOOL_STEP events.
                            if tool_name == "TodoWrite":
                                tool_message = _format_todo_write(data=chunk.data)
                            else:
                                tool_message = f"🔧 {tool_name} result:\n{chunk.data.get('result', {}).get('data', '')[0:200]}..."

                            async for event in _stream_agui_text_message_event(
                                message=tool_message
                            ):
                                yield encoder.encode(event)

            # End the last chat span if still open
            if current_chat_span is not None:
                current_chat_span.end()

            # Calculate total operation duration
            operation_duration = time.time() - operation_start_time

            # Set final attributes on root span with accumulated metrics
            root_span.set_attribute(otel_attr.TOOL_CALL_COUNT, tool_call_count)
            root_span.set_attribute(otel_attr.AGENT_ITERATION, iteration_count)
            root_span.set_attribute(otel_attr.INPUT_TOKENS, total_input_tokens)
            root_span.set_attribute(otel_attr.OUTPUT_TOKENS, total_output_tokens)
            root_span.set_attribute(
                otel_attr.TOTAL_TOKENS, total_input_tokens + total_output_tokens
            )
            if total_cost_usd > 0:
                root_span.set_attribute(otel_attr.COST_USD, total_cost_usd)
            root_span.set_attribute(otel_attr.RESULT_SUCCESS, True)

            # Record final metrics
            record_operation_duration(
                duration_seconds=operation_duration,
                operation_name="invoke_agent",
                model=model_name,
                agent_name="HolmesGPT",
                success=True,
            )
            increment_iterations(
                count=iteration_count, model=model_name, agent_name="HolmesGPT"
            )

            logging.info(
                f"[AG-UI] Stream completed for run_id={input_data.run_id}, sending RUN_FINISHED"
            )
            yield encoder.encode(
                RunFinishedEvent(
                    type=EventType.RUN_FINISHED,
                    thread_id=input_data.thread_id,
                    run_id=input_data.run_id,
                )
            )
        except Exception as e:
            logging.error(f"[AG-UI] Error in /api/agui/chat: {e}", exc_info=True)
            # End the chat span if still open on error
            if current_chat_span is not None:
                set_span_error(current_chat_span, e)
                current_chat_span.end()

            # Clean up any unclosed spans to prevent span leaks
            for key, span in list(active_spans.items()):
                try:
                    set_span_error(span, e)
                    span.end()
                except Exception:
                    pass  # Best effort cleanup
            active_spans.clear()

            set_span_error(root_span, e)

            # Record error metrics
            error_type = type(e).__name__
            increment_errors(
                count=1, error_type=error_type, operation_name="invoke_agent"
            )
            operation_duration = time.time() - operation_start_time
            record_operation_duration(
                duration_seconds=operation_duration,
                operation_name="invoke_agent",
                model=model_name,
                agent_name="HolmesGPT",
                success=False,
            )

            logging.info(f"[AG-UI] Sending RUN_ERROR for run_id={input_data.run_id}")
            yield encoder.encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Agent encountered an error: {str(e)}",
                )
            )
        finally:
            root_span.end()

    return StreamingResponse(
        event_generator(messages), media_type=encoder.get_content_type()
    )


def _format_todo_write(data) -> str:
    status_icons = {"pending": "⬜", "in_progress": "⏳", "completed": "✅"}
    result_data = data.get("result", {})
    params = result_data.get("params", {})
    todos = params.get("todos", {})
    output_str = "### Investigation Tasks:  \n"
    task_list = []
    for idx, todo in enumerate(todos):
        status = todo.get("status", "")
        icon = status_icons.get(status, "⬜")
        content = todo.get("content", "")
        task_list.append(f"{idx+1}. {icon} - {content}")
    output_str += "  \n".join(task for task in task_list)
    return output_str


def _should_execute_suggested_query(
    backend_tool_name: str, frontend_tools: list
) -> bool:
    for fe_tool in frontend_tools:
        if "execute_prom" in fe_tool.name and backend_tool_name in (
            "execute_prometheus_range_query",
            "execute_prometheus_instant_query",
        ):
            return True
        elif (
            "execute_ppl" in fe_tool.name
            and backend_tool_name == "opensearch_ppl_query_assist"
        ):
            return True
    return False


def _parse_query(data) -> str:
    result_data = data.get("result", {})
    params = result_data.get("params", {})
    query = params.get("query", "")
    return query


def _should_graph_timeseries_data(tool_name: str) -> bool:
    # Only support prometheus timeseries data for now.
    return tool_name in (
        "execute_prometheus_range_query",
        "execute_prometheus_instant_query",
    )


def _parse_timeseries_data(data) -> dict:
    try:
        logging.debug(f"🔍 _parse_timeseries_data received data: {data}")
        logging.debug(f"🔍 Data type: {type(data)}")
        logging.debug(
            f"🔍 Data keys: {list(data.keys()) if hasattr(data, 'keys') else 'No keys'}"
        )

        # Extract the result from chunk.data
        result_data = data.get("result", {})
        params = result_data.get("params", {})
        query = params.get("query", "")
        description = params.get("description")
        tool_name = data.get("tool_name", data.get("name", ""))

        logging.debug(f"🔍 Extracted - result_data: {result_data}")
        logging.debug(f"🔍 Extracted - query: {query}")
        logging.debug(f"🔍 Extracted - tool_name: {tool_name}")

        # If result is a JSON string, parse it
        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
                logging.debug(f"🔍 Parsed JSON result_data: {result_data}")
            except json.JSONDecodeError:
                logging.warning(f"Failed to parse result as JSON: {result_data}")
                result_data = {}

        # Handle different Prometheus response formats
        prometheus_data = result_data
        result_type = "unknown"
        if "data" in result_data:
            prometheus_data = json.loads(result_data["data"]).get("data")
            result_type = prometheus_data.get("resultType", "unknown")

        # Prepare metadata
        metadata = {
            "timestamp": int(time.time()),
            "source": "Prometheus",
            "result_type": result_type,
            "description": description,
            "query": query,
        }

        return {
            "title": description,
            "query": query,
            "data": prometheus_data,
            "metadata": metadata,
        }

    except Exception as e:
        logging.error(f"Error parsing timeseries data: {e}", exc_info=True)
        # Return a fallback structure
        return {
            "title": "Prometheus Query Results (Parse Error)",
            "query": data.get("query", ""),
            "data": {"result": []},
            "metadata": {
                "timestamp": int(time.time()),
                "source": "Prometheus",
                "error": str(e),
            },
        }


async def _invoke_front_end_tool(
    tool_call_id: str, tool_call_name: str, tool_call_args: dict
):
    yield ToolCallStartEvent(
        type=EventType.TOOL_CALL_START,
        tool_call_id=tool_call_id,
        tool_call_name=tool_call_name,
    )
    yield ToolCallArgsEvent(
        type=EventType.TOOL_CALL_ARGS,
        tool_call_id=tool_call_id,
        delta=json.dumps(tool_call_args),
    )
    yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id)


async def _stream_agui_text_message_event(message: str):
    message_id = str(uuid.uuid4())
    yield TextMessageStartEvent(
        type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"
    )
    yield TextMessageContentEvent(
        type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=message
    )
    yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id)


def _is_tool_result_message(input_data: RunAgentInput) -> bool:
    return len(input_data.messages) > 0 and input_data.messages[-1].role == "tool"


def _agui_input_to_holmes_chat_request(input_data: RunAgentInput) -> ChatRequest:
    # Convert AG-UI input to HolmesGPT ChatRequest format
    non_system_messages = []
    # IMPORTANT: Do not support front-end "tool" messages for now. Store them as assistant messages in conv history.
    # Requires full integration with tools. Claude will complain about "toolResult" missing corresponding "toolUse" msg.
    # E.g. `The number of toolResult blocks at messages.2.content exceeds the number of toolUse blocks of previous turn`
    for msg in input_data.messages:
        if msg.role in ("user", "assistant"):
            non_system_messages.append(msg)
        elif msg.role == "tool":
            non_system_messages.append(AssistantMessage(content=msg.content, id=msg.id))
    conversation_history = [
        {
            "role": "system",
            "content": "You are Holmes, an AI assistant for observability. You use Prometheus metrics, alerts and OpenSearch logs to quickly perform root cause analysis.",
        }
    ]
    if len(non_system_messages) > 1:
        conversation_history.extend(
            [
                {
                    "role": msg.role,
                    "content": msg.content.strip() if msg.content else "",
                }
                for msg in non_system_messages[:-1]
            ]
        )

    # Get the last user message and validate it
    last_user_message = ""
    if non_system_messages and non_system_messages[-1].role == "user":
        last_user_message = (
            non_system_messages[-1].content.strip()
            if non_system_messages[-1].content
            else ""
        )

    if input_data.context:
        # insert page context at 2nd to last entry (behind latest user message).
        # page context might change. Don't want it to get buried in past messages.
        conversation_history.insert(
            -1,
            {
                "role": "system",
                "content": f"The user has the following information in their current web page for which you are assisting them. {input_data.context}",
            },
        )

    chat_request = ChatRequest(
        ask=last_user_message,
        conversation_history=conversation_history,
        model=getattr(input_data, "model", None),
        stream=True,
    )
    return chat_request


@app.get("/api/model")
def get_model():
    return {"model_name": config.get_models_list()}


if __name__ == "__main__":
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )
    log_config["formatters"]["default"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )
    uvicorn.run(
        app, host=HOLMES_HOST, port=HOLMES_PORT, log_config=log_config, reload=False
    )
