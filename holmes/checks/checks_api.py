import hashlib
import logging
import os
import re
from typing import Optional

from fastapi import FastAPI, HTTPException
from litellm.exceptions import AuthenticationError
from pydantic import BaseModel, Field

from holmes.checks.checks import execute_check
from holmes.checks.models import Check, CheckMode, CheckResult, CheckStatus
from holmes.config import Config
from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.usage_recorder import UsageRecorderState, resolve_provider
from holmes.plugins.destinations.pagerduty.plugin import (
    DEFAULT_PAGERDUTY_API_URL,
    PagerDutyDestination,
)
from holmes.plugins.destinations.slack.plugin import SlackDestination

checks_app = FastAPI()

_CONFIG: Config
_CHECK_RUN_SUFFIX = re.compile(r"-\d{8}-\d{6}-[0-9a-f]{6}$", re.IGNORECASE)


def _get_check_identity(check_name: str, query: str) -> str:
    """Return an identity stable across scheduled check executions."""
    stable_name = _CHECK_RUN_SUFFIX.sub("", check_name) or "api-check"
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    return f"{stable_name}-{query_hash}"


def init_checks_app(main_app: FastAPI, config: Config):
    global _CONFIG
    _CONFIG = config

    main_app.mount("/api/checks", checks_app)


class CheckExecutionRequest(BaseModel):
    """Request model for check execution."""

    query: str
    name: Optional[str] = Field(
        None, description="Name of the check for tracking purposes"
    )
    timeout: int = 30
    mode: CheckMode = CheckMode.MONITOR
    destinations: list[dict] = Field(
        default_factory=list
    )  # TODO: change to DestinationConfig after deciding on the schema.
    model: Optional[str] = Field(None, description="The model to use for the check.")


class NotificationStatus(BaseModel):
    """Status of a notification attempt."""

    type: str  # "slack", "pagerduty", etc.
    channel: Optional[str] = None  # Channel/destination details
    status: str  # "sent", "failed", "skipped"
    error: Optional[str] = None  # Error message if failed


class CheckExecutionResponse(BaseModel):
    """Response model for check execution."""

    status: CheckStatus
    message: str
    duration: float
    rationale: Optional[str] = None
    error: Optional[str] = None
    model_used: Optional[str] = None  # The actual model that was used
    notifications: Optional[list[NotificationStatus]] = (
        None  # Notification delivery status
    )


def _get_ai(model: Optional[str]) -> ToolCallingLLM:
    return _CONFIG.create_toolcalling_llm(
        dal=_CONFIG.dal,
        toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
        reuse_executor=True,
        model=model,
    )


