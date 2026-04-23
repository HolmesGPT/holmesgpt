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
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import colorlog
import litellm
from holmes.core.oauth_config import OAuthConfigLookupError, OAuthTokenExchangeError
from holmes.core.oauth_server_callbacks import get_toolset_oauth_config, process_oauth_callback
from holmes.core.oauth_utils import _get_token_manager
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
    OAuthCallbackRequest,
    OAuthCallbackResponse,
)
from holmes.core.prompt import PromptComponent
from holmes.core.tools import PrerequisiteCacheMode, ToolsetStatusEnum, ToolsetTag, ToolsetType
from holmes.core.scheduled_prompts import ScheduledPromptsExecutor
from holmes.utils.connection_utils import patch_socket_create_connection
from holmes.utils.holmes_status import update_holmes_status_in_db
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
from holmes.utils.log import EndpointFilter
from holmes.checks.checks_api import init_checks_app
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.core.models import FrontendToolMode
from holmes.core.tools_utils.frontend_tools import build_frontend_noop_tool, build_frontend_pause_tool
from holmes.core.tracing import TracingFactory
from holmes.utils.stream import stream_chat_formatter


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

# Initialize tracer — auto-detects OTel if OTEL_EXPORTER_OTLP_ENDPOINT is set
server_tracer = TracingFactory.create_tracer(trace_type=os.environ.get("HOLMES_TRACE_BACKEND"))

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
    executor = config.cached_tool_executor  # thread-safe property
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
                changes = config.refresh_tool_executor(
                    dal,
                    toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
                    enable_all_toolsets_possible=False,
                )
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

app = FastAPI()

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


