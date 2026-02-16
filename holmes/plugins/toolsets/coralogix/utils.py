import json
import logging
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """Coralogix toolset configuration.

    Required:
        domain: Coralogix region domain (e.g., "eu2.coralogix.com")
        api_key: API key with DataQuerying permissions

    Optional:
        team_slug: Your team's URL slug (e.g., "my-team" from https://my-team.eu2.coralogix.com).
                   Only needed to generate clickable UI permalink URLs in tool output.
        labels: Label mappings for log fields (for Kubernetes log extraction)
    """

    model_config = ConfigDict(extra="allow")
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
    team_slug: Optional[str] = Field(
        default=None,
        description="Your team's URL slug for generating UI permalinks",
        examples=["my-team"],
    )
    labels: CoralogixLabelsConfig = Field(
        default_factory=CoralogixLabelsConfig,
        title="Labels",
        description="Label mappings for log fields",
    )

    @model_validator(mode="after")
    def handle_deprecated_fields(self):
        """Handle backwards compatibility for renamed fields."""
        extra = self.model_extra or {}
        deprecated = []

        # team_hostname was renamed to team_slug
        if "team_hostname" in extra and not self.team_slug:
            self.team_slug = extra["team_hostname"]
            deprecated.append("team_hostname -> team_slug")

        if deprecated:
            logging.warning(f"Coralogix: deprecated config field names: {', '.join(deprecated)}")
        return self


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
