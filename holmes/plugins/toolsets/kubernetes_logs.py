import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Set
from pydantic import BaseModel

from holmes.common.env_vars import KUBERNETES_LOGS_TIMEOUT_SECONDS
from holmes.core.tools import (
    StaticPrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolsetTag,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    BasePodLoggingToolset,
    FetchPodLogsParams,
    LoggingCapability,
    LoggingConfig,
    PodLoggingTool,
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import process_timestamps_to_int, to_unix_ms


# match ISO 8601 format (YYYY-MM-DDTHH:MM:SS[.fffffffff]Z) or (YYYY-MM-DDTHH:MM:SS[.fffffffff]+/-XX:XX)
timestamp_pattern = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
)


class Pod(BaseModel):
    containers: list[str]


class StructuredLog(BaseModel):
    timestamp_ms: Optional[int]
    container: Optional[str]
    content: str


class LogResult(BaseModel):
    error: Optional[str]
    return_code: Optional[int]
    has_multiple_containers: bool
    logs: list[StructuredLog]


class KubernetesLogsToolset(BasePodLoggingToolset):
    """Implementation of the unified logging API for Kubernetes logs using kubectl commands"""

    @property
    def supported_capabilities(self) -> Set[LoggingCapability]:
        """Kubernetes native logging supports regex and exclude filters"""
        return {
            LoggingCapability.REGEX_FILTER,
            LoggingCapability.EXCLUDE_FILTER,
        }

    def __init__(self):
        prerequisite = StaticPrerequisite(enabled=False, disabled_reason="Initializing")
        super().__init__(
            name="kubernetes/logs",
            description="Read Kubernetes pod logs using a unified API",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/kubernetes/",
            icon_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRPKA-U9m5BxYQDF1O7atMfj9EMMXEoGu4t0Q&s",
            prerequisites=[prerequisite],
            is_default=True,
            tools=[],  # Initialize with empty tools first
            tags=[ToolsetTag.CORE],
        )
        # Now that parent is initialized and self.name exists, create the tool
        self.tools = [PodLoggingTool(self)]
        enabled, disabled_reason = self.health_check()
        prerequisite.enabled = enabled
        prerequisite.disabled_reason = disabled_reason

    def health_check(self) -> Tuple[bool, str]:
        try:
            # Check if kubectl is available
            result = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            else:
                return False, f"kubectl command failed: {result.stderr}"
        except subprocess.TimeoutExpired:
            return False, "kubectl command timed out"
        except FileNotFoundError:
            return False, "kubectl command not found"
        except Exception as e:
            return False, f"kubectl health check error: {str(e)}"

    def get_example_config(self):
        return LoggingConfig().model_dump()

    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        try:
            all_logs: list[StructuredLog] = []

            # Fetch previous and current logs in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_previous = executor.submit(
                    self._fetch_kubectl_logs, params, previous=True
                )
                future_current = executor.submit(
                    self._fetch_kubectl_logs, params, previous=False
                )

                futures = {future_previous: "previous", future_current: "current"}
                previous_logs_result = None
                current_logs_result = None

                for future in as_completed(futures):
                    log_type = futures[future]
                    try:
                        result = future.result()
                        if log_type == "previous":
                            previous_logs_result = result
                        else:
                            current_logs_result = result
                    except Exception as e:
                        logging.error(f"Error fetching {log_type} logs: {str(e)}")
                        error_result = LogResult(
                            logs=[],
                            error=f"Error fetching {log_type} logs: {str(e)}",
                            return_code=None,
                            has_multiple_containers=False,
                        )
                        if log_type == "previous":
                            previous_logs_result = error_result
                        else:
                            current_logs_result = error_result

            # Ensure both results are not None (they should always be set by the loop)
            if current_logs_result is None or previous_logs_result is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Internal error: Failed to fetch logs",
                    params=params.model_dump(),
                )

            return_code: Optional[int] = current_logs_result.return_code

            if previous_logs_result.logs:
                all_logs.extend(previous_logs_result.logs)
                return_code = previous_logs_result.return_code

            if current_logs_result.logs:
                all_logs.extend(current_logs_result.logs)
                return_code = current_logs_result.return_code

            if (
                not all_logs
                and previous_logs_result.error
                and current_logs_result.error
            ):
                # Both commands failed - return error from current logs
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=current_logs_result.error,
                    params=params.model_dump(),
                    return_code=return_code,
                )

            # Track counts for metadata
            total_count = len(all_logs)
            (
                filtered_logs,
                filtered_count_before_limit,
                used_substring_fallback,
                exclude_used_substring_fallback,
                removed_by_include_filter,
                removed_by_exclude_filter,
            ) = filter_logs(all_logs, params)

            has_multiple_containers = (
                previous_logs_result.has_multiple_containers
                or current_logs_result.has_multiple_containers
            )

            formatted_logs = format_logs(
                logs=filtered_logs,
                display_container_name=has_multiple_containers,
            )

            # Generate metadata
            metadata_lines = add_metadata(
                params=params,
                total_count=total_count,
                filtered_logs=filtered_logs,
                filtered_count_before_limit=filtered_count_before_limit,
                used_substring_fallback=used_substring_fallback,
                exclude_used_substring_fallback=exclude_used_substring_fallback,
                removed_by_include_filter=removed_by_include_filter,
                removed_by_exclude_filter=removed_by_exclude_filter,
                has_multiple_containers=has_multiple_containers,
            )

            # Check if we have any logs to return
            if len(filtered_logs) == 0:
                # Return NO_DATA status when there are no logs
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data="\n".join(
                        metadata_lines
                    ),  # Still include metadata for context
                    params=params.model_dump(),
                    return_code=return_code,
                )

            # Put metadata at the end
            response_data = formatted_logs + "\n" + "\n".join(metadata_lines)

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params.model_dump(),
                return_code=return_code,
            )
        except Exception as e:
            logging.exception(f"Error fetching logs for pod {params.pod_name}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Error fetching logs: {str(e)}",
                params=params.model_dump(),
            )

    def _fetch_kubectl_logs(
        self,
        params: FetchPodLogsParams,
        previous: bool = False,
    ) -> LogResult:
        """Fetch logs using kubectl command"""
        cmd = [
            "kubectl",
            "logs",
            params.pod_name,
            "-n",
            params.namespace,
            "--all-containers=true",
            "--timestamps=true",
            "--prefix=true",
        ]

        if previous:
            cmd.append("--previous")

        try:
            result = subprocess.run(
                cmd,
                text=True,
                timeout=KUBERNETES_LOGS_TIMEOUT_SECONDS,
                check=False,  # do not throw error, we just return the error code
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            if result.returncode == 0:
                # Parse the logs - kubectl with --all-containers prefixes lines with container name
                log_result = self._parse_kubectl_logs(logs=result.stdout)
                log_result.return_code = result.returncode
                return log_result
            else:
                error_msg = (
                    result.stdout.strip()
                    or f"kubectl logs command failed with return code {result.returncode}"
                )
                logging.debug(
                    f"kubectl logs command failed for pod {params.pod_name} "
                    f"(previous={previous}): {error_msg}"
                )
                return LogResult(
                    logs=[],
                    error=error_msg,
                    return_code=result.returncode,
                    has_multiple_containers=False,
                )

        except subprocess.TimeoutExpired:
            error_msg = f"kubectl logs command timed out after {KUBERNETES_LOGS_TIMEOUT_SECONDS} seconds"
            logging.warning(
                f"kubectl logs command timed out for pod {params.pod_name} "
                f"(previous={previous})"
            )
            return LogResult(
                logs=[],
                error=error_msg,
                return_code=None,
                has_multiple_containers=False,
            )
        except Exception as e:
            error_msg = f"Error executing kubectl: {str(e)}"
            logging.error(
                f"Error executing kubectl logs for pod {params.pod_name} "
                f"(previous={previous}): {str(e)}"
            )
            return LogResult(
                logs=[],
                error=error_msg,
                return_code=None,
                has_multiple_containers=False,
            )

    def _parse_kubectl_logs(self, logs: str) -> LogResult:
        """Parse kubectl logs output with container prefixes"""
        structured_logs: List[StructuredLog] = []

        if not logs:
            return LogResult(
                logs=structured_logs,
                error=None,
                return_code=None,
                has_multiple_containers=False,
            )

        has_multiple_containers = False

        previous_container: Optional[str] = None

        for line in logs.strip().split("\n"):
            if not line:
                continue

            # kubectl with --all-containers prefixes lines with [pod/container]
            # Format: [pod/container] timestamp content
            container_match = re.match(r"^\[([^/]+)/([^\]]+)\] (.*)$", line)

            if container_match:
                pod_name, container_name, rest_of_line = container_match.groups()

                if not has_multiple_containers and not previous_container:
                    previous_container = container_name
                elif (
                    not has_multiple_containers and previous_container != container_name
                ):
                    has_multiple_containers = True

                # Now extract timestamp from rest_of_line
                timestamp_match = timestamp_pattern.match(rest_of_line)

                if timestamp_match:
                    timestamp_str = timestamp_match.group(0)
                    try:
                        log_unix_ts = to_unix_ms(timestamp_str)
                        prefix_length = len(timestamp_str)
                        content = rest_of_line[prefix_length:]
                        # Remove only the single space after timestamp, preserve other whitespaces to
                        #   keep the indentations of the original logs
                        if content.startswith(" "):
                            content = content[1:]

                        structured_logs.append(
                            StructuredLog(
                                timestamp_ms=log_unix_ts,
                                content=content,
                                container=container_name,
                            )
                        )
                    except ValueError:
                        # Keep the line with container info but no timestamp
                        structured_logs.append(
                            StructuredLog(
                                timestamp_ms=None,
                                content=rest_of_line,
                                container=container_name,
                            )
                        )
                else:
                    # No timestamp but has container info
                    structured_logs.append(
                        StructuredLog(
                            timestamp_ms=None,
                            content=rest_of_line,
                            container=container_name,
                        )
                    )
            else:
                # No container prefix - parse as regular log line
                parsed = parse_logs(line, None)
                structured_logs.extend(parsed)

        return LogResult(
            logs=structured_logs,
            error=None,
            return_code=None,
            has_multiple_containers=has_multiple_containers,
        )


# TODO: review this
def format_relative_time(timestamp_str: str, current_time: datetime) -> str:
    """Format a timestamp as relative to current time (e.g., '2 hours 15 minutes ago')"""
    try:
        # Handle relative timestamps (negative numbers)
        if timestamp_str and timestamp_str.startswith("-"):
            seconds = abs(int(timestamp_str))
            if seconds < 60:
                return f"{seconds} second{'s' if seconds != 1 else ''} before end time"
            minutes = seconds // 60
            if minutes < 60:
                return f"{minutes} minute{'s' if minutes != 1 else ''} before end time"
            hours = minutes // 60
            if hours < 24:
                return f"{hours} hour{'s' if hours != 1 else ''} before end time"
            days = hours // 24
            return f"{days} day{'s' if days != 1 else ''} before end time"

        # Parse the timestamp
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

        # Calculate the difference
        diff = current_time - timestamp

        # If in the future
        if diff.total_seconds() < 0:
            diff = timestamp - current_time
            suffix = "from now"
        else:
            suffix = "ago"

        # Format the difference
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0 and days == 0:  # Only show minutes if less than a day
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

        if not parts:
            if diff.seconds < 60:
                return "just now" if suffix == "ago" else "right now"

        return f"{' '.join(parts)} {suffix}"
    except Exception:
        # If we can't parse it, just return the original
        return timestamp_str


# TODO: review this
def add_metadata(
    params: FetchPodLogsParams,
    total_count: int,
    filtered_logs: List[StructuredLog],
    filtered_count_before_limit: int,
    used_substring_fallback: bool,
    exclude_used_substring_fallback: bool,
    removed_by_include_filter: int,
    removed_by_exclude_filter: int,
    has_multiple_containers: bool,
) -> List[str]:
    """Generate all metadata for the log query"""
    metadata_lines = [
        "\n" + "=" * 80,
        "LOG QUERY METADATA",
        "=" * 80,
    ]

    # Time Context section
    current_time = datetime.now(timezone.utc)
    current_time_str = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata_lines.extend(
        [
            "Time Context:",
            f"- Query executed at: {current_time_str} (UTC)",
            "",
            "Query Parameters:",
            f"- Pod: {params.pod_name}",
            f"- Namespace: {params.namespace}",
            "- Log source: Current and previous container logs",
        ]
    )

    # Always show time range info
    if params.start_time or params.end_time:
        start_str = params.start_time or "beginning"
        end_str = params.end_time or "now"

        # Calculate relative times and duration
        relative_parts = []

        # Parse timestamps for duration calculation
        start_dt = None
        end_dt = None

        if params.start_time and params.start_time != "beginning":
            start_relative = format_relative_time(params.start_time, current_time)
            relative_parts.append(f"Started: {start_relative}")
            try:
                if not params.start_time.startswith("-"):
                    start_dt = datetime.fromisoformat(
                        params.start_time.replace("Z", "+00:00")
                    )
            except Exception:
                pass

        if params.end_time and params.end_time != "now":
            end_relative = format_relative_time(params.end_time, current_time)
            relative_parts.append(f"Ended: {end_relative}")
            try:
                end_dt = datetime.fromisoformat(params.end_time.replace("Z", "+00:00"))
            except Exception:
                pass
        else:
            # If end_time is "now" or not specified, use current time
            end_dt = current_time

        # Calculate duration if we have both timestamps
        if start_dt and end_dt:
            duration = end_dt - start_dt
            if duration.total_seconds() > 0:
                days = duration.days
                hours = duration.seconds // 3600
                minutes = (duration.seconds % 3600) // 60

                duration_parts = []
                if days > 0:
                    duration_parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours > 0:
                    duration_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes > 0:
                    duration_parts.append(
                        f"{minutes} minute{'s' if minutes != 1 else ''}"
                    )

                if duration_parts:
                    duration_str = " ".join(duration_parts)
                else:
                    duration_str = "less than 1 minute"

                metadata_lines.append(
                    f"- Log time range: {start_str} (UTC) to {end_str} (UTC) ({duration_str})"
                )
            else:
                metadata_lines.append(
                    f"- Log time range: {start_str} (UTC) to {end_str} (UTC)"
                )
        else:
            metadata_lines.append(
                f"- Log time range: {start_str} (UTC) to {end_str} (UTC)"
            )

        if relative_parts:
            metadata_lines.append(f"  {' | '.join(relative_parts)}")
    else:
        metadata_lines.append(
            "- Log time range: None (fetching logs available via `kubectl logs`)"
        )

    # Add container info if multiple containers
    if has_multiple_containers:
        metadata_lines.append("- Container(s): Multiple containers")

    metadata_lines.extend(
        [
            "",
            f"Total logs found before filtering: {total_count:,}",
        ]
    )

    # Only show filtering details if filters were applied
    if params.filter or params.exclude_filter:
        metadata_lines.append("")
        metadata_lines.append("Filtering Applied:")

    if params.filter:
        if used_substring_fallback:
            metadata_lines.append(
                f"  ⚠️  Filter '{params.filter}' is not valid regex, using substring match"
            )
        matched_by_filter = total_count - removed_by_include_filter
        percentage = (matched_by_filter / total_count * 100) if total_count > 0 else 0
        metadata_lines.append(f"  1. Include filter: '{params.filter}'")
        metadata_lines.append(
            f"     → Matched: {matched_by_filter:,} logs ({percentage:.1f}% of total)"
        )

    if params.exclude_filter:
        if exclude_used_substring_fallback:
            metadata_lines.append(
                f"  ⚠️  Exclude filter '{params.exclude_filter}' is not valid regex, using substring match"
            )
        metadata_lines.append("")
        metadata_lines.append(f"  2. Exclude filter: '{params.exclude_filter}'")
        metadata_lines.append(f"     → Excluded: {removed_by_exclude_filter:,} logs")
        metadata_lines.append(f"     → Remaining: {filtered_count_before_limit:,} logs")

    # Display section
    metadata_lines.append("")
    hit_limit = params.limit is not None and params.limit < filtered_count_before_limit
    if hit_limit and params.limit is not None:
        logs_omitted = filtered_count_before_limit - params.limit
        metadata_lines.append(
            f"Display: Showing latest {params.limit:,} of {filtered_count_before_limit:,} filtered logs ({logs_omitted:,} omitted)"
        )
    else:
        if filtered_count_before_limit == total_count:
            metadata_lines.append(f"Display: Showing all {len(filtered_logs):,} logs")
        else:
            metadata_lines.append(
                f"Display: Showing all {len(filtered_logs):,} filtered logs"
            )

    # Add contextual hints based on results
    if len(filtered_logs) == 0:
        metadata_lines.append("")
        if params.filter and total_count > 0:
            # Logs exist but none matched the filter
            metadata_lines.append("Result: No logs matched your filters")
            metadata_lines.append("")
            metadata_lines.append("⚠️  Suggestions:")
            metadata_lines.append("  - Try a broader filter pattern")
            metadata_lines.append(
                f"  - Remove the filter to see all {total_count:,} available logs"
            )
            metadata_lines.append(
                "  - Your filter may be too specific for the log format used"
            )
        else:
            # No logs exist at all
            metadata_lines.append("Result: No logs found for this pod")
            metadata_lines.append("")
            metadata_lines.append("⚠️  Possible reasons:")
            if params.start_time or params.end_time:
                metadata_lines.append("  - Pod was not running during this time period")
            else:
                metadata_lines.append(
                    "  - Pod may not exist or may have been recently created"
                )
            metadata_lines.append("  - Container might not be logging to stdout/stderr")
            metadata_lines.append(
                "  - Logs might be going to a file instead of stdout/stderr"
            )

            # Only show time range suggestions if a time range was specified
            if params.start_time or params.end_time:
                metadata_lines.append("")
                metadata_lines.append("⚠️  Try:")
                metadata_lines.append(
                    "  - Remove time range to see ALL available logs (recommended unless you need this specific timeframe)"
                )
                metadata_lines.append("  - Or expand time range (e.g., last 24 hours)")
            else:
                metadata_lines.append("")
                metadata_lines.append("⚠️  Try:")
                metadata_lines.append(
                    f"  - Check if pod exists: kubectl get pods -n {params.namespace}"
                )
                metadata_lines.append(
                    f"  - Check pod events: kubectl describe pod {params.pod_name} -n {params.namespace}"
                )
    elif hit_limit:
        metadata_lines.append("")
        metadata_lines.append("⚠️  Hit display limit! Suggestions:")
        metadata_lines.append(
            "  - Add exclude_filter to remove noise: exclude_filter='<pattern1>|<pattern2>|<pattern3>'"
        )
        metadata_lines.append("  - Narrow time range to see fewer logs")
        metadata_lines.append(
            "  - Use more specific filter: filter='<term1>.*<term2>|<exact-phrase>'"
        )

    metadata_lines.append("=" * 80)
    return metadata_lines


def format_logs(logs: List[StructuredLog], display_container_name: bool) -> str:
    if display_container_name:
        return "\n".join([f"{log.container or 'N/A'}: {log.content}" for log in logs])
    else:
        return "\n".join([log.content for log in logs])


class TimeFilter(BaseModel):
    start_ms: int
    end_ms: int


def filter_logs(
    logs: List[StructuredLog], params: FetchPodLogsParams
) -> Tuple[List[StructuredLog], int, bool, bool, int, int]:
    time_filter: Optional[TimeFilter] = None
    if params.start_time or params.end_time:
        start, end = process_timestamps_to_int(
            start=params.start_time,
            end=params.end_time,
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )
        time_filter = TimeFilter(start_ms=start * 1000, end_ms=end * 1000)

    filtered_logs = []
    # is this really needed? doesn't kubectl already sort logs for us
    logs.sort(key=lambda x: x.timestamp_ms or 0)

    # Pre-compile regex patterns if provided
    regex_pattern = None
    exclude_regex_pattern = None
    used_substring_fallback = False
    exclude_used_substring_fallback = False

    # Track filtering statistics
    removed_by_include_filter = 0
    removed_by_exclude_filter = 0

    if params.filter:
        try:
            # Try to compile as regex first
            regex_pattern = re.compile(params.filter, re.IGNORECASE)
        except re.error:
            # If not a valid regex, fall back to simple substring matching
            logging.debug(
                f"Filter '{params.filter}' is not a valid regex, using substring matching"
            )
            regex_pattern = None
            used_substring_fallback = True

    if params.exclude_filter:
        try:
            # Try to compile as regex first
            exclude_regex_pattern = re.compile(params.exclude_filter, re.IGNORECASE)
        except re.error:
            # If not a valid regex, fall back to simple substring matching
            logging.debug(
                f"Exclude filter '{params.exclude_filter}' is not a valid regex, using substring matching"
            )
            exclude_regex_pattern = None
            exclude_used_substring_fallback = True

    for log in logs:
        # Apply inclusion filter
        if params.filter:
            if regex_pattern:
                # Use regex matching
                if not regex_pattern.search(log.content):
                    # exclude this log
                    removed_by_include_filter += 1
                    continue
            else:
                # Fall back to simple substring matching (case-insensitive)
                if params.filter.lower() not in log.content.lower():
                    # exclude this log
                    removed_by_include_filter += 1
                    continue

        # Apply exclusion filter
        if params.exclude_filter:
            if exclude_regex_pattern:
                # Use regex matching
                if exclude_regex_pattern.search(log.content):
                    # exclude this log
                    removed_by_exclude_filter += 1
                    continue
            else:
                # Fall back to simple substring matching (case-insensitive)
                if params.exclude_filter.lower() in log.content.lower():
                    # exclude this log
                    removed_by_exclude_filter += 1
                    continue

        if (
            time_filter
            and log.timestamp_ms
            and (
                log.timestamp_ms
                < time_filter.start_ms  # log is before expected time range
                or time_filter.end_ms
                < log.timestamp_ms  # log is after expected time range
            )
        ):
            # exclude this log
            continue
        else:
            filtered_logs.append(log)

    # Track count before limiting
    filtered_count_before_limit = len(filtered_logs)

    if params.limit and params.limit < len(filtered_logs):
        filtered_logs = filtered_logs[-params.limit :]

    return (
        filtered_logs,
        filtered_count_before_limit,
        used_substring_fallback,
        exclude_used_substring_fallback,
        removed_by_include_filter,
        removed_by_exclude_filter,
    )


def parse_logs(
    logs: Optional[str], container_name: Optional[str]
) -> list[StructuredLog]:
    structured_logs = []
    if logs:
        for log_line in logs.strip().split("\n"):
            if not isinstance(log_line, str):
                # defensive code given logs are from an external API
                structured_logs.append(
                    StructuredLog(
                        timestamp_ms=None,
                        content=str(log_line),
                        container=container_name,
                    )
                )
                continue
            match = timestamp_pattern.match(log_line)
            if match:
                timestamp_str = match.group(0)
                try:
                    log_unix_ts = to_unix_ms(timestamp_str)
                    prefix_length = len(timestamp_str)
                    # Remove only the single space after timestamp, preserve other whitespace
                    line_content = log_line[prefix_length:]
                    if line_content.startswith(" "):
                        line_content = line_content[1:]
                    structured_logs.append(
                        StructuredLog(
                            timestamp_ms=log_unix_ts,
                            content=line_content,
                            container=container_name,
                        )
                    )

                except ValueError:
                    # For invalid timestamp formats (when regex matches but date parsing fails)
                    # keep the original line - this is important for testing and consistency
                    structured_logs.append(
                        StructuredLog(
                            timestamp_ms=None,
                            content=log_line,
                            container=container_name,
                        )
                    )
            elif len(structured_logs) > 0:
                # if a line has no timestamp, assume it is part of a previous line
                structured_logs[-1].content += "\n" + log_line
            else:
                structured_logs.append(
                    StructuredLog(
                        timestamp_ms=None, content=log_line, container=container_name
                    )
                )
    return structured_logs
