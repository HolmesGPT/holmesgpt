import json
import logging
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

from pydantic import BaseModel, Field

from holmes.utils.pydantic_utils import ToolsetConfig


class FlattenedLog(NamedTuple):
    timestamp: str
    log_message: str


class CoralogixQueryResult(BaseModel):
    logs: List[FlattenedLog]
    http_status: Optional[int]
    error: Optional[str]


class CoralogixLabelsConfig(ToolsetConfig):
    pod: str = Field(
        default="resource.attributes.k8s.pod.name",
        title="Pod Field",
        description="Field path for pod name in log entries",
    )
    namespace: str = Field(
        default="resource.attributes.k8s.namespace.name",
        title="Namespace Field",
        description="Field path for namespace in log entries",
    )
    log_message: str = Field(
        default="logRecord.body",
        title="Log Message Field",
        description="Field path for log message content",
    )
    timestamp: str = Field(
        default="logRecord.attributes.time",
        title="Timestamp Field",
        description="Field path for timestamp in log entries",
    )


class CoralogixConfig(ToolsetConfig):
    team_hostname: str = Field(
        title="Team Hostname",
        description="Your Coralogix team hostname",
        examples=["my-team"],
    )
    domain: str = Field(
        title="Domain",
        description="Coralogix domain",
        examples=["eu2.coralogix.com", "coralogix.us", "coralogix.in"],
    )
    api_key: str = Field(
        title="API Key",
        description="Coralogix API key (starts with cxuw_)",
        examples=["cxuw_xxxxxxxxxxxx"],
    )
    labels: CoralogixLabelsConfig = Field(
        default_factory=CoralogixLabelsConfig,
        title="Labels",
        description="Label mappings for log fields",
    )


def parse_json_lines(raw_text) -> List[Dict[str, Any]]:
    """Parses JSON objects from a raw text response and removes duplicate userData fields from child objects."""
    json_objects = []
    for line in raw_text.strip().split("\n"):  # Split by newlines
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                # Remove userData from top level
                obj.pop("userData", None)
                # Remove userData from direct child dicts (one level deep, no recursion)
                for key, value in list(obj.items()):
                    if isinstance(value, dict):
                        value.pop("userData", None)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                item.pop("userData", None)
            json_objects.append(obj)
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON from line: {line}")
    return json_objects


def normalize_datetime(date_str: Optional[str]) -> str:
    if not date_str:
        return "UNKNOWN_TIMESTAMP"

    try:
        date_str_no_z = date_str.rstrip("Z")

        parts = date_str_no_z.split(".")
        if len(parts) > 1 and len(parts[1]) > 6:
            date_str_no_z = f"{parts[0]}.{parts[1][:6]}"

        date = datetime.fromisoformat(date_str_no_z)

        normalized_date_time = date.strftime("%Y-%m-%dT%H:%M:%S.%f")
        return normalized_date_time + "Z"
    except Exception:
        return date_str


def extract_field(data_obj: dict[str, Any], field: str):
    """returns a nested field from a dict
    e.g. extract_field({"parent": {"child": "value"}}, "parent.child") => value
    """
    current_object: Any = data_obj
    fields = field.split(".")

    for field in fields:
        if not current_object:
            return None
        if isinstance(current_object, dict):
            current_object = current_object.get(field)
        else:
            return None

    return current_object


def flatten_structured_log_entries(
    log_entries: List[Dict[str, Any]],
    labels_config: CoralogixLabelsConfig,
) -> List[FlattenedLog]:
    flattened_logs = []
    for log_entry in log_entries:
        try:
            userData = json.loads(log_entry.get("userData", "{}"))
            log_message = extract_field(userData, labels_config.log_message)
            timestamp = extract_field(userData, labels_config.timestamp)
            if not log_message or not timestamp:
                log_message = json.dumps(userData)
            else:
                flattened_logs.append(
                    FlattenedLog(timestamp=timestamp, log_message=log_message)
                )  # Store as tuple for sorting

        except json.JSONDecodeError:
            logging.error(f"Failed to decode userData JSON: {json.dumps(log_entry)}")
    return flattened_logs


def stringify_flattened_logs(log_entries: List[FlattenedLog]) -> str:
    formatted_logs = []
    for entry in log_entries:
        formatted_logs.append(entry.log_message)

    return "\n".join(formatted_logs) if formatted_logs else "No logs found."


def parse_json_objects(
    json_objects: List[Dict[str, Any]], labels_config: CoralogixLabelsConfig
) -> List[FlattenedLog]:
    """Extracts timestamp and log values from parsed JSON objects, sorted in ascending order (oldest first)."""
    logs: List[FlattenedLog] = []

    for data in json_objects:
        if isinstance(data, dict) and "result" in data and "results" in data["result"]:
            logs += flatten_structured_log_entries(
                log_entries=data["result"]["results"], labels_config=labels_config
            )
        elif isinstance(data, dict) and data.get("warning"):
            logging.info(
                f"Received the following warning when fetching coralogix logs: {data}"
            )
        else:
            logging.debug(f"Unrecognised partial response from coralogix logs: {data}")

    logs.sort(key=lambda x: x[0])

    return logs
