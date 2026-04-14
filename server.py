# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import colorlog
import litellm
import sentry_sdk
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from litellm.exceptions import AuthenticationError

from holmes import get_version, is_official_release
from holmes.common.env_vars import (
    DEVELOPMENT_MODE,
    ENABLE_CONNECTION_KEEPALIVE,
    ENABLE_TELEMETRY,
    ENABLED_SCHEDULED_PROMPTS,
    HOLMES_HOST,
    HOLMES_PORT,
    LOG_PERFORMANCE,
    MCP_RETRY_BACKOFF_SCHEDULE,
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
    TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS,
)
from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core.conversations import (
    build_chat_messages,
)
from holmes.core.models import (
    ChatRequest,
    ChatResponse,
    FollowUpAction,
)
from holmes.core.prompt import PromptComponent
from holmes.core.tools import ToolsetStatusEnum, ToolsetType
from holmes.core.scheduled_prompts import ScheduledPromptsExecutor
from holmes.utils.connection_utils import patch_socket_create_connection
from holmes.utils.holmes_status import update_holmes_status_in_db
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
from holmes.utils.log import EndpointFilter
from holmes.checks.checks_api import init_checks_app
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.utils.stream import stream_chat_formatter

# removed: add_runbooks_to_user_prompt


def _save_investigation(
    investigation_id: str,
    started_at: str,
    question: str,
    project_id: str,
    answer: str,
    tool_calls: list,
    status: str,
    error: str = "",
    metadata: Optional[dict] = None,
) -> None:
    """Persist a completed investigation to DynamoDB (best-effort, never raises)."""
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        import sys as _sys
        import os as _os

        _frontend_dir = _os.path.join(_os.path.dirname(__file__), "frontend")
        if _frontend_dir not in _sys.path:
            _sys.path.insert(0, _frontend_dir)
        from projects import get_investigation_store, Investigation, ToolCallRecord  # type: ignore  # noqa: PLC0415

        from datetime import datetime, timezone

        inv = Investigation(
            id=investigation_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            trigger="manual",
            source="ui",
            question=question,
            answer=answer,
            tool_calls=[ToolCallRecord(**tc) for tc in tool_calls],
            project_id=project_id or "",
            status=status,
            error=error,
            metadata=metadata or {},
        )
        get_investigation_store().save(inv)
        logging.debug("Saved investigation %s to DynamoDB", investigation_id)
    except Exception:
        logging.warning("Failed to save investigation to DynamoDB", exc_info=True)


def _investigation_tracking_stream(
    call_stream,
    investigation_id: str,
    started_at: str,
    question: str,
    project_id: str,
    initial_metadata: Optional[dict] = None,
):
    """
    Wrap call_stream to intercept StreamMessage events and accumulate the tool
    call trace and final answer, then persist the investigation to DynamoDB.

    Passes every StreamMessage through unchanged so stream_chat_formatter sees
    the same events it always did.
    """
    from holmes.utils.stream import StreamEvents  # noqa: PLC0415

    tool_calls: list = []
    final_answer: str = ""
    status: str = "completed"
    error_msg: str = ""
    investigation_metadata: dict = dict(initial_metadata or {})

    try:
        for message in call_stream:
            # Accumulate tool call records
            if message.event == StreamEvents.TOOL_RESULT:
                try:
                    from datetime import datetime, timezone as _tz  # noqa: PLC0415

                    tool_calls.append(
                        {
                            "tool_name": message.data.get("name", ""),
                            "tool_input": {},  # not exposed in streaming data
                            "tool_output": (message.data.get("result") or {}).get(
                                "data", ""
                            ),
                            "called_at": datetime.now(_tz.utc).isoformat(),
                        }
                    )
                except Exception:
                    pass  # never block the stream

            # Capture the final answer
            elif message.event == StreamEvents.ANSWER_END:
                final_answer = message.data.get("content", "")

            elif message.event == StreamEvents.TOKEN_COUNT:
                try:
                    meta = message.data.get("metadata", {})
                    usage = meta.get("usage", {})
                    if usage:
                        investigation_metadata.update(
                            {
                                "prompt_tokens": usage.get("prompt_tokens", 0),
                                "completion_tokens": usage.get("completion_tokens", 0),
                                "total_tokens": usage.get("total_tokens", 0),
                            }
                        )
                except Exception:
                    pass

            yield message

    except Exception as exc:
        status = "failed"
        error_msg = str(exc)
        raise
    finally:
        _save_investigation(
            investigation_id=investigation_id,
            started_at=started_at,
            question=question,
            project_id=project_id,
            answer=final_answer,
            tool_calls=tool_calls,
            status=status,
            error=error_msg,
            metadata=investigation_metadata,
        )


