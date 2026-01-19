import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import ceil
from typing import Optional, Set

from pydantic import BaseModel, field_validator

from holmes.core.llm import LLM
from holmes.core.tools import (
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.core.tools_utils.token_counting import count_tool_response_tokens
from holmes.plugins.toolsets.utils import get_param_or_raise

# Default values for log fetching
DEFAULT_LOG_LIMIT = 100
SECONDS_PER_DAY = 24 * 60 * 60
DEFAULT_TIME_SPAN_SECONDS = 7 * SECONDS_PER_DAY  # 1 week in seconds
DEFAULT_GRAPH_TIME_SPAN_SECONDS = 1 * 60 * 60  # 1 hour in seconds

POD_LOGGING_TOOL_NAME = "fetch_pod_logs"

TRUNCATION_PROMPT_PREFIX = "[... PREVIOUS LOGS ABOVE THIS LINE HAVE BEEN TRUNCATED]"
MIN_NUMBER_OF_CHARACTERS_TO_TRUNCATE: int = (
    50 + len(TRUNCATION_PROMPT_PREFIX)
)  # prevents the truncation algorithm from going too slow once the actual token count gets close to the expected limit


class LoggingCapability(str, Enum):
    """Optional advanced logging capabilities"""

    REGEX_FILTER = "regex_filter"  # If not supported, falls back to substring matching
    EXCLUDE_FILTER = "exclude_filter"  # If not supported, parameter is not shown at all
    HISTORICAL_DATA = (
        "historical_data"  # Can fetch logs for pods no longer in the cluster
    )


class LoggingConfig(BaseModel):
    """Base configuration for all logging backends"""

    pass


class FetchPodLogsParams(BaseModel):
    namespace: str
    pod_name: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    filter: Optional[str] = None
    exclude_filter: Optional[str] = None
    limit: Optional[int] = None

    @field_validator("start_time", mode="before")
    @classmethod
    def convert_start_time_to_string(cls, v):
        """Convert integer start_time values to strings."""
        if v is not None and isinstance(v, int):
            return str(v)
        return v


class BasePodLoggingToolset(Toolset, ABC):
    """Base class for all logging toolsets"""

    @property
    @abstractmethod
    def supported_capabilities(self) -> Set[LoggingCapability]:
        """Return the set of optional capabilities supported by this provider"""
        pass

    @abstractmethod
    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        pass

    def logger_name(self) -> str:
        return ""


def truncate_logs(
    logging_structured_tool_result: StructuredToolResult,
    llm: LLM,
    token_limit: int,
    structured_params: FetchPodLogsParams,
    tool_call_id: str,
    tool_name: str,
):
    original_token_count = count_tool_response_tokens(
        llm=llm,
        structured_tool_result=logging_structured_tool_result,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    token_count = original_token_count
    text = None
    while token_count > token_limit:
        # Loop because we are counting tokens but trimming characters. This means we try to trim a number of
        # characters proportional to the number of tokens but we may still have too many tokens
        if not text:
            text = logging_structured_tool_result.get_stringified_data()
        if not text:
            # Weird scenario where the result exceeds the token allowance but there is not data.
            # Exit and do nothing because I don't know how to handle such scenario.
            logging.warning(
                f"The calculated token count for logs is {token_count} but the limit is {token_limit}. However the data field is empty so there are no logs to truncate."
            )
            return
        ratio = token_count / token_limit
        character_count = len(text)
        number_of_characters_to_truncate = character_count - ceil(
            character_count / ratio
        )
        number_of_characters_to_truncate = max(
            MIN_NUMBER_OF_CHARACTERS_TO_TRUNCATE, number_of_characters_to_truncate
        )

        if len(text) <= number_of_characters_to_truncate:
            logging.warning(
                f"The calculated token count for logs is {token_count} (max allowed tokens={token_limit}) but the logs are only {len(text)} characters which is below the intended truncation of {number_of_characters_to_truncate} characters. Logs will no longer be truncated"
            )
            return
        else:
            linefeed_truncation_offset = max(
                text[number_of_characters_to_truncate:].find("\n"), 0
            )  # keep log lines atomic

            # Tentatively add the truncation prefix.
            # When counting tokens, we want to include the TRUNCATION_PROMPT_PREFIX because it will be part of the tool response.
            # Because we're truncating based on character counts but ultimately checking tokens count,
            # it is possible that the character truncation is incorrect and more need to be truncated.
            # This will be caught in the next iteration and the truncation prefix will be truncated
            # because MIN_NUMBER_OF_CHARACTERS_TO_TRUNCATE cannot be smaller than TRUNCATION_PROMPT_PREFIX
            text = (
                TRUNCATION_PROMPT_PREFIX
                + text[number_of_characters_to_truncate + linefeed_truncation_offset :]
            )
            logging_structured_tool_result.data = text
            token_count = count_tool_response_tokens(
                llm=llm,
                structured_tool_result=logging_structured_tool_result,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
    if token_count < original_token_count:
        logging.info(
            f"Logs for pod {structured_params.pod_name}/{structured_params.namespace} have been truncated from {original_token_count} tokens down to {token_count} tokens."
        )


class PodLoggingTool(Tool):
    """Common tool for fetching pod logs across different logging backends"""

    def __init__(self, toolset: BasePodLoggingToolset):
        # Get parameters dynamically based on what the toolset supports
        parameters = self._get_tool_parameters(toolset)

        # Build description based on capabilities
        # Include the toolset name in the description
        toolset_name = toolset.name if toolset.name else "logging backend"
        description = f"Fetch logs for a Kubernetes pod from {toolset_name}"
        capabilities = toolset.supported_capabilities

        if LoggingCapability.HISTORICAL_DATA in capabilities:
            description += (
                " (including historical data for pods no longer in the cluster)"
            )

        if (
            LoggingCapability.REGEX_FILTER in capabilities
            and LoggingCapability.EXCLUDE_FILTER in capabilities
        ):
            description += " with support for regex filtering and exclusion patterns"
        elif LoggingCapability.REGEX_FILTER in capabilities:
            description += " with support for regex filtering"

        # Add default information
        description += f". Defaults: Fetches last {DEFAULT_TIME_SPAN_SECONDS // SECONDS_PER_DAY} days of logs, limited to {DEFAULT_LOG_LIMIT} most recent entries"

        super().__init__(
            name=POD_LOGGING_TOOL_NAME,
            description=description,
            parameters=parameters,
        )
        self._toolset = toolset

    def _get_tool_parameters(self, toolset: BasePodLoggingToolset) -> dict:
        """Generate parameters based on what this provider supports"""
        # Base parameters always available
        params = {
            "pod_name": ToolParameter(
                description="The exact kubernetes pod name",
                type="string",
                required=True,
            ),
            "namespace": ToolParameter(
                description="Kubernetes namespace", type="string", required=True
            ),
            "start_time": ToolParameter(
                description=f"Start time for logs. Can be an RFC3339 formatted datetime (e.g. '2023-03-01T10:30:00Z') for absolute time or a negative string number (e.g. -3600) for relative seconds before end_time. Default: -{DEFAULT_TIME_SPAN_SECONDS} (last {DEFAULT_TIME_SPAN_SECONDS // SECONDS_PER_DAY} days)",
                type="string",
                required=False,
            ),
            "end_time": ToolParameter(
                description="End time for logs. Must be an RFC3339 formatted datetime (e.g. '2023-03-01T12:30:00Z'). If not specified, defaults to current time.",
                type="string",
                required=False,
            ),
            "limit": ToolParameter(
                description=f"Maximum number of logs to return. Default: {DEFAULT_LOG_LIMIT}",
                type="integer",
                required=False,
            ),
        }

        # Add filter - description changes based on regex support
        if LoggingCapability.REGEX_FILTER in toolset.supported_capabilities:
            params["filter"] = ToolParameter(
                description="""An optional filter for logs - can be a simple keyword/phrase or a regex pattern (case-insensitive).
Examples of useful filters:
- For errors: filter='err|error|fatal|critical|fail|exception|panic|crash'
- For warnings: filter='warn|warning|caution'
- For specific HTTP errors: filter='5[0-9]{2}|404|403'
- For Java exceptions: filter='Exception|Error|Throwable|StackTrace'
- For timeouts: filter='timeout|timed out|deadline exceeded'
If you get no results with a filter, try a broader pattern or drop the filter.""",
                type="string",
                required=False,
            )
        else:
            params["filter"] = ToolParameter(
                description="An optional keyword to filter logs - matches logs containing this text (case-insensitive)",
                type="string",
                required=False,
            )

        # ONLY add exclude_filter if supported - otherwise it doesn't exist
        if LoggingCapability.EXCLUDE_FILTER in toolset.supported_capabilities:
            params["exclude_filter"] = ToolParameter(
                description="""An optional exclusion filter - logs matching this pattern will be excluded. Can be a simple keyword or regex pattern (case-insensitive).
Examples of useful exclude filters:
- Exclude HTTP 200s: exclude_filter='GET.*200|POST.*200'
- Exclude health/metrics: exclude_filter='health|metrics|ping|heartbeat'
- Exclude specific log levels: exclude_filter='"level": "INFO"'
If you hit the log limit and see lots of repetitive INFO logs, use exclude_filter to remove the noise and focus on what matters.""",
                type="string",
                required=False,
            )

        return params

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        structured_params = FetchPodLogsParams(
            namespace=get_param_or_raise(params, "namespace"),
            pod_name=get_param_or_raise(params, "pod_name"),
            start_time=params.get("start_time"),
            end_time=params.get("end_time"),
            filter=params.get("filter"),
            exclude_filter=params.get("exclude_filter"),
            limit=params.get("limit"),
        )

        result = self._toolset.fetch_pod_logs(
            params=structured_params,
        )

        truncate_logs(
            logging_structured_tool_result=result,
            llm=context.llm,
            token_limit=context.max_token_count,
            structured_params=structured_params,
            tool_call_id=context.tool_call_id,
            tool_name=context.tool_name,
        )

        return result

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Generate a one-line description of this tool invocation"""
        namespace = params.get("namespace", "unknown-namespace")
        pod_name = params.get("pod_name", "unknown-pod")

        logger_name = (
            f"{self._toolset.logger_name()}: " if self._toolset.logger_name() else ""
        )
        return f"{logger_name}Fetch Logs (pod={pod_name}, namespace={namespace})"


def process_time_parameters(
    start_time: Optional[str],
    end_time: Optional[str],
    default_span_seconds: int = DEFAULT_TIME_SPAN_SECONDS,
) -> tuple[Optional[str], Optional[str]]:
    """
    Convert time parameters to standard RFC3339 format

    Args:
        start_time: Either RFC3339 timestamp or negative integer (seconds before end)
        end_time: RFC3339 timestamp or None (defaults to now)
        default_span_seconds: Default time span if start_time not provided

    Returns:
        Tuple of (start_time, end_time) both in RFC3339 format or None
    """
    # Process end time first (as start might depend on it)
    now = datetime.now(timezone.utc)

    # Handle end_time
    processed_end_time = None
    if end_time:
        try:
            # Check if it's already in RFC3339 format
            processed_end_time = end_time
            datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            # If not a valid RFC3339, log the error and use current time
            logging.warning(f"Invalid end_time format: {end_time}, using current time")
            processed_end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Default to current time
        processed_end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Handle start_time
    processed_start_time = None
    if start_time:
        try:
            # Check if it's a negative integer (relative time)
            if isinstance(start_time, int) or (
                isinstance(start_time, str)
                and start_time.startswith("-")
                and start_time[1:].isdigit()
            ):
                # Convert to seconds before end_time
                seconds_before = abs(int(start_time))

                # Parse end_time
                if processed_end_time:
                    end_datetime = datetime.fromisoformat(
                        processed_end_time.replace("Z", "+00:00")
                    )
                else:
                    end_datetime = now

                # Calculate start_time
                start_datetime = end_datetime - timedelta(seconds=seconds_before)
                processed_start_time = start_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                # Assume it's RFC3339
                processed_start_time = start_time
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            # If not a valid format, use default
            logging.warning(
                f"Invalid start_time format: {start_time}, using default time span"
            )
            if processed_end_time:
                end_datetime = datetime.fromisoformat(
                    processed_end_time.replace("Z", "+00:00")
                )
            else:
                end_datetime = now

            start_datetime = end_datetime - timedelta(seconds=default_span_seconds)
            processed_start_time = start_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Default to default_span_seconds before end_time
        if processed_end_time:
            end_datetime = datetime.fromisoformat(
                processed_end_time.replace("Z", "+00:00")
            )
        else:
            end_datetime = now

        start_datetime = end_datetime - timedelta(seconds=default_span_seconds)
        processed_start_time = start_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")

    return processed_start_time, processed_end_time
