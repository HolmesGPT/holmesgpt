import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from pydantic import AnyUrl

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.consts import (
    STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
    TOOLSET_CONFIG_MISSING_ERROR,
)
from holmes.plugins.toolsets.datadog.datadog_api import (
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
)
from holmes.plugins.toolsets.datadog.datadog_models import DatadogMetricsConfig
from holmes.plugins.toolsets.datadog.datadog_url_utils import (
    generate_datadog_metric_metadata_url,
    generate_datadog_metric_tags_url,
    generate_datadog_metrics_explorer_url,
    generate_datadog_metrics_list_url,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    get_param_or_raise,
    process_timestamps_to_int,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)


class BaseDatadogMetricsTool(Tool):
    toolset: "DatadogMetricsToolset"


ACTIVE_METRICS_DEFAULT_LOOK_BACK_HOURS = 24
ACTIVE_METRICS_DEFAULT_TIME_SPAN_SECONDS = 24 * 60 * 60


class ListActiveMetrics(BaseDatadogMetricsTool):
    def __init__(self, toolset: "DatadogMetricsToolset"):
        super().__init__(
            name="list_active_datadog_metrics",
            description=f"[datadog/metrics toolset] List active metrics from Datadog for the last {ACTIVE_METRICS_DEFAULT_LOOK_BACK_HOURS} hours. This includes metrics that have actively reported data points, including from pods no longer in the cluster.",
            parameters={
                "from_time": ToolParameter(
                    description=f"Start time for listing metrics. Can be an RFC3339 formatted datetime (e.g. '2023-03-01T10:30:00Z') or a negative integer for relative seconds from now (e.g. -86400 for 24 hours ago). Defaults to {ACTIVE_METRICS_DEFAULT_LOOK_BACK_HOURS} hours ago",
                    type="string",
                    required=False,
                ),
                "host": ToolParameter(
                    description="Filter metrics by hostname",
                    type="string",
                    required=False,
                ),
                "tag_filter": ToolParameter(
                    description="Filter metrics by tags in the format tag:value.",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        url = None
        query_params = None

        try:
            from_time_str = params.get("from_time")

            from_time, _ = process_timestamps_to_int(
                start=from_time_str,
                end=None,
                default_time_span_seconds=ACTIVE_METRICS_DEFAULT_TIME_SPAN_SECONDS,
            )

            url = f"{self.toolset.dd_config.site_api_url}/api/v1/metrics"
            headers = get_headers(self.toolset.dd_config)

            query_params = {
                "from": from_time,
            }

            if params.get("host"):
                query_params["host"] = params["host"]

            if params.get("tag_filter"):
                query_params["tag_filter"] = params["tag_filter"]

            data = execute_datadog_http_request(
                url=url,
                headers=headers,
                timeout=self.toolset.dd_config.request_timeout,
                method="GET",
                payload_or_params=query_params,
            )

            metrics = data.get("metrics", [])
            if not metrics:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    data="Your filter returned no metrics. Change your filter and try again",
                    params=params,
                )

            output = ["Metric Name"]
            output.append("-" * 50)

            for metric in sorted(metrics):
                output.append(metric)

            url = generate_datadog_metrics_list_url(
                self.toolset.dd_config,
                from_time,
                params.get("host"),
                params.get("tag_filter"),
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data="\n".join(output),
                params=params,
                url=url,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)

            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Ensure your Datadog Application Key has the 'metrics_read' "
                    f"and 'timeseries_query' permissions. Error: {str(e)}"
                )
            else:
                # Include full API error details for better debugging
                error_msg = (
                    f"Datadog API error (status {e.status_code}): {e.response_text}"
                )
                if params:
                    # ListActiveMetrics parameters: from_time, host, tag_filter
                    if params.get("host"):
                        error_msg += f"\nHost filter: {params.get('host')}"
                    if params.get("tag_filter"):
                        error_msg += f"\nTag filter: {params.get('tag_filter')}"

                    from_time_param = params.get("from_time")
                    if from_time_param:
                        time_desc = from_time_param
                    else:
                        time_desc = f"default (last {ACTIVE_METRICS_DEFAULT_LOOK_BACK_HOURS} hours)"
                    error_msg += f"\nTime range: {time_desc}"

                    # Note: We cannot generate a Datadog Metrics Explorer URL for ListActiveMetrics
                    # because the Metrics Explorer requires a specific metric query,
                    # while ListActiveMetrics just lists available metrics without querying any specific one

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=json.dumps({"url": url, "params": query_params})
                if url and query_params
                else None,
            )

        except Exception as e:
            logging.exception(
                f"Failed to query Datadog metrics for params: {params}", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception while querying Datadog: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        filters = []
        if params.get("host"):
            filters.append(f"host={params['host']}")
        if params.get("tag_filter"):
            filters.append(f"tag_filter={params['tag_filter']}")
        filter_str = f"{', '.join(filters)}" if filters else "all"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Active Metrics ({filter_str})"


class QueryMetrics(BaseDatadogMetricsTool):
    def __init__(self, toolset: "DatadogMetricsToolset"):
        super().__init__(
            name="query_datadog_metrics",
            description="[datadog/metrics toolset] Query timeseries data from Datadog for a specific metric, including historical data for pods no longer in the cluster",
            parameters={
                "query": ToolParameter(
                    description="The metric query string (e.g., 'system.cpu.user{host:myhost}')",
                    type="string",
                    required=True,
                ),
                "from_time": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "to_time": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "description": ToolParameter(
                    description="Describes the query",
                    type="string",
                    required=True,
                ),
                "output_type": ToolParameter(
                    description="Specifies how to interpret the Datadog result. Use 'Plain' for raw values, 'Bytes' to format byte values, 'Percentage' to scale 0–1 values into 0–100%, or 'CPUUsage' to convert values to cores (e.g., 500 becomes 500m, 2000 becomes 2).",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        url = None
        query_params = None

        try:
            query = get_param_or_raise(params, "query")

            (from_time, to_time) = process_timestamps_to_int(
                start=params.get("from_time"),
                end=params.get("to_time"),
                default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
            )

            url = f"{self.toolset.dd_config.site_api_url}/api/v1/query"
            headers = get_headers(self.toolset.dd_config)

            query_params = {
                "query": query,
                "from": from_time,
                "to": to_time,
            }

            data = execute_datadog_http_request(
                url=url,
                headers=headers,
                timeout=self.toolset.dd_config.request_timeout,
                method="GET",
                payload_or_params=query_params,
            )

            series = data.get("series", [])
            description = params.get("description", "")
            output_type = params.get("output_type", "Plain")

            if not series:
                # Include detailed context in error message
                from_time_param = params.get("from_time")
                to_time_param = params.get("to_time")

                if from_time_param:
                    from_desc = from_time_param
                else:
                    from_desc = (
                        f"default (last {DEFAULT_TIME_SPAN_SECONDS // 86400} days)"
                    )

                to_desc = to_time_param or "now"

                error_msg = (
                    f"The query returned no data.\n"
                    f"Query: {params.get('query', 'not specified')}\n"
                    f"Time range: {from_desc} to {to_desc}\n"
                    f"Please check your query syntax and ensure data exists for this time range."
                )

                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=error_msg,
                    params=params,
                )

            # Transform Datadog series data to match Prometheus format
            prometheus_result = []
            for serie in series:
                # Extract metric info from Datadog series
                metric_info = {}
                if "metric" in serie:
                    metric_info["__name__"] = serie["metric"]

                # Add other fields from scope/tag_set if available
                if "scope" in serie and serie["scope"]:
                    # Parse scope like "pod_name:robusta-runner-78599b764d-f847h" into labels
                    scope_parts = serie["scope"].split(",")
                    for part in scope_parts:
                        if ":" in part:
                            key, value = part.split(":", 1)
                            metric_info[key.strip()] = value.strip()

                # Transform pointlist to values format (timestamp, value as strings)
                values = []
                if "pointlist" in serie:
                    for point in serie["pointlist"]:
                        if len(point) >= 2:
                            # Convert timestamp from milliseconds to seconds, format as string
                            timestamp = int(point[0] / 1000)
                            value = str(point[1])
                            values.append([timestamp, value])

                prometheus_result.append({"metric": metric_info, "values": values})

            # Convert timestamps to RFC3339 format for start/end
            start_rfc = datetime.fromtimestamp(from_time).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_rfc = datetime.fromtimestamp(to_time).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Create response matching Prometheus format
            response_data = {
                "status": "success",
                "error_message": None,
                "tool_name": self.name,
                "description": description,
                "query": query,
                "start": start_rfc,
                "end": end_rfc,
                "step": 60,  # Default step, Datadog doesn't provide this directly
                "output_type": output_type,
                "data": {"resultType": "matrix", "result": prometheus_result},
            }

            url = generate_datadog_metrics_explorer_url(
                self.toolset.dd_config,
                query,
                from_time,
                to_time,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
                url=url,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)

            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Ensure your Datadog Application Key has the 'metrics_read' "
                    f"and 'timeseries_query' permissions. Error: {str(e)}"
                )
            else:
                # Include full API error details for better debugging
                error_msg = (
                    f"Datadog API error (status {e.status_code}): {e.response_text}"
                )
                if params:
                    error_msg += f"\nQuery: {params.get('query', 'not specified')}"

                    from_time_param = params.get("from_time")
                    to_time_param = params.get("to_time")

                    if from_time_param:
                        from_desc = from_time_param
                    else:
                        from_desc = (
                            f"default (last {DEFAULT_TIME_SPAN_SECONDS // 86400} days)"
                        )

                    to_desc = to_time_param or "now"
                    error_msg += f"\nTime range: {from_desc} to {to_desc}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=json.dumps({"url": url, "params": query_params})
                if url and query_params
                else None,
            )

        except Exception as e:
            logging.exception(
                f"Failed to query Datadog metrics for params: {params}", exc_info=True
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception while querying Datadog: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Query Metrics ({description})"


class QueryMetricsMetadata(BaseDatadogMetricsTool):
    def __init__(self, toolset: "DatadogMetricsToolset"):
        super().__init__(
            name="get_datadog_metric_metadata",
            description="[datadog/metrics toolset] Get metadata about one or more metrics including their type, description, unit, and other properties",
            parameters={
                "metric_names": ToolParameter(
                    description="Comma-separated list of metric names to get metadata for (e.g., 'system.cpu.user, system.mem.used')",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        try:
            metric_names_str = get_param_or_raise(params, "metric_names")

            metric_names = [
                name.strip()
                for name in metric_names_str.split(",")
                if name.strip()  # Filter out empty strings
            ]

            if not metric_names:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="metric_names cannot be empty",
                    params=params,
                )

            headers = get_headers(self.toolset.dd_config)

            results = {}
            errors = {}

            for metric_name in metric_names:
                try:
                    api_url = f"{self.toolset.dd_config.site_api_url}/api/v1/metrics/{metric_name}"

                    data = execute_datadog_http_request(
                        url=api_url,
                        headers=headers,
                        payload_or_params={},
                        timeout=self.toolset.dd_config.request_timeout,
                        method="GET",
                    )

                    results[metric_name] = data

                except DataDogRequestError as e:
                    if e.status_code == 404:
                        errors[metric_name] = "Metric not found"
                    elif e.status_code == 429:
                        errors[metric_name] = (
                            f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
                        )
                    else:
                        errors[metric_name] = f"Error {e.status_code}: {str(e)}"
                except Exception as e:
                    errors[metric_name] = f"Exception: {str(e)}"

            response_data = {
                "metrics_metadata": results,
                "errors": errors,
                "total_requested": len(metric_names),
                "successful": len(results),
                "failed": len(errors),
            }

            # Generate URL for the first metric (or a general metrics page if multiple)
            if metric_names:
                url = generate_datadog_metric_metadata_url(
                    self.toolset.dd_config,
                    metric_names[0],
                )
            else:
                url = None

            if not results and errors:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Failed to retrieve metadata for all metrics",
                    data=response_data,
                    params=params,
                    url=url,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
                url=url,
            )

        except Exception as e:
            logging.exception(
                f"Failed to query Datadog metric metadata for params: {params}",
                exc_info=True,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception while querying Datadog: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        metric_names = params.get("metric_names", [])
        if isinstance(metric_names, list):
            if len(metric_names) == 1:
                return f"Get Metric Metadata ({metric_names[0]})"
            elif len(metric_names) > 1:
                return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Datadog metric metadata for {len(metric_names)} metrics"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Datadog metric metadata"


class ListMetricTags(BaseDatadogMetricsTool):
    def __init__(self, toolset: "DatadogMetricsToolset"):
        super().__init__(
            name="list_datadog_metric_tags",
            description="[datadog/metrics toolset] List all available tags and aggregations for a specific metric. This helps in building queries by showing what dimensions are available for filtering.",
            parameters={
                "metric_name": ToolParameter(
                    description="The name of the metric to get tags for (e.g., 'system.cpu.user', 'container.memory.usage')",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        api_url = None
        query_params = None

        try:
            metric_name = get_param_or_raise(params, "metric_name")

            api_url = f"{self.toolset.dd_config.site_api_url}/api/v2/metrics/{metric_name}/active-configurations"
            headers = get_headers(self.toolset.dd_config)

            data = execute_datadog_http_request(
                url=api_url,
                headers=headers,
                timeout=self.toolset.dd_config.request_timeout,
                method="GET",
                payload_or_params={},
            )

            web_url = generate_datadog_metric_tags_url(
                self.toolset.dd_config,
                metric_name,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
                url=web_url,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)

            if e.status_code == 404:
                error_msg = f"Metric '{params.get('metric_name', 'unknown')}' not found. Please check the metric name."
            elif e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Ensure your Datadog Application Key has the 'metrics_read' "
                    f"permissions. Error: {str(e)}"
                )
            else:
                # Include full API error details for better debugging
                error_msg = (
                    f"Datadog API error (status {e.status_code}): {e.response_text}"
                )
                if params:
                    error_msg += (
                        f"\nMetric name: {params.get('metric_name', 'not specified')}"
                    )

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=json.dumps({"url": api_url, "params": query_params})
                if api_url and query_params
                else None,
            )

        except Exception as e:
            logging.exception(
                f"Failed to query Datadog metric tags for params: {params}",
                exc_info=True,
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception while querying Datadog: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        metric_name = params.get("metric_name", "<no metric>")
        return f"List available tags for Datadog metric: {metric_name}"


class DatadogMetricsToolset(Toolset):
    dd_config: Optional[DatadogMetricsConfig] = None

    def __init__(self):
        super().__init__(
            name="datadog/metrics",
            description="Toolset for fetching metrics and metadata from Datadog, including historical data for pods no longer in the cluster",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://imgix.datadoghq.com//img/about/presskit/DDlogo.jpg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListActiveMetrics(toolset=self),
                QueryMetrics(toolset=self),
                QueryMetricsMetadata(toolset=self),
                ListMetricTags(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )
        self._reload_instructions()

    def _perform_healthcheck(self, dd_config: DatadogMetricsConfig) -> Tuple[bool, str]:
        try:
            logging.debug("Performing Datadog metrics configuration healthcheck...")

            url = f"{dd_config.site_api_url}/api/v1/validate"
            headers = get_headers(dd_config)

            data = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params={},
                timeout=dd_config.request_timeout,
                method="GET",
            )

            if data.get("valid", False):
                logging.info("Datadog metrics healthcheck completed successfully")
                return True, ""
            else:
                error_msg = "Datadog API key validation failed"
                logging.error(f"Datadog metrics healthcheck failed: {error_msg}")
                return False, f"Datadog metrics healthcheck failed: {error_msg}"

        except Exception as e:
            logging.exception("Failed during Datadog metrics healthcheck")
            return False, f"Healthcheck failed with exception: {str(e)}"

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return (
                False,
                "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            )

        try:
            dd_config = DatadogMetricsConfig(**config)
            self.dd_config = dd_config

            success, error_msg = self._perform_healthcheck(dd_config)
            return success, error_msg

        except Exception as e:
            logging.exception("Failed to set up Datadog metrics toolset")
            return (False, f"Failed to parse Datadog configuration: {str(e)}")

    def get_example_config(self) -> Dict[str, Any]:
        example_config = DatadogMetricsConfig(
            dd_api_key="<your_datadog_api_key>",
            dd_app_key="<your_datadog_app_key>",
            site_api_url=AnyUrl("https://api.datadoghq.com"),
        )
        return example_config.model_dump(mode="json")

    def _reload_instructions(self):
        """Load Datadog metrics specific troubleshooting instructions."""
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "datadog_metrics_instructions.jinja2"
            )
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")