def init_logging():
    # Filter out periodical healniss and readiness probe.
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.addFilter(EndpointFilter(path="/healthz"))
    uvicorn_logger.addFilter(EndpointFilter(path="/readyz"))

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

    litellm_logger = logging.getLogger("LiteLLM")
    if litellm_logger:
        litellm_logger.handlers = []

    logging.info(f"logger initialized using {logging_level} log level")


init_logging()

if ENABLE_CONNECTION_KEEPALIVE:
    patch_socket_create_connection()


def init_config():
    """
    Initialize configuration from file if it exists at the default location,
    otherwise load from environment variables.

    Returns:
        tuple: (config, dal) - The initialized Config object and its DAL instance
    """
    default_config_path = Path(DEFAULT_CONFIG_LOCATION)
    if default_config_path.exists():
        logging.info(f"Loading config from file: {default_config_path}")
        config = Config.load_from_file(default_config_path)
    else:
        logging.info("No config file found, loading from environment variables")
        config = Config.load_from_env()

    dal = config.dal
    return config, dal


config, dal = init_config()


def sync_before_server_start():
    if not dal.enabled:
        logging.info(
            "Skipping holmes status and toolsets synchronization - not connected to Robusta platform"
        )
        return
    try:
        update_holmes_status_in_db(dal, config)
    except Exception:
        logging.error("Failed to update holmes status", exc_info=True)
    try:
        holmes_sync_toolsets_status(dal, config)
    except Exception:
        logging.error("Failed to synchronise holmes toolsets", exc_info=True)
    if not ENABLED_SCHEDULED_PROMPTS:
        return
    # No need to check if dal is enabled again, done at the start of this function
    try:
        scheduled_prompts_executor.start()
    except Exception:
        logging.error("Failed to start scheduled prompts executor", exc_info=True)


def _has_failed_mcp_toolsets() -> bool:
    """Check if any MCP toolsets are in FAILED state."""
    executor = config._server_tool_executor
    if not executor:
        return False
    return any(
        t.type == ToolsetType.MCP and t.status == ToolsetStatusEnum.FAILED
        for t in executor.toolsets
    )


def _get_next_refresh_interval(
    has_failed_mcp: bool,
    backoff_index: int,
    default_interval: int,
) -> tuple[int, int]:
    """Determine the next sleep interval and updated backoff index.

    Returns (sleep_seconds, new_backoff_index).
    """
    if has_failed_mcp and backoff_index < len(MCP_RETRY_BACKOFF_SCHEDULE):
        return MCP_RETRY_BACKOFF_SCHEDULE[backoff_index], backoff_index + 1
    return default_interval, 0


def _toolset_status_refresh_loop():
    interval = TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS
    if interval <= 0:
        logging.info("Periodic toolset status refresh is disabled")
        return

    logging.info(
        f"Starting periodic toolset status refresh (interval: {interval} seconds)"
    )

    def refresh_loop():
        backoff_index = 0

        while True:
            # Use shorter intervals when MCP servers are failing
            sleep_time, backoff_index = _get_next_refresh_interval(
                _has_failed_mcp_toolsets(), backoff_index, interval
            )
            if sleep_time < interval:
                logging.info(
                    f"Failed MCP server(s) detected, retrying in {sleep_time} seconds"
                )

            time.sleep(sleep_time)
            try:
                changes = config.refresh_server_tool_executor(dal)
                if changes:
                    for toolset_name, old_status, new_status in changes:
                        logging.info(
                            f"Toolset '{toolset_name}' status changed: {old_status} -> {new_status}"
                        )
                    holmes_sync_toolsets_status(dal, config)
                else:
                    logging.debug(
                        "Periodic toolset status refresh: no changes detected"
                    )
            except Exception:
                logging.error(
                    "Error during periodic toolset status refresh", exc_info=True
                )

    thread = threading.Thread(target=refresh_loop, daemon=True, name="toolset-refresh")
    thread.start()


