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
import uvicorn
import colorlog

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

    # Hijack the existing HolmesGPT cat stream output and format as AG-UI events.

    async def event_generator(message_history):
        try:
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
                if hasattr(chunk, "data"):
                    tool_name = chunk.data.get(
                        "tool_name", chunk.data.get("name", "Tool")
                    )
                    if event_type in (
                        StreamEvents.AI_MESSAGE,
                        StreamEvents.ANSWER_END,
                        "unknown",
                    ):
                        async for event in _stream_agui_text_message_event(
                            message=str(chunk.data.get("content", ""))
                        ):
                            yield encoder.encode(event)
                    elif event_type == StreamEvents.START_TOOL:
                        async for event in _stream_agui_text_message_event(
                            message=f"🔧 Using Agent tool: `{tool_name}`..."
                        ):
                            yield encoder.encode(event)
                    elif event_type == StreamEvents.TOOL_RESULT:
                        logging.debug(
                            f"🔧 TOOL_RESULT received - tool_name: {tool_name}"
                        )
                        front_end_tool_invoked = False
                        if _should_graph_timeseries_data(tool_name=tool_name):
                            front_end_tool_invoked = True
                            logging.debug(
                                f"🔧 Should graph timeseries data for tool: {tool_name}"
                            )
                            ts_data = _parse_timeseries_data(chunk.data)
                            tool_call_id = chunk.data.get(
                                "tool_call_id", chunk.data.get("id", "unknown")
                            )
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
                            tool_call_id = chunk.data.get(
                                "tool_call_id", chunk.data.get("id", "unknown")
                            )
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
            yield encoder.encode(
                RunFinishedEvent(
                    type=EventType.RUN_FINISHED,
                    thread_id=input_data.thread_id,
                    run_id=input_data.run_id,
                )
            )
        except Exception as e:
            logging.error(f"Error in /api/agui/chat: {e}", exc_info=True)
            yield encoder.encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Agent encountered an error: {str(e)}",
                )
            )

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