@app.post("/api/oauth/callback")
def oauth_callback(request: OAuthCallbackRequest) -> OAuthCallbackResponse:
    logging.info(
        "OAuth callback: toolset=%s client_id=%s client_secret_present=%s code_present=%s code_verifier_present=%s redirect_uri=%s",
        request.toolset_name, request.client_id, bool(request.client_secret), bool(request.code),
        bool(request.code_verifier), request.redirect_uri,
    )
    try:
        executor = config.create_tool_executor(dal=dal, reuse_executor=True, prerequisite_cache=PrerequisiteCacheMode.DISABLED)
        return process_oauth_callback(request, executor.toolsets, _get_token_manager(), executor=executor)
    except OAuthConfigLookupError as e:
        logging.error("OAuth config error for '%s': %s", request.toolset_name, e.detail)
        raise HTTPException(status_code=400, detail=e.detail)
    except OAuthTokenExchangeError as e:
        logging.error("OAuth token exchange failed for '%s': %s", request.toolset_name, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logging.error(f"OAuth callback failed for '{request.toolset_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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


def _stream_with_storage_cleanup(storage, stream_generator, req_info):
    """Wrap a stream generator to clean up tool result files after streaming completes."""
    try:
        yield from stream_generator
    finally:
        logging.info(f"Stream request end: {req_info}")
        storage.__exit__(None, None, None)


def _stream_with_trace_cleanup(storage, stream_generator, req_info, trace_span):
    """Wrap a stream generator with both storage cleanup and OTel span lifecycle.

    The investigation span stays active throughout all yields so that httpx
    auto-instrumented calls made during streaming become children of it.
    The span is ended in the finally block so it always closes, even on error.
    """
    try:
        yield from stream_generator
    finally:
        logging.info(f"Stream request end: {req_info}")
        trace_span.end()
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
        if chat_request.user_id:
            request_context.setdefault("headers", {})
            request_context["user_id"] = chat_request.user_id

        storage = tool_result_storage()
        tool_results_dir = storage.__enter__()

        ai = config.create_toolcalling_llm(
            dal=dal,
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
            enable_all_toolsets_possible=False,
            prerequisite_cache=PrerequisiteCacheMode.DISABLED,
            reuse_executor=True,
            model=chat_request.model,
            tracer=server_tracer,
            tool_results_dir=tool_results_dir,
        )

        global_instructions = dal.get_global_instructions_for_account()
        messages = build_chat_messages(
            chat_request.ask,
            chat_request.conversation_history,
            ai=ai,
            config=config,
            global_instructions=global_instructions,
            additional_system_prompt=chat_request.additional_system_prompt,
            runbooks=runbooks,
            images=chat_request.images,
            prompt_component_overrides=prompt_component_overrides,
        )

        # Build a per-request AI instance with frontend tools injected into the executor
        request_ai = ai
        has_pause_tools = False
        if chat_request.frontend_tools:
            # Validate no name collisions with backend tools
            backend_tool_names = set(ai.tool_executor.tools_by_name.keys())
            frontend_tool_instances = []
            for ft in chat_request.frontend_tools:
                if ft.name in backend_tool_names:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Frontend tool name '{ft.name}' conflicts with a built-in Holmes tool. Use a different name.",
                    )
                if ft.mode == FrontendToolMode.NOOP:
                    frontend_tool_instances.append(
                        build_frontend_noop_tool(
                            name=ft.name,
                            description=ft.description,
                            parameters=ft.parameters,
                            canned_response=ft.noop_response,
                        )
                    )
                else:
                    has_pause_tools = True
                    frontend_tool_instances.append(
                        build_frontend_pause_tool(
                            name=ft.name,
                            description=ft.description,
                            parameters=ft.parameters,
                        )
                    )

            # Pause-mode tools require streaming (the pause/resume flow needs SSE)
            if has_pause_tools and not chat_request.stream:
                raise HTTPException(
                    status_code=400,
                    detail="frontend_tools with mode='pause' requires stream=true (the pause/resume flow needs SSE)",
                )

            cloned_executor = ai.tool_executor.clone_with_extra_tools(frontend_tool_instances)
            request_ai = ai.with_executor(cloned_executor)

        if chat_request.stream:
            # Create root investigation span for streaming (same as non-streaming)
            trace_span = server_tracer.start_trace("holmesgpt.investigation")
            trace_span.log(metadata={
                "holmesgpt.investigation.question": chat_request.ask[:1024],
                "holmesgpt.investigation.stream": True,
            })
            otel_metrics = TracingFactory.get_metrics()
            if otel_metrics:
                inv_attrs = {"gen_ai_request_model": chat_request.model or config.model or "unknown"}
                otel_metrics.investigation_count.add(1, inv_attrs)

            stream = stream_chat_formatter(
                request_ai.call_stream(
                    msgs=messages,
                    enable_tool_approval=chat_request.enable_tool_approval or False,
                    tool_decisions=chat_request.tool_decisions,
                    frontend_tool_results=chat_request.frontend_tool_results,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                    trace_span=trace_span,
                ),
                [f.model_dump() for f in follow_up_actions],
            )
            return StreamingResponse(
                _stream_with_trace_cleanup(storage, stream, req_info, trace_span),
                media_type="text/event-stream",
            )
        else:
            try:
                # Use provided trace_span or create a root investigation span
                trace_span = chat_request.trace_span
                if trace_span is None:
                    trace_span = server_tracer.start_trace(
                        "holmesgpt.investigation",
                    )
                    trace_span.log(metadata={
                        "holmesgpt.investigation.question": chat_request.ask[:1024],
                    })

                _inv_start = time.time()
                llm_call = ai.call(
                    messages=messages,
                    trace_span=trace_span,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                )

                # Record investigation metrics
                otel_metrics = TracingFactory.get_metrics()
                if otel_metrics:
                    inv_attrs = {"gen_ai_request_model": chat_request.model or config.model or "unknown"}
                    otel_metrics.investigation_count.add(1, inv_attrs)
                    otel_metrics.investigation_duration.record(time.time() - _inv_start, inv_attrs)
                    if hasattr(llm_call, "num_llm_calls") and llm_call.num_llm_calls:
                        otel_metrics.investigation_iterations.record(llm_call.num_llm_calls, inv_attrs)

                logging.info(f"Completed {req_info}")
                response = ChatResponse(
                    analysis=llm_call.result,
                    tool_calls=llm_call.tool_calls,
                    conversation_history=llm_call.messages,
                    follow_up_actions=follow_up_actions,
                    metadata=llm_call.metadata,
                )
                return response
            finally:
                if trace_span is not None:
                    trace_span.end()
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