if ENABLE_TELEMETRY and SENTRY_DSN:
    # Initialize Sentry for official releases or when development mode is enabled
    if is_official_release() or DEVELOPMENT_MODE:
        environment = "production" if is_official_release() else "development"
        version = get_version()
        release = None if version.startswith("dev-") else version
        logging.info(f"Initializing sentry for {environment} environment...")

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            send_default_pii=False,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=0,
            environment=environment,
            release=release,
        )
        sentry_sdk.set_tags(
            {
                "account_id": dal.account_id,
                "cluster_name": config.cluster_name,
                "version": get_version(),
                "environment": environment,
            }
        )
    else:
        logging.info(
            "Skipping sentry initialization - not an official release and DEVELOPMENT_MODE not enabled"
        )

app = FastAPI(
    title="HolmesGPT API",
    description="AI-powered infrastructure investigation API",
    version="1.0.0",
    swagger_ui_parameters={"persistAuthorization": True},
)

if LOG_PERFORMANCE:

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            process_time = int((time.time() - start_time) * 1000)

            status_code = "unknown"
            if response:
                status_code = response.status_code
            logging.info(
                f"Request completed {request.method} {request.url.path} status={status_code} latency={process_time}ms"
            )


init_checks_app(app, config)


def already_answered(conversation_history: Optional[List[dict]]) -> bool:
    if conversation_history is None:
        return False

    for message in conversation_history:
        if message["role"] == "assistant":
            return True
    return False


def extract_passthrough_headers(request: Request) -> dict:
    """
    Extract pass-through headers from the request, excluding sensitive auth headers.
    These headers are forwarded to all toolset types (MCP, HTTP, YAML, Python) for authentication and context.

    The blocked headers can be configured via the HOLMES_PASSTHROUGH_BLOCKED_HEADERS
    environment variable (comma-separated list). Defaults to "authorization,cookie,set-cookie".

    Returns:
        dict: {"headers": {"X-Foo-Bar": "...", "ABC": "...", ...}}
    """
    # Get blocked headers from environment variable or use defaults
    blocked_headers_str = os.environ.get(
        "HOLMES_PASSTHROUGH_BLOCKED_HEADERS", "authorization,cookie,set-cookie"
    )
    blocked_headers = {
        h.strip().lower() for h in blocked_headers_str.split(",") if h.strip()
    }

    passthrough_headers = {}
    for header_name, header_value in request.headers.items():
        if header_name.lower() not in blocked_headers:
            # Preserve original case from request (no normalization)
            passthrough_headers[header_name] = header_value

    return {"headers": passthrough_headers} if passthrough_headers else {}


def _stream_with_keepalive(stream_generator, keepalive_interval: int = 30):
    """
    Wrap a stream generator to emit SSE keepalive comments while the generator
    is blocked (e.g. waiting for an LLM response or a slow tool call).

    The ALB idle timeout is 300 s. Without keepalives, a long LLM call with no
    bytes sent causes the ALB to close the connection with a 502. SSE comment
    lines (': keep-alive\\n\\n') are ignored by browsers and the Holmes frontend
    but reset the ALB idle timer.
    """
    import queue as _queue

    q: _queue.Queue = _queue.Queue()
    _SENTINEL = object()

    def _producer():
        try:
            for chunk in stream_generator:
                q.put(chunk)
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=_producer, daemon=True)
    t.start()

    while True:
        try:
            item = q.get(timeout=keepalive_interval)
        except _queue.Empty:
            # No chunk arrived within the interval — send a keepalive comment
            yield ": keep-alive\n\n"
            continue

        if item is _SENTINEL:
            break
        yield item


def _stream_with_storage_cleanup(storage, stream_generator, req_info):
    """Wrap a stream generator to clean up tool result files after streaming completes."""
    try:
        yield from _stream_with_keepalive(stream_generator)
    finally:
        logging.info(f"Stream request end: {req_info}")
        storage.__exit__(None, None, None)