@checks_app.post("/execute")
def execute_health_check(
    request: CheckExecutionRequest,
) -> CheckExecutionResponse:
    """
    Execute a single health check.

    This endpoint is used by the Holmes operator to execute checks
    via the stateless API servers.
    """
    try:
        ai = _get_ai(request.model)

        # Create Check object from request
        # Extract destination names from list of dicts if needed
        destination_names = []
        if request.destinations:
            # Check if we have dictionaries or strings
            if request.destinations and isinstance(request.destinations[0], dict):
                # Extract 'type' field from destination dicts (matches CRD schema)
                destination_names = [
                    d.get("type", "") for d in request.destinations if d.get("type")
                ]
            else:
                destination_names = request.destinations

        check = Check(
            name=request.name or "api-check",
            query=request.query,
            timeout=request.timeout,
            mode=request.mode,
            destinations=destination_names,
        )

        # Build the recorder state so the operator-driven check shows up in
        # HolmesUsageEvents with request_type='health_check' and source_ref
        # set to the check name (per-check cost reporting key).
        ai_model = getattr(ai.llm, "model", None) or request.model or "unknown"
        recorder_state = UsageRecorderState(
            dal=_CONFIG.dal,
            request_type="health_check",
            request_source="operator",
            source_ref=request.name or "api-check",
            conversation_id=None,
            conversation_source=None,
            user_id=None,
            is_streaming=False,
            model=ai_model,
            provider=resolve_provider(ai_model),
            is_robusta_model=getattr(ai.llm, "is_robusta_model", False),
            meta={"check_mode": request.mode.value, "timeout": request.timeout},
        )

        # Execute the check using the shared function
        result: CheckResult = execute_check(
            check=check,
            ai=ai,
            verbose=False,
            console=None,
            recorder_state=recorder_state,
        )

        # Track notification statuses
        notifications = []

        # PagerDuty needs both failure and recovery events to manage one incident.
        if (
            result.status in (CheckStatus.FAIL, CheckStatus.PASS)
            and request.mode == CheckMode.ALERT
            and request.destinations
        ):
            try:
                # Create an Issue object for the failed check
                check_name = result.check_name
                cluster_name = (
                    os.environ.get("CLUSTER_NAME")
                    or _CONFIG.cluster_name
                    or "unknown"
                )
                stable_check_name = _CHECK_RUN_SUFFIX.sub("", check_name) or "api-check"
                check_identity = _get_check_identity(check_name, request.query)
                event_action = (
                    "resolve" if result.status == CheckStatus.PASS else "trigger"
                )
                issue = Issue(
                    id=f"healthcheck-{cluster_name}-{check_identity}",
                    name=f"Health Check Failed: {stable_check_name}",
                    source_instance_id=cluster_name,
                    source_type="HealthCheck",
                    presentation_status=(
                        IssueStatus.CLOSED
                        if result.status == CheckStatus.PASS
                        else IssueStatus.OPEN
                    ),
                    presentation_key_metadata=f"*Check:* `{stable_check_name}`\n*Query:* {request.query}",
                    show_status_in_title=False,  # Don't append " - open" to the title
                )

                # Create LLM result for the destination
                llm_result = LLMResult(
                    result=result.rationale or result.message,
                    tool_calls=[],
                    messages=[],
                )

                # Send to configured destinations
                for dest in request.destinations:
                    if isinstance(dest, dict):
                        dest_type = dest.get("type", "").lower()
                        dest_config = dest.get("config", {})
                    else:
                        dest_type = dest.lower()
                        dest_config = {}

                    if dest_type == "slack" and result.status == CheckStatus.FAIL:
                        slack_channel = None
                        notification = NotificationStatus(
                            type="slack", status="pending"
                        )

                        try:
                            # Check if SLACK_TOKEN is configured (from destination config or env var)
                            slack_token = dest_config.get("token") or os.environ.get(
                                "SLACK_TOKEN"
                            )
                            slack_channel = dest_config.get(
                                "channel"
                            ) or os.environ.get("SLACK_CHANNEL")
                            if slack_token and slack_channel:
                                notification.channel = slack_channel
                                slack_dest = SlackDestination(
                                    token=slack_token, channel=slack_channel
                                )
                                slack_dest.send_issue(issue, llm_result)

                                notification.status = "sent"
                                logging.info(
                                    f"Sent Slack notification to {slack_channel} for check {check_name}"
                                )
                            else:
                                notification.status = "skipped"
                                notification.error = (
                                    "SLACK_TOKEN or SLACK_CHANNEL not configured"
                                )
                                logging.warning(
                                    "SLACK_TOKEN or SLACK_CHANNEL not configured, skipping Slack notification"
                                )
                        except Exception as e:
                            notification.status = "failed"
                            notification.error = str(e)
                            logging.error(
                                f"Failed to send Slack notification: {e}", exc_info=True
                            )

                        notifications.append(notification)
                    elif dest_type == "pagerduty":
                        notification = NotificationStatus(
                            type="pagerduty", status="pending"
                        )

                        try:
                            inline_integration_key = dest_config.get("integration_key")
                            custom_api_url = dest_config.get("api_url")
                            integration_key = inline_integration_key or os.environ.get(
                                "PAGERDUTY_INTEGRATION_KEY"
                            )
                            if not integration_key:
                                notification.status = "skipped"
                                notification.error = (
                                    "PAGERDUTY_INTEGRATION_KEY not configured"
                                )
                                logging.warning(
                                    "PAGERDUTY_INTEGRATION_KEY not configured, "
                                    "skipping PagerDuty notification"
                                )
                            else:
                                pagerduty_dest = PagerDutyDestination(
                                    integration_key=integration_key,
                                    api_url=custom_api_url or DEFAULT_PAGERDUTY_API_URL,
                                )
                                if pagerduty_dest.send_issue(
                                    issue, llm_result, event_action=event_action
                                ):
                                    notification.status = "sent"
                                    logging.info(
                                        f"Sent PagerDuty {event_action} for check "
                                        f"{check_name}"
                                    )
                                else:
                                    notification.status = "failed"
                                    notification.error = (
                                        "PagerDuty notification failed"
                                    )
                        except Exception as e:
                            notification.status = "failed"
                            notification.error = "PagerDuty notification failed"
                            logging.error(
                                "Failed to send PagerDuty notification: %s",
                                type(e).__name__,
                            )

                        notifications.append(notification)

            except Exception as e:
                logging.error(
                    f"Failed to process alert destinations: {e}", exc_info=True
                )
                # Don't fail the whole request if notification fails

        # Return the result with the actual model used
        return CheckExecutionResponse(
            status=result.status,
            message=result.message,
            duration=result.duration,
            rationale=result.rationale,
            error=result.error,
            model_used=ai.llm.model,  # Include the actual model that was used
            notifications=(
                notifications if notifications else None
            ),  # Include notification statuses
        )

    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except Exception as e:
        logging.error(f"Error in /api/checks/execute: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
