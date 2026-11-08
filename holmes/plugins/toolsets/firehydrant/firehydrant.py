"""FireHydrant AI investigations toolset.

Exposes read tools over FireHydrant's investigations and incidents API, and
holds the configuration and HTTP client that the server-side poller
(``holmes.plugins.toolsets.firehydrant.poller``) uses to claim pending
investigations, run them through Holmes and post the result back.
"""

import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast
from urllib.parse import urljoin

import requests  # type: ignore
from pydantic import Field
from strenum import StrEnum

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

FIREHYDRANT_TOOLSET_NAME = "firehydrant/investigations"
DEFAULT_FIREHYDRANT_API_URL = "https://api.firehydrant.io"
FIREHYDRANT_ICON_URL = "https://www.google.com/s2/favicons?sz=64&domain=firehydrant.com"

# Incident fields worth showing to the LLM. The raw IncidentEntity carries
# dozens of nested objects (role assignments, status pages, conference
# bridges, retro exports...) that bloat the prompt without helping the
# investigation.
INCIDENT_SCALAR_FIELDS = (
    "id",
    "number",
    "name",
    "severity",
    "priority",
    "current_milestone",
    "started_at",
    "created_at",
    "active",
    "summary",
    "description",
    "customer_impact_summary",
    "incident_url",
    "last_update",
    "customers_impacted",
    "tag_list",
    "labels",
)
INCIDENT_NAMED_LIST_FIELDS = ("services", "environments", "functionalities")


class FireHydrantInvestigationStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELED = "canceled"


class FireHydrantConfig(ToolsetConfig):
    """Configuration for the FireHydrant investigations toolset.

    Example configuration:
    ```yaml
    api_key: "fh-..."
    poll_interval_seconds: 30
    ```
    """

    _name: ClassVar[Optional[str]] = "FireHydrant"
    _description: ClassVar[Optional[str]] = (
        "Claim pending FireHydrant AI investigations, run them in Holmes and post the results back"
    )
    _icon_url: ClassVar[Optional[str]] = FIREHYDRANT_ICON_URL

    api_url: str = Field(
        default=DEFAULT_FIREHYDRANT_API_URL,
        title="API URL",
        description="FireHydrant API base URL",
        examples=[DEFAULT_FIREHYDRANT_API_URL, "https://api.staging.firehydrant.io"],
    )
    api_key: str = Field(
        title="API Key",
        description="FireHydrant API key (bot token) with access to investigations and incidents",
        examples=["fh-..."],
    )
    poll_investigations: bool = Field(
        default=True,
        title="Poll for investigations",
        description=(
            "When true, the Holmes server polls FireHydrant for pending AI investigations, "
            "claims them, investigates and posts the result back. When false only the read tools are available."
        ),
    )
    poll_interval_seconds: int = Field(
        default=30,
        ge=5,
        title="Poll interval (seconds)",
        description="How often to ask FireHydrant for pending investigations",
        examples=[30, 60],
    )
    request_timeout_seconds: int = Field(
        default=30,
        ge=1,
        title="Request timeout (seconds)",
        description="Timeout for each HTTP request to the FireHydrant API",
    )
    model: Optional[str] = Field(
        default=None,
        title="Model",
        description="Holmes model used to run FireHydrant investigations. Defaults to the server's default model.",
        examples=["gpt-4.1"],
    )
    additional_system_prompt: Optional[str] = Field(
        default=None,
        title="Additional system prompt",
        description="Extra instructions appended to the system prompt of every FireHydrant investigation",
    )


class FireHydrantApiError(Exception):
    """An HTTP error from the FireHydrant API, carrying the status code and body for self-correction."""

    def __init__(self, method: str, url: str, status_code: int, body: str):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body
        super().__init__(f"FireHydrant API {method} {url} failed with status {status_code}: {body}")