@app.post("/api/chat")
def chat(chat_request: ChatRequest, http_request: Request):
    try:
        # Log incoming request details
        has_images = bool(chat_request.images)
        has_structured_output = bool(chat_request.response_format)
        req_info = f"/api/chat request: ask={chat_request.ask}"
        logging.info(
            f"Received: {req_info}, model={chat_request.model}, "
            f"images={has_images}, structured_output={has_structured_output}, "
            f"streaming={chat_request.stream}"
        )

        runbooks = config.get_runbook_catalog()

        prompt_component_overrides = None
        if chat_request.behavior_controls:
            logging.info(
                f"Applying behavior_controls: {chat_request.behavior_controls}"
            )
            prompt_component_overrides = {}
            for k, v in chat_request.behavior_controls.items():
                try:
                    prompt_component_overrides[PromptComponent(k.lower())] = v
                except ValueError:
                    logging.warning(f"Unknown behavior_controls key '{k}', ignoring")

        follow_up_actions = []
        if not already_answered(chat_request.conversation_history):
            follow_up_actions = [
                FollowUpAction(
                    id="logs",
                    action_label="Logs",
                    prompt="Show me the relevant logs",
                    pre_action_notification_text="Fetching relevant logs...",
                ),
                FollowUpAction(
                    id="graphs",
                    action_label="Graphs",
                    prompt="Show me the relevant graphs. Use prometheus and make sure you embed the results with `<< >>` to display a graph",
                    pre_action_notification_text="Drawing some graphs...",
                ),
                FollowUpAction(
                    id="articles",
                    action_label="Articles",
                    prompt="List the relevant runbooks and links used. Write a short summary for each",
                    pre_action_notification_text="Looking up and summarizing runbooks and links...",
                ),
            ]

        request_context = extract_passthrough_headers(http_request)

        storage = tool_result_storage()
        tool_results_dir = storage.__enter__()

        # Ensure frontend is on sys.path so we can import shared helpers
        import sys
        import os as _os
        import re as _re

        _frontend_dir = _os.path.join(_os.path.dirname(__file__), "frontend")
        if _frontend_dir not in sys.path:
            sys.path.insert(0, _frontend_dir)

        def _extract_source_from_ask(ask: str) -> str:
            """Extract source= value from ask string, e.g. 'source=azure_devops ...'"""
            m = _re.search(r"\bsource=([A-Za-z0-9_\-]+)", ask or "")
            return m.group(1) if m else ""

        def _make_scoped_ai(source: str):
            """Build a scoped ToolCallingLLM capped at MAX_TOOLS_PER_CALL tools."""
            try:
                from server_frontend import _create_scoped_toolcalling_llm  # type: ignore  # noqa: PLC0415

                return _create_scoped_toolcalling_llm(
                    config, source, model=chat_request.model
                )
            except Exception:
                logging.warning(
                    "Failed to build scoped tool executor, falling back to global",
                    exc_info=True,
                )
                return config.create_toolcalling_llm(
                    dal=dal, model=chat_request.model, tool_results_dir=tool_results_dir
                )

        # Build a project-scoped ToolCallingLLM when project_id is provided
        if chat_request.project_id:
            try:
                from projects import (
                    get_store,
                    get_instances_store,
                    build_project_tool_executor,
                )  # type: ignore  # noqa: PLC0415

                project = get_store().get(chat_request.project_id)
                if project:
                    from holmes.core.tool_calling_llm import ToolCallingLLM  # noqa: PLC0415

                    project_executor = build_project_tool_executor(
                        project, config, dal, get_instances_store()
                    )
                    ai = ToolCallingLLM(
                        project_executor,
                        config.max_steps,
                        config._get_llm(chat_request.model),
                        tool_results_dir=tool_results_dir,
                    )
                    logging.info(
                        "Using project-scoped tool executor for project '%s'",
                        project.name,
                    )
                    # Inject project context so the LLM knows which project the user is asking about
                else:
                    logging.warning(
                        "Project '%s' not found, falling back to scoped executor",
                        chat_request.project_id,
                    )
                    source = _extract_source_from_ask(chat_request.ask)
                    ai = _make_scoped_ai(source)
            except Exception:
                logging.warning(
                    "Failed to build project executor, falling back to scoped",
                    exc_info=True,
                )
                source = _extract_source_from_ask(chat_request.ask)
                ai = _make_scoped_ai(source)
        else:
            # No project — use source-scoped executor to stay within Anthropic tool limits
            source = _extract_source_from_ask(chat_request.ask)
            ai = _make_scoped_ai(source)
        global_instructions = dal.get_global_instructions_for_account()

        # ── Inject verified past resolutions into the first message ───────
        enriched_ask = chat_request.ask
        if not chat_request.conversation_history:
            try:
                from projects import get_investigation_store  # noqa: PLC0415

                similar = get_investigation_store().search_similar(
                    query=chat_request.ask,
                    project_id=chat_request.project_id or None,
                    limit=3,
                    min_score=0.3,
                )
                approved = [
                    s
                    for s in similar
                    if s.get("feedback") == "helpful" and s.get("resolution_summary")
                ]
                if approved:
                    ctx = (
                        "\n\n## Similar Past Investigations (verified resolutions)\n\n"
                    )
                    ctx += (
                        "The following past investigations were marked as helpful by the team. "
                        "Consider this context but verify independently with current data.\n\n"
                    )
                    for i, s in enumerate(approved, 1):
                        ctx += (
                            f"### Past Investigation {i} (match: {s['score']:.0%}, source: {s['source']})\n"
                            f"**Question:** {s['question']}\n"
                            f"**Resolution:** {s['resolution_summary']}\n\n"
                        )
                    enriched_ask = chat_request.ask + ctx
            except Exception as e:
                logging.warning(
                    "Failed to inject similar investigations into chat: %s", e
                )

        # ── Inject project context so the LLM knows which project the user is in ──
        if chat_request.project_id:
            try:
                from projects import get_store as _get_proj_store  # noqa: PLC0415

                _proj = _get_proj_store().get(chat_request.project_id)
                if _proj:
                    enriched_ask = (
                        f"[Project context: The user is working in the '{_proj.name}' project. "
                        f"When they say 'this project' or ask about project-specific resources, "
                        f"they mean '{_proj.name}'.]\n\n"
                        + enriched_ask
                    )
            except Exception:
                pass

        # Merge global system prompt additions (from Settings page) with
        # any per-request additional_system_prompt.
        combined_system_prompt = chat_request.additional_system_prompt or ""
        try:
            from server_frontend import _system_prompt_additions  # noqa: PLC0415

            if _system_prompt_additions:
                combined_system_prompt = (
                    f"{_system_prompt_additions}\n\n{combined_system_prompt}"
                    if combined_system_prompt
                    else _system_prompt_additions
                )
        except ImportError:
            pass  # server_frontend not mounted (e.g. CLI mode)

        messages = build_chat_messages(
            enriched_ask,
            chat_request.conversation_history,
            ai=ai,
            config=config,
            global_instructions=global_instructions,
            additional_system_prompt=combined_system_prompt or None,
            runbooks=runbooks,
            images=chat_request.images,
            prompt_component_overrides=prompt_component_overrides,
        )

        if chat_request.stream:
            investigation_id = uuid.uuid4().hex
            started_at = datetime.now(timezone.utc).isoformat()
            raw_stream = ai.call_stream(
                msgs=messages,
                enable_tool_approval=chat_request.enable_tool_approval or False,
                tool_decisions=chat_request.tool_decisions,
                response_format=chat_request.response_format,
                request_context=request_context,
            )
            tracked_stream = _investigation_tracking_stream(
                raw_stream,
                investigation_id=investigation_id,
                started_at=started_at,
                question=chat_request.ask,
                project_id=chat_request.project_id or "",
                initial_metadata={"model": ai.llm.model},
            )
            stream = stream_chat_formatter(
                tracked_stream,
                [f.model_dump() for f in follow_up_actions],
            )
            return StreamingResponse(
                _stream_with_storage_cleanup(storage, stream, req_info),
                media_type="text/event-stream",
            )
        else:
            try:
                llm_call = ai.messages_call(
                    messages=messages,
                    trace_span=chat_request.trace_span,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                )

                logging.info(f"Completed {req_info}")
                return ChatResponse(
                    analysis=llm_call.result,
                    tool_calls=llm_call.tool_calls,
                    conversation_history=llm_call.messages,
                    follow_up_actions=follow_up_actions,
                    metadata=llm_call.metadata,
                )
            finally:
                storage.__exit__(None, None, None)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except litellm.exceptions.RateLimitError as e:
        raise HTTPException(status_code=429, detail=e.message)
    except Exception as e:
        logging.error(f"Error in /api/chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


scheduled_prompts_executor = ScheduledPromptsExecutor(
    dal=dal, config=config, chat_function=chat
)


@app.get("/api/model")
def get_model():
    return {"model_name": json.dumps(config.get_models_list())}


@app.get("/healthz")
def health_check():
    return {"status": "healthy"}


@app.get("/readyz")
def readiness_check():
    try:
        models_list = config.get_models_list()
        return {"status": "ready", "models": models_list}
    except Exception as e:
        logging.error(f"Readiness check failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Service not ready")


def main():
    """Holmes AI Server entry point"""
    # Configure uvicorn logging
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )
    log_config["formatters"]["default"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )

    # Sync before server start
    sync_before_server_start()
    _toolset_status_refresh_loop()

    # Start server
    uvicorn.run(app, host=HOLMES_HOST, port=HOLMES_PORT, log_config=log_config)


if __name__ == "__main__":
    main()
