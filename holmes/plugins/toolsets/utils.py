import datetime
import math
import time
import re
from typing import Dict, Optional, Tuple, Union

from dateutil import parser


def standard_start_datetime_tool_param_description(time_span_seconds: int):
    return f"Start datetime, inclusive. Should be formatted in rfc3339. If negative integer, the number of seconds relative to end. Defaults to -{time_span_seconds}"


def is_int(val):
    try:
        int(val)
    except ValueError:
        return False
    else:
        return True


def is_rfc3339(timestamp_str: str) -> bool:
    """Check if a string is in RFC3339 format."""
    try:
        parser.parse(timestamp_str)
        return True
    except (ValueError, TypeError):
        return False


def to_unix(timestamp_str: str) -> int:
    dt = parser.parse(timestamp_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def to_unix_ms(timestamp_str: str) -> int:
    dt = parser.parse(timestamp_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def unix_nano_to_rfc3339(unix_nano: int | float) -> str:
    unix_seconds = int(unix_nano) / 1_000_000_000

    seconds_part = int(unix_seconds)
    milliseconds_part = int((unix_seconds - seconds_part) * 1000)

    dt = datetime.datetime.fromtimestamp(seconds_part, datetime.timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{milliseconds_part:03d}Z"


def datetime_to_unix(timestamp_or_datetime_str):
    if timestamp_or_datetime_str and is_int(timestamp_or_datetime_str):
        return int(timestamp_or_datetime_str)
    else:
        return to_unix(timestamp_or_datetime_str)


def unix_to_rfc3339(timestamp: int) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}Z"


def datetime_to_rfc3339(timestamp):
    if isinstance(timestamp, int):
        return unix_to_rfc3339(timestamp)
    else:
        return timestamp


def process_timestamps_to_rfc3339(
    start_timestamp: Optional[Union[int, str]],
    end_timestamp: Optional[Union[int, str]],
    default_time_span_seconds: int,
) -> Tuple[str, str]:
    (start_timestamp, end_timestamp) = process_timestamps_to_int(
        start_timestamp,
        end_timestamp,
        default_time_span_seconds=default_time_span_seconds,
    )
    parsed_start_timestamp = datetime_to_rfc3339(start_timestamp)
    parsed_end_timestamp = datetime_to_rfc3339(end_timestamp)
    return (parsed_start_timestamp, parsed_end_timestamp)


def process_timestamps_to_int(
    start: Optional[Union[int, str]],
    end: Optional[Union[int, str]],
    default_time_span_seconds: int,
) -> Tuple[int, int]:
    """
    Process and normalize start and end timestamps.

    Supports:
    - Integer timestamps (Unix time)
    - RFC3339 formatted timestamps
    - Negative integers as relative time from the other timestamp
    - Auto-inversion if start is after end

    Returns:
    Tuple of (start_timestamp, end_timestamp)
    """
    # If no end_timestamp provided, use current time
    if not end or end == "0" or end == 0:
        end = int(time.time())

    # If no start provided, default to one hour before end
    if not start:
        start = -1 * abs(default_time_span_seconds)

    start = datetime_to_unix(start)
    end = datetime_to_unix(end)

    # Handle negative timestamps (relative to the other timestamp)
    if isinstance(start, int) and isinstance(end, int):
        if start < 0 and end < 0:
            # end is relative to now()
            end = int(time.time()) + end
            start = end + start
        elif start < 0:
            start = end + start
        elif end < 0:
            # start/end are inverted. end should be after start_timestamp
            delta = end
            end = start
            start = start + delta

    # Invert timestamps if start is after end
    if isinstance(start, int) and isinstance(end, int) and start > end:
        start, end = end, start

    return (start, end)  # type: ignore


def seconds_to_duration_string(seconds: int) -> str:
    """Convert seconds into a compact duration string like '2h30m15s'.
    If the value is less than 1 minute, return just the number of seconds (e.g. '45').
    """
    if seconds < 0:
        raise ValueError("seconds must be non-negative")

    parts = []
    weeks, seconds = divmod(seconds, 7 * 24 * 3600)
    days, seconds = divmod(seconds, 24 * 3600)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return "".join(parts)


def duration_string_to_seconds(duration_string: str) -> int:
    """Convert a duration string like '2h30m15s' or '300' into total seconds.
    A bare integer string is treated as seconds.
    """
    if not duration_string:
        raise ValueError("duration_string cannot be empty")

    # Pure number? Assume seconds
    if duration_string.isdigit():
        return int(duration_string)

    pattern = re.compile(r"(?P<value>\d+)(?P<unit>[wdhms])")
    matches = pattern.findall(duration_string)
    if not matches:
        raise ValueError(f"Invalid duration string: {duration_string}")

    unit_multipliers = {
        "w": 7 * 24 * 3600,
        "d": 24 * 3600,
        "h": 3600,
        "m": 60,
        "s": 1,
    }

    total_seconds = 0
    for value, unit in matches:
        if unit not in unit_multipliers:
            raise ValueError(f"Unknown unit: {unit}")
        total_seconds += int(value) * unit_multipliers[unit]

    return total_seconds


def adjust_step_for_max_points(
    time_range_seconds: int,
    max_points: int,
    step: Optional[int] = None,
) -> int:
    """
    Adjusts the step parameter to ensure the number of data points doesn't exceed max_points.

    Args:
        time_range_seconds: time range in seconds
        step: The requested step duration in seconds
        max_points: The requested maximum number of data points

    Returns:
        Adjusted step value in seconds that ensures points <= max_points
    """
    smallest_allowed_step = int(
        math.ceil(float(time_range_seconds) / float(max_points))
    )

    if not step:
        return smallest_allowed_step

    return max(smallest_allowed_step, step)


def get_param_or_raise(dict: Dict, param: str) -> str:
    value = dict.get(param)
    if not value:
        raise Exception(f'Missing param "{param}"')
    return value


def toolset_name_for_one_liner(toolset_name: str) -> str:
    name = toolset_name
    if "/" in toolset_name:
        name = toolset_name.split("/")[0]
    return name.capitalize()