class FireHydrantClient:
    """Thin wrapper over the FireHydrant investigations and incidents endpoints."""

    def __init__(self, api_url: str, api_key: str, timeout_seconds: int = 30):
        self.api_url = api_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _url(self, path: str) -> str:
        return urljoin(self.api_url, path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._url(path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        # Drop unset query params so the URL only carries the filters that were asked for.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        response = requests.request(
            method,
            url,
            headers=headers,
            params=clean_params or None,
            json=json,
            timeout=self.timeout_seconds,
        )
        if not response.ok:
            raise FireHydrantApiError(method, response.url, response.status_code, response.text)
        if not response.content:
            return {}
        return response.json()

    def list_investigations(
        self,
        status: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/v1/investigations",
            params={"status": status, "page": page, "per_page": per_page},
        )

    def get_investigation(self, investigation_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/investigations/{investigation_id}")

    def claim_investigation(self, investigation_id: str, external_id: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/investigations/{investigation_id}/claim",
            json={"external_id": external_id},
        )

    def complete_investigation(
        self,
        investigation_id: str,
        status: str,
        summary: Optional[str],
        markdown: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"status": status}
        if summary:
            body["summary"] = summary
        if markdown:
            body["result"] = {"markdown": markdown}
        if error:
            body["error"] = error
        return self._request("POST", f"/v1/investigations/{investigation_id}/complete", json=body)

    def get_incident(self, incident_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/incidents/{incident_id}")


def _names(items: Any) -> List[str]:
    """Names of FireHydrant entities such as services or environments."""
    if not isinstance(items, list):
        return []
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("slug") or item.get("id")
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    return names


def compact_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a FireHydrant IncidentEntity to the fields that matter for an investigation."""
    compact: Dict[str, Any] = {}
    for field in INCIDENT_SCALAR_FIELDS:
        value = incident.get(field)
        if value not in (None, "", [], {}):
            compact[field] = value
    for field in INCIDENT_NAMED_LIST_FIELDS:
        names = _names(incident.get(field))
        if names:
            compact[field] = names
    incident_type = incident.get("incident_type")
    if isinstance(incident_type, dict) and incident_type.get("name"):
        compact["incident_type"] = incident_type["name"]
    impacts = incident.get("impacts")
    if isinstance(impacts, list) and impacts:
        compact["impacts"] = [
            {
                "type": impact.get("type"),
                "name": (impact.get("impact") or {}).get("name"),
                "condition": (impact.get("condition") or {}).get("name"),
            }
            for impact in impacts
            if isinstance(impact, dict)
        ]
    phases = incident.get("lifecycle_phases")
    if isinstance(phases, list) and phases:
        milestones = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            for milestone in phase.get("milestones") or []:
                if isinstance(milestone, dict) and milestone.get("occurred_at"):
                    milestones.append(
                        {
                            "milestone": milestone.get("slug") or milestone.get("name"),
                            "occurred_at": milestone["occurred_at"],
                        }
                    )
        if milestones:
            compact["milestones"] = milestones
    last_note = incident.get("last_note")
    if isinstance(last_note, dict) and last_note.get("body"):
        compact["last_note"] = last_note["body"]
    return compact


class FireHydrantToolset(Toolset):
    config_classes: ClassVar[List[Type[FireHydrantConfig]]] = [FireHydrantConfig]

    def __init__(self):
        super().__init__(
            name=FIREHYDRANT_TOOLSET_NAME,
            description=(
                "Read FireHydrant AI investigations and incidents. When polling is enabled, the Holmes "
                "server claims pending FireHydrant investigations, investigates them and posts the results back."
            ),
            icon_url=FIREHYDRANT_ICON_URL,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/firehydrant/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListInvestigations(self),
                GetInvestigation(self),
                GetIncident(self),
            ],
            tags=[ToolsetTag.CORE],
        )
        self._load_llm_instructions_from_file(os.path.dirname(__file__), "instructions.jinja2")

    @property
    def firehydrant_config(self) -> FireHydrantConfig:
        return cast(FireHydrantConfig, self.config)

    @property
    def client(self) -> FireHydrantClient:
        config = self.firehydrant_config
        return FireHydrantClient(
            api_url=config.api_url,
            api_key=config.api_key,
            timeout_seconds=config.request_timeout_seconds,
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = FireHydrantConfig(**config)
        except Exception as e:
            return False, f"Failed to validate FireHydrant configuration: {e}"

        api_url = self.firehydrant_config.api_url
        try:
            self.client.list_investigations(status=FireHydrantInvestigationStatus.PENDING, per_page=1)
        except FireHydrantApiError as e:
            if e.status_code in (401, 403):
                return (
                    False,
                    f"FireHydrant rejected the API key ({e.status_code}). Check that the key is valid and may read investigations. Full error: {e.body}",
                )
            return False, f"FireHydrant API returned an error: {e.status_code} - {e.body}"
        except requests.exceptions.Timeout:
            return False, f"FireHydrant health check timed out connecting to {api_url}"
        except requests.exceptions.RequestException as e:
            return False, f"Failed to connect to FireHydrant at {api_url}: {e}"
        except Exception as e:
            return False, f"FireHydrant health check failed: {e}"

        mode = (
            f"polling every {self.firehydrant_config.poll_interval_seconds}s for pending investigations"
            if self.firehydrant_config.poll_investigations
            else "polling disabled, read tools only"
        )
        logging.info(f"FireHydrant toolset ready: {api_url} ({mode})")
        return True, ""


class BaseFireHydrantTool(Tool, ABC):
    def __init__(self, toolset: FireHydrantToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _call(self, params: dict, call) -> StructuredToolResult:
        """Run an API call and turn its outcome into a tool result with the full error on failure."""
        try:
            data = call()
        except FireHydrantApiError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                return_code=e.status_code,
                params=params,
            )
        except requests.exceptions.RequestException as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request to FireHydrant ({self._toolset.firehydrant_config.api_url}) failed: {e}",
                params=params,
            )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
        )


class ListInvestigations(BaseFireHydrantTool):
    def __init__(self, toolset: FireHydrantToolset):
        super().__init__(
            toolset=toolset,
            name="firehydrant_list_investigations",
            description=(
                "List the organization's FireHydrant AI investigations, oldest first, using GET /v1/investigations. "
                "Each investigation references the incident it is about and, once settled, carries the result."
            ),
            parameters={
                "status": ToolParameter(
                    description="Only return investigations in this status",
                    type="string",
                    required=False,
                    enum=[s.value for s in FireHydrantInvestigationStatus],
                ),
                "page": ToolParameter(
                    description="Page number, starting at 1",
                    type="integer",
                    required=False,
                ),
                "per_page": ToolParameter(
                    description="Investigations per page (default: 20, max: 100)",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        per_page = min(int(params.get("per_page") or 20), 100)
        return self._call(
            params,
            lambda: self._toolset.client.list_investigations(
                status=params.get("status"),
                page=params.get("page"),
                per_page=per_page,
            ),
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        status = params.get("status") or "any status"
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List investigations ({status})"


class GetInvestigation(BaseFireHydrantTool):
    def __init__(self, toolset: FireHydrantToolset):
        super().__init__(
            toolset=toolset,
            name="firehydrant_get_investigation",
            description="Retrieve a single FireHydrant AI investigation by id using GET /v1/investigations/{investigation_id}",
            parameters={
                "investigation_id": ToolParameter(
                    description="The investigation id, as returned by firehydrant_list_investigations. Never guess it.",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._call(
            params,
            lambda: self._toolset.client.get_investigation(params["investigation_id"]),
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get investigation {params.get('investigation_id')}"


class GetIncident(BaseFireHydrantTool):
    def __init__(self, toolset: FireHydrantToolset):
        super().__init__(
            toolset=toolset,
            name="firehydrant_get_incident",
            description=(
                "Retrieve the key details of a FireHydrant incident (name, severity, milestones, impacted services, "
                "description, latest update) using GET /v1/incidents/{incident_id}"
            ),
            parameters={
                "incident_id": ToolParameter(
                    description="The incident UUID, as found in an investigation's `incident.id`. Never guess it.",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._call(
            params,
            lambda: compact_incident(self._toolset.client.get_incident(params["incident_id"])),
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get incident {params.get('incident_id')}"
