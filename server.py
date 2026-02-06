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
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
    TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS,
)
from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core import investigation
from holmes.core.conversations import (
    build_chat_messages,
    build_issue_chat_messages,
)
from holmes.core.models import (
    ChatRequest,
    ChatResponse,
    FollowUpAction,
    InvestigateRequest,
    InvestigationResult,
    IssueChatRequest,
)
from holmes.core.prompt import generate_user_prompt
from holmes.core.scheduled_prompts import ScheduledPromptsExecutor
from holmes.plugins.prompts import load_and_render_prompt
from holmes.utils.connection_utils import patch_socket_create_connection
from holmes.utils.holmes_status import update_holmes_status_in_db
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
from holmes.utils.log import EndpointFilter
from holmes.utils.stream import stream_chat_formatter, stream_investigate_formatter

# removed: add_runbooks_to_user_prompt


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


def _toolset_status_refresh_loop():
    interval = TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS
    if interval <= 0:
        logging.info("Periodic toolset status refresh is disabled")
        return

    logging.info(
        f"Starting periodic toolset status refresh (interval: {interval} seconds)"
    )

    def refresh_loop():
        while True:
            time.sleep(interval)
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


@app.post("/api/investigate")
def investigate_issues(investigate_request: InvestigateRequest, http_request: Request):
    try:
        runbooks = config.get_runbook_catalog()
        request_context = extract_passthrough_headers(http_request)
        result = investigation.investigate_issues(
            investigate_request=investigate_request,
            dal=dal,
            config=config,
            model=investigate_request.model,
            runbooks=runbooks,
            request_context=request_context,
        )
        return result

    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except litellm.exceptions.RateLimitError as e:
        raise HTTPException(status_code=429, detail=e.message)
    except Exception as e:
        logging.error(f"Error in /api/investigate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stream/investigate")
def stream_investigate_issues(req: InvestigateRequest, http_request: Request):
    try:
        ai, system_prompt, user_prompt, response_format, sections = (
            investigation.get_investigation_context(req, dal, config)
        )
        request_context = extract_passthrough_headers(http_request)

        return StreamingResponse(
            stream_investigate_formatter(
                ai.call_stream(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_format=response_format,
                    sections=sections,
                    request_context=request_context,
                ),
            ),
            media_type="text/event-stream",
        )

    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except Exception as e:
        logging.exception(f"Error in /api/stream/investigate: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/issue_chat")
def issue_conversation(issue_chat_request: IssueChatRequest, http_request: Request):
    try:
        runbooks = config.get_runbook_catalog()
        ai = config.create_toolcalling_llm(dal=dal, model=issue_chat_request.model)
        global_instructions = dal.get_global_instructions_for_account()

        messages = build_issue_chat_messages(
            issue_chat_request=issue_chat_request,
            ai=ai,
            config=config,
            global_instructions=global_instructions,
            runbooks=runbooks,
        )
        request_context = extract_passthrough_headers(http_request)
        llm_call = ai.messages_call(messages=messages, request_context=request_context)

        return ChatResponse(
            analysis=llm_call.result,
            tool_calls=llm_call.tool_calls,
            conversation_history=llm_call.messages,
            metadata=llm_call.metadata,
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except litellm.exceptions.RateLimitError as e:
        raise HTTPException(status_code=429, detail=e.message)
    except Exception as e:
        logging.error(f"Error in /api/issue_chat: {e}", exc_info=True)
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
    These headers are forwarded to MCP servers for authentication and context.

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


@app.post("/api/chat")
def chat(chat_request: ChatRequest, http_request: Request):
    try:
        # Log incoming request details
        has_images = bool(chat_request.images)
        has_structured_output = bool(chat_request.response_format)
        logging.info(
            f"Received /api/chat request: model={chat_request.model}, "
            f"images={has_images}, structured_output={has_structured_output}, "
            f"streaming={chat_request.stream}"
        )

        runbooks = config.get_runbook_catalog()
        ai = config.create_toolcalling_llm(dal=dal, model=chat_request.model)
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
        )
        request_context = extract_passthrough_headers(http_request)

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

        if chat_request.stream:
            return StreamingResponse(
                stream_chat_formatter(
                    ai.call_stream(
                        msgs=messages,
                        enable_tool_approval=chat_request.enable_tool_approval or False,
                        tool_decisions=chat_request.tool_decisions,
                        response_format=chat_request.response_format,
                        request_context=request_context,
                    ),
                    [f.model_dump() for f in follow_up_actions],
                ),
                media_type="text/event-stream",
            )
        else:
            llm_call = ai.messages_call(
                messages=messages,
                trace_span=chat_request.trace_span,
                response_format=chat_request.response_format,
                request_context=request_context,
            )

            # For non-streaming, we need to handle approvals differently
            # This is a simplified version - in practice, non-streaming with approvals
            # would require a different approach or conversion to streaming
            return ChatResponse(
                analysis=llm_call.result,
                tool_calls=llm_call.tool_calls,
                conversation_history=llm_call.messages,
                follow_up_actions=follow_up_actions,
                metadata=llm_call.metadata,
            )
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
