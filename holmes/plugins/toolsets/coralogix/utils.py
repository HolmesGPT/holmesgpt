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


# Official mapping of Coralogix account domains to the "Team Hostname" suffix used
# by the web UI, per https://coralogix.com/docs/user-guides/account-management/account-settings/coralogix-domain/
# The UI permalink hostname is f"{team_slug}.{suffix}" and differs from the API
# domain in most regions: e.g. the US2 API domain is us2.coralogix.com (legacy:
# cx498.coralogix.com) but the US2 UI lives at <team>.app.cx498.coralogix.com.
# Both the current regional domains (us2.coralogix.com) and the legacy ones
# (cx498.coralogix.com) are accepted as keys since either works for API calls.
CORALOGIX_TEAM_HOSTNAME_SUFFIXES: Dict[str, str] = {
    # US1 - AWS us-east-2 (Ohio)
    "us1.coralogix.com": "app.coralogix.us",
    "coralogix.us": "app.coralogix.us",
    # US2 - AWS us-west-2 (Oregon)
    "us2.coralogix.com": "app.cx498.coralogix.com",
    "cx498.coralogix.com": "app.cx498.coralogix.com",
    # US3 - GCP us-central1 (Iowa)
    "us3.coralogix.com": "app.us3.coralogix.com",
    # EU1 - AWS eu-west-1 (Ireland); the only region without an 'app.' prefix
    "eu1.coralogix.com": "coralogix.com",
    "coralogix.com": "coralogix.com",
    # EU2 - AWS eu-north-1 (Stockholm)
    "eu2.coralogix.com": "app.eu2.coralogix.com",
    # AP1 - AWS ap-south-1 (Mumbai)
    "ap1.coralogix.com": "app.coralogix.in",
    "coralogix.in": "app.coralogix.in",
    # AP2 - AWS ap-southeast-1 (Singapore)
    "ap2.coralogix.com": "app.coralogixsg.com",
    "coralogixsg.com": "app.coralogixsg.com",
    # AP3 - AWS ap-southeast-3 (Jakarta)
    "ap3.coralogix.com": "app.ap3.coralogix.com",
    # GOV1 - AWS GovCloud us-gov-west-1 (FedRAMP)
    "gov1.coralogixgov.us": "app.gov1.coralogixgov.us",
}


class CoralogixConfig(ToolsetConfig):
    """Coralogix toolset configuration.

    Required:
        domain: Coralogix region domain (e.g., "eu2.coralogix.com")
        api_key: API key with DataQuerying permissions

    Optional:
        team_slug: Your team's URL slug (e.g., "my-team" from https://my-team.app.eu2.coralogix.com).
                   Only needed to generate clickable UI permalink URLs in tool output.
        ui_url: Full base URL of your team's Coralogix UI. Only needed when the
                auto-derived UI hostname is wrong (e.g. custom deployments).
        labels: Label mappings for log fields (for Kubernetes log extraction)
    """

    model_config = ConfigDict(extra="allow")
    domain: str = Field(
        title="Domain",
        description="Coralogix domain",
        examples=["eu2.coralogix.com", "us2.coralogix.com", "coralogix.us"],
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
    ui_url: Optional[str] = Field(
        default=None,
        title="UI URL",
        description="Base URL of your team's Coralogix UI, used for generating UI permalinks. "
        "Overrides the hostname otherwise derived from 'team_slug' and 'domain'.",
        examples=["https://my-team.app.cx498.coralogix.com"],
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
        if "team_hostname" in extra:
            if not self.team_slug:
                self.team_slug = extra["team_hostname"]
            extra.pop("team_hostname")
            deprecated.append("team_hostname -> team_slug")

        if deprecated:
            logging.warning(
                f"Coralogix: deprecated config field names: {', '.join(deprecated)}"
            )
        return self


def get_ui_base_url(config: CoralogixConfig) -> Optional[str]:
    """Return the base URL of the Coralogix team web UI, or None if unknown.

    The UI ("Team Hostname") differs from the API domain in most Coralogix
    regions, so the configured API domain cannot be reused verbatim (ROB-1395).

    Resolution order:
    1. `ui_url`, when configured (explicit override).
    2. `team_slug` + the official team hostname suffix for the configured domain.
    3. `team_slug` + "app." + domain for unrecognized Coralogix domains: every
       region added since the regional naming scheme (us3, eu2, ap3) serves its
       team UI at app.<domain>, so assume future regions follow the same
       convention; the pre-scheme exceptions are pinned in the map above.
    4. `team_slug` + the domain as-is, for non-Coralogix (custom) domains.
    """
    if config.ui_url:
        ui_url = config.ui_url.strip().rstrip("/")
        if not ui_url.lower().startswith(("https://", "http://")):
            ui_url = f"https://{ui_url}"
        return ui_url

    if not config.team_slug:
        return None

    domain = config.domain.strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://")
    domain = domain.strip("/").rstrip(".")
    team_hostname_suffix = CORALOGIX_TEAM_HOSTNAME_SUFFIXES.get(domain)
    if team_hostname_suffix is None:
        # only subdomains of Coralogix-owned apexes get the app. inference;
        # anything else (custom/proxied domains) is used as-is
        is_coralogix_regional = domain.endswith(
            (".coralogix.com", ".coralogixgov.us")
        ) and not domain.startswith("app.")
        team_hostname_suffix = f"app.{domain}" if is_coralogix_regional else domain
    return f"https://{config.team_slug}.{team_hostname_suffix}"


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
