"""Leftover of a removed multi-backend "logging backend" abstraction.

Historically, multiple log toolsets (Loki, Datadog, ...) implemented a shared
`fetch_pod_logs` interface defined here. That abstraction was dropped: each of
those toolsets now defines its own tools, and TODAY this module serves exactly
two purposes:

1. Shared constants (DEFAULT_LOG_LIMIT, DEFAULT_TIME_SPAN_SECONDS,
   DEFAULT_GRAPH_TIME_SPAN_SECONDS) imported by the Datadog, Loki,
   VictoriaLogs, Prometheus and Tempo toolsets.
2. The kubernetes/logs implementation of `fetch_pod_logs` (PodLoggingTool,
   FetchPodLogsParams, truncate_logs), used ONLY by
   holmes.plugins.toolsets.kubernetes_logs.KubernetesLogsToolset.

Do not build new logging backends on top of this module — define tools in the
new toolset directly and import only the constants if you need consistency.
"""

import logging
from math import ceil
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, field_validator

from holmes.core.llm import LLM
from holmes.core.tools import (
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.core.tools_utils.token_counting import count_tool_response_tokens
from holmes.plugins.toolsets.utils import get_param_or_raise

if TYPE_CHECKING:
    from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset

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


def truncate_logs(
    logging_structured_tool_result: StructuredToolResult,
    llm: LLM,
    token_limit: int,
    structured_params: FetchPodLogsParams,
    tool_call_id: str,
    tool_name: str,
):
    """Lossily trim logs from the top until the result fits within token_limit.

    Only used as a fallback when spill-to-disk is unavailable (see
    PodLoggingTool._invoke): the truncated logs are dropped and cannot be
    recovered by the LLM, so spilling the full result to disk is preferred.
    """
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
    """Tool for fetching Kubernetes pod logs"""

    def __init__(self, toolset: "KubernetesLogsToolset"):
        toolset_name = toolset.name if toolset.name else "logging backend"
        description = (
            f"Fetch logs for a Kubernetes pod from {toolset_name}"
            " with support for regex filtering and exclusion patterns"
            f". Defaults: Fetches last {DEFAULT_TIME_SPAN_SECONDS // SECONDS_PER_DAY} days of logs, limited to {DEFAULT_LOG_LIMIT} most recent entries"
        )

        parameters = {
            "pod_name": ToolParameter(
                description="The exact kubernetes pod name",
                type="string",
                required=True,
            ),
            "namespace": ToolParameter(
                description="Kubernetes namespace", type="string", required=True
            ),
            "start_time": ToolParameter(
                description=f"RFC3339 datetime (e.g. '2023-03-01T10:30:00Z') or negative number as string for seconds before end_time (e.g. '-3600'). Default: -{DEFAULT_TIME_SPAN_SECONDS} (last {DEFAULT_TIME_SPAN_SECONDS // SECONDS_PER_DAY} days)",
                type="string",
                required=False,
            ),
            "end_time": ToolParameter(
                description="RFC3339 datetime (e.g. '2023-03-01T12:30:00Z'). Default: now.",
                type="string",
                required=False,
            ),
            "limit": ToolParameter(
                description=f"Maximum number of logs to return. Default: {DEFAULT_LOG_LIMIT}",
                type="integer",
                required=False,
            ),
            "filter": ToolParameter(
                description="Optional keyword/phrase or case-insensitive regex to match, e.g. filter='err|error|fatal|fail|exception|panic' for errors. If no results, broaden the pattern or drop the filter.",
                type="string",
                required=False,
            ),
            "exclude_filter": ToolParameter(
                description="Optional keyword or case-insensitive regex to exclude, e.g. exclude_filter='health|metrics|ping' to cut noise when hitting the log limit.",
                type="string",
                required=False,
            ),
        }

        super().__init__(
            name=POD_LOGGING_TOOL_NAME,
            description=description,
            parameters=parameters,
        )
        self._toolset = toolset

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

        # When spill-to-disk is available, return the full logs: oversized
        # results are saved to disk by spill_oversized_tool_result and the LLM
        # gets a preview plus a file path it can cat/grep, instead of silently
        # losing everything above the token limit. Only fall back to lossy
        # in-place truncation when the result cannot be spilled.
        if not context.tool_results_dir:
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
        return f"Fetch Logs (pod={pod_name}, namespace={namespace})"
